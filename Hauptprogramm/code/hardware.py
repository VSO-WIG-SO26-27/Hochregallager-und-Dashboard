"""
Hardware-Schicht: Zugriff auf Motoren, Ausgänge, Zähler und Eingänge eines TXT.

Es gibt zwei austauschbare Varianten mit identischer Schnittstelle:

* TxtHardware  – echter TXT-Controller über WLAN mit ftrobopy
* SimHardware  – Simulation am Mac ohne Anlage (zum Testen des Ablaufs
                 und des Dashboards), mit Profil "hbw" oder "vgr"

Richtungskonvention wie im C++-Code (TxtAxis.cpp):
  Geschwindigkeit > 0  ->  "links"  = Richtung Referenztaster / Ausleger einfahren
                           (C++: duty[chM*2] = speed)
  Geschwindigkeit < 0  ->  "rechts" = vom Referenztaster weg / Ausleger ausfahren
                           (C++: duty[chM*2+1] = speed)
ftrobopy verwendet genau dieselbe Zuordnung (setSpeed(+v) -> PWM-Kanal 2n),
deshalb kann die Logik 1:1 übernommen werden.
"""

import threading
import time

from . import konfiguration as cfg


class HardwareFehler(Exception):
    pass


# ===========================================================================
# Echter TXT über ftrobopy
# ===========================================================================
class TxtHardware:
    modus = "TXT"

    def __init__(self, ip=cfg.TXT_IP, port=cfg.TXT_PORT, motoren=(1, 2, 3, 4), ausgaenge=(), spannung=()):
        """motoren:   Motorausgänge M1..M4, die als Motor benutzt werden
        ausgaenge: Einzelausgänge O1..O8 (z. B. Kompressor/Ventil)
        spannung:  Eingänge, die analog als Spannung (mV) gelesen werden (z. B. Farbsensor)
        Alle übrigen Eingänge I1..I8 werden als digitale Taster eingerichtet."""
        try:
            import ftrobopy
        except ImportError as e:
            raise HardwareFehler("ftrobopy ist nicht installiert: pip3 install ftrobopy") from e

        self.ip = ip
        try:
            self.txt = ftrobopy.ftrobopy(ip, port)
        except Exception as e:  # ftrobopy wirft je nach Fehler verschiedene Typen
            raise HardwareFehler(
                f"Keine Verbindung zum TXT unter {ip}:{port}. "
                "Ist der Mac im Lernfabrik-WLAN und läuft auf diesem TXT kein anderes Programm?"
            ) from e
        if not self.txt.isOnline():
            raise HardwareFehler(f"TXT unter {ip}:{port} antwortet nicht (Online-Modus nicht gestartet).")

        self._motoren = {nr: self.txt.motor(nr) for nr in motoren}
        self._ausgaenge = {nr: self.txt.output(nr) for nr in ausgaenge}
        self._spannung = {nr: self.txt.voltage(nr) for nr in spannung}
        # Digitale Taster (C++: MODE_R, digital = 1)
        self._eingaenge = {nr: self.txt.input(nr) for nr in range(1, 9) if nr not in self._spannung}
        self.alles_aus()

    # --- Motoren -----------------------------------------------------------
    def motor(self, nr, geschwindigkeit):
        """Motor mit Geschwindigkeit -512..512 laufen lassen (0 = aus)."""
        self._motoren[nr].setSpeed(int(geschwindigkeit))

    def motor_strecke(self, nr, schritte):
        """Streckenbefehl: Motor hält nach 'schritte' Encoder-Impulsen selbst an.
        schritte = 0 bedeutet: keine Begrenzung (wie moveRef im C++-Code).
        Entspricht ftX1out.distance[] + motor_ex_cmd_id++ im C++-Code."""
        self._motoren[nr].setDistance(int(schritte))

    def motor_fertig(self, nr):
        """True, wenn der TXT den letzten Streckenbefehl abgeschlossen meldet
        (C++: ftX1in.motor_ex_cmd_id == ftX1out.motor_ex_cmd_id)."""
        return self._motoren[nr].finished()

    # --- Einzelausgänge ----------------------------------------------------
    def ausgang(self, nr, wert):
        """Ausgang O<nr> setzen, 0..512 (C++: duty[ch] = 512)."""
        self._ausgaenge[nr].setLevel(int(wert))

    # --- Zähler ------------------------------------------------------------
    def zaehler(self, nr):
        return int(self.txt.getCurrentCounterValue(nr - 1))

    def zaehler_reset(self, nr, timeout=1.0):
        """Zähler C<nr> auf 0 setzen und warten, bis der TXT es bestätigt
        (C++: cnt_reset_cmd_id++)."""
        self.txt.incrCounterCmdId(nr - 1)
        start = time.time()
        while self.txt.getCurrentCounterCmdId(nr - 1) != self.txt.getCounterCmdId(nr - 1):
            if time.time() - start > timeout:
                raise HardwareFehler(f"Zähler C{nr} wurde vom TXT nicht zurückgesetzt")
            time.sleep(0.005)

    def zaehler_eingang(self, nr):
        """Digitaler Zustand des Zählereingangs C<nr> (C++: ftX1in.cnt_in[]).
        Wird z. B. für die Lichtschranke an der Ausgabe (C4) benutzt."""
        return int(self.txt.getCurrentCounterInput(nr - 1))

    # --- Eingänge ----------------------------------------------------------
    def eingang(self, nr):
        """Digitaler Eingang I<nr>: 1 = Taster gedrückt / Licht kommt an (C++: uni[] == 1)."""
        if nr in self._spannung:
            return int(self._spannung[nr].voltage() > 600)
        return int(self._eingaenge[nr].state())

    def spannung(self, nr):
        """Analoger Eingang in mV (C++: MODE_U, uni[]) – z. B. Farbsensor."""
        return int(self._spannung[nr].voltage())

    # --- Sonstiges ---------------------------------------------------------
    def alles_aus(self):
        """Alle Motoren aus. Ausgänge (Kompressor/Ventil) bleiben – sonst fällt
        ein angesaugtes Werkstück herunter. Die Ablaufsteuerung schaltet sie selbst."""
        for m in self._motoren.values():
            m.setSpeed(0)

    def trennen(self):
        try:
            self.alles_aus()
            for a in self._ausgaenge.values():
                a.setLevel(0)
            time.sleep(0.2)   # damit der Stopp-Befehl noch übertragen wird
            self.txt.stopOnline()
        except Exception:
            pass


# ===========================================================================
# Simulation
# ===========================================================================
SIM_PROFILE = {
    # Encoder-Achsen: Motor -> (Referenztaster, Endlage, Startposition)
    # Ausleger:       Motor -> (Taster eingefahren, Taster ausgefahren)
    "hbw": {
        "achsen": {cfg.MOTOR_X: (cfg.REF_X, cfg.ENDE_X, 900), cfg.MOTOR_Y: (cfg.REF_Y, cfg.ENDE_Y, 400)},
        "ausleger": {cfg.MOTOR_Z: (cfg.TASTER_Z_EINGEFAHREN, cfg.TASTER_Z_AUSGEFAHREN)},
        "eingaenge": {},
        "zaehler_eingaenge": {},
    },
    "vgr": {
        "achsen": {cfg.VGR_MOTOR_X: (cfg.VGR_REF_X, cfg.VGR_ENDE_X, 300),
                   cfg.VGR_MOTOR_Y: (cfg.VGR_REF_Y, cfg.VGR_ENDE_Y, 200),
                   cfg.VGR_MOTOR_Z: (cfg.VGR_REF_Z, cfg.VGR_ENDE_Z, 150)},
        "ausleger": {},
        # Lichtschranke Eingabe: 1 = frei (Licht kommt an), 0 = Werkstück liegt drin
        "eingaenge": {cfg.DPS_LICHTSCHRANKE_EINGABE: 1},
        # Lichtschranke Ausgabe (C4): 1 = frei
        "zaehler_eingaenge": {cfg.DPS_LICHTSCHRANKE_AUSGABE: 1},
    },
}

SIM_FARBWERTE = {"WHITE": 600, "RED": 1200, "BLUE": 1800, "NONE": 100}


class SimHardware:
    """Einfaches Physikmodell. Encoder-Achsen: Referenztaster bei Position <= 0.
    Ausleger: 0.0 (eingefahren) .. 1.0 (ausgefahren)."""
    modus = "Simulation"
    IMPULSE_PRO_S = 450      # Encoder-Impulse pro Sekunde bei voller Geschwindigkeit
    AUSLEGER_ZEIT_S = 1.2    # Zeit für einmal komplett aus-/einfahren

    def __init__(self, zeitfaktor=1.0, profil="hbw"):
        p = SIM_PROFILE[profil]
        self.profil = profil
        self.zf = zeitfaktor
        self._lock = threading.Lock()
        self._achsen = dict(p["achsen"])
        self._ausleger = dict(p["ausleger"])
        self.pos = {m: float(a[2]) for m, a in self._achsen.items()}
        self.ausleger_pos = {m: 0.6 for m in self._ausleger}
        self._v = {m: 0 for m in (1, 2, 3, 4)}
        self._zaehler = {m: 0.0 for m in (1, 2, 3, 4)}
        self._strecke = {m: 0 for m in (1, 2, 3, 4)}
        self._fertig = {m: True for m in (1, 2, 3, 4)}
        self._ausgaenge = {n: 0 for n in range(1, 9)}
        self.sim_eingaenge = dict(p["eingaenge"])
        self.sim_zaehler_eingaenge = dict(p["zaehler_eingaenge"])
        self.sim_farbe = "NONE"          # Werkstück, das gerade unter dem Farbsensor liegt
        self._stop = threading.Event()
        threading.Thread(target=self._physik, daemon=True).start()

    # --- Physik ------------------------------------------------------------
    def _physik(self):
        dt = 0.005
        while not self._stop.is_set():
            time.sleep(dt)
            with self._lock:
                for m, (ref, ende, _) in self._achsen.items():
                    v = self._v[m]
                    if v == 0:
                        continue
                    d = abs(v) / 512 * self.IMPULSE_PRO_S * dt * self.zf
                    neu = self.pos[m] - d if v > 0 else self.pos[m] + d
                    self.pos[m] = max(-5.0, min(ende + 60, neu))
                    self._zaehler[m] += d
                    if self._strecke[m] > 0 and self._zaehler[m] >= self._strecke[m]:
                        self._v[m] = 0
                        self._fertig[m] = True
                for m in self._ausleger:
                    v = self._v[m]
                    if v:
                        d = abs(v) / 512 * dt / self.AUSLEGER_ZEIT_S * self.zf
                        z = self.ausleger_pos[m]
                        self.ausleger_pos[m] = max(0.0, min(1.0, z - d if v > 0 else z + d))

    # --- Motoren -----------------------------------------------------------
    def motor(self, nr, geschwindigkeit):
        with self._lock:
            self._v[nr] = int(geschwindigkeit)

    def motor_strecke(self, nr, schritte):
        with self._lock:
            self._strecke[nr] = int(schritte)
            self._zaehler[nr] = 0.0          # TXT startet die Zählung neu
            self._fertig[nr] = schritte <= 0

    def motor_fertig(self, nr):
        with self._lock:
            return self._fertig[nr]

    # --- Ausgänge ----------------------------------------------------------
    def ausgang(self, nr, wert):
        with self._lock:
            alt = self._ausgaenge[nr]
            self._ausgaenge[nr] = int(wert)
            # Greifer-Simulation: Ventil öffnet an der Eingabe -> Werkstück wird
            # aus der Eingabe genommen (Lichtschranke wieder frei)
            if (self.profil == "vgr" and nr == cfg.VGR_VENTIL and wert and not alt
                    and self.sim_eingaenge.get(cfg.DPS_LICHTSCHRANKE_EINGABE) == 0
                    and abs(self.pos[cfg.VGR_MOTOR_X] - cfg.VGR_STANDARD_POSITIONEN["DIN"][0]) < 60):
                self.sim_eingaenge[cfg.DPS_LICHTSCHRANKE_EINGABE] = 1

    # --- Zähler ------------------------------------------------------------
    def zaehler(self, nr):
        with self._lock:
            return int(self._zaehler[nr])

    def zaehler_reset(self, nr, timeout=1.0):
        with self._lock:
            self._zaehler[nr] = 0.0

    def zaehler_eingang(self, nr):
        with self._lock:
            return int(self.sim_zaehler_eingaenge.get(nr, 0))

    # --- Eingänge ----------------------------------------------------------
    def eingang(self, nr):
        with self._lock:
            for m, (ref, _, _) in self._achsen.items():
                if ref == nr:
                    return int(self.pos[m] <= 0)
            for m, (t_ein, t_aus) in self._ausleger.items():
                if nr == t_ein:
                    return int(self.ausleger_pos[m] <= 0.0)
                if nr == t_aus:
                    return int(self.ausleger_pos[m] >= 1.0)
            return int(self.sim_eingaenge.get(nr, 0))

    def spannung(self, nr):
        return SIM_FARBWERTE.get(self.sim_farbe, 100)

    # --- Nur Simulation: Dashboard-Knöpfe ----------------------------------
    def sim_werkstueck_einlegen(self, farbe):
        with self._lock:
            self.sim_farbe = farbe
            self.sim_eingaenge[cfg.DPS_LICHTSCHRANKE_EINGABE] = 0

    def sim_ausgabe(self, belegt):
        with self._lock:
            self.sim_zaehler_eingaenge[cfg.DPS_LICHTSCHRANKE_AUSGABE] = 0 if belegt else 1

    # --- Sonstiges ---------------------------------------------------------
    def alles_aus(self):
        with self._lock:
            for nr in self._v:
                self._v[nr] = 0

    def trennen(self):
        self.alles_aus()
        self._stop.set()
