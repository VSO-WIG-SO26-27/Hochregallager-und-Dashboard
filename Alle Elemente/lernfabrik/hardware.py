"""
Hardware-Schicht: Zugriff auf Motoren, Ausgänge, Zähler und Eingänge eines TXT.

Es gibt zwei austauschbare Varianten mit identischer Schnittstelle:

* TxtHardware  – echter TXT-Controller über WLAN mit ftrobopy
                 (optional mit Erweiterungs-TXT, z. B. bei der Bearbeitungsstation)
* SimHardware  – Simulation am Rechner ohne Anlage (zum Testen der Abläufe
                 und des Dashboards). Die stationsspezifischen Simulationen
                 (Greifer, Ofen, Sortierstrecke) stehen in simulation.py.

Nummerierung der Anschlüsse: 1..8 = Master (I1..I8, O1..O8, M1..M4),
9..16 = Erweiterung (wie im C++-Code "8+n", siehe konfiguration.ERW).

Richtungskonvention wie im C++-Code (TxtAxis.cpp):
  Geschwindigkeit > 0  ->  "links"  = Richtung Referenztaster / erster Taster
                           (C++: duty[chM*2] = speed)
  Geschwindigkeit < 0  ->  "rechts" = vom Referenztaster weg / zweiter, dritter Taster
                           (C++: duty[chM*2+1] = speed)
ftrobopy verwendet genau dieselbe Zuordnung (setSpeed(+v) -> PWM-Kanal 2n),
deshalb kann die Logik 1:1 übernommen werden.
"""

import threading
import time

from . import konfiguration as cfg


class HardwareFehler(Exception):
    pass


def _ftrobopy_reparieren(ftrobopy):
    """Fehler in ftrobopy beheben (betrifft nur den Betrieb mit Erweiterungs-TXT).

    Mit Erweiterung tauscht ftrobopy die Daten komprimiert aus und liest aus jeder
    Antwort des TXT fest 77 Werte. Enthält die Antwort weniger Daten, läuft
    compBuffer.GetBits über das Pufferende -> IndexError -> ftrobopy beendet den
    Datenaustausch ("Network error index out of range"), der TXT fällt aus dem
    Online-Modus ("kein Programm") und alle Eingänge frieren ein.
    Ob es passiert, hängt von der Bit-Ausrichtung ab – typischerweise dann, wenn sich
    ein Eingang ändert (z. B. Lichtschranke am Ofen).

    Korrektur: fehlende Bits als 0 lesen. Im Kompressionsformat bedeutet "00"
    "keine Änderung" – nicht übertragene Werte behalten also ihren letzten Stand,
    genau wie es die Auffüllbits am Ende einer Antwort ohnehin vorsehen."""
    cb = getattr(ftrobopy, "compBuffer", None)
    if cb is None or getattr(cb, "_lernfabrik_repariert", False):
        return

    def GetBits(self, count):
        while self.m_bitcount < count:
            if len(self.m_compressed):
                cp = self.m_compressed[0]
                self.m_compressed = self.m_compressed[1:]
                if isinstance(cp, str):
                    cp = ord(cp)
            else:
                cp = 0                      # Pufferende: wie Auffüllbits behandeln
            self.m_bitbuffer |= cp << self.m_bitcount
            self.m_bitcount += 8
        res = self.m_bitbuffer & (0xffffffff >> (32 - count))
        self.m_bitbuffer >>= count
        self.m_bitcount -= count
        return res

    cb.GetBits = GetBits
    cb._lernfabrik_repariert = True


def _teile(nr):
    """Anschlussnummer -> (Nummer am TXT, 0 = Master / 1 = Erweiterung)."""
    return (nr - cfg.ERW, 1) if nr > cfg.ERW else (nr, 0)


# ===========================================================================
# Echter TXT über ftrobopy
# ===========================================================================
class TxtHardware:
    modus = "TXT"

    def __init__(self, ip=cfg.TXT_IP, port=cfg.TXT_PORT, motoren=(1, 2, 3, 4), ausgaenge=(), spannung=(),
                 erweiterung=False):
        """motoren:     Motorausgänge, die als Motor benutzt werden (1..4, Erweiterung 9..12)
        ausgaenge:   Einzelausgänge (z. B. Kompressor/Ventil), 1..8, Erweiterung 9..16
        spannung:    Eingänge, die analog als Spannung (mV) gelesen werden (z. B. Farbsensor)
        erweiterung: True, wenn am EXT-Anschluss ein zweiter TXT hängt (Bearbeitungsstation)
        Alle übrigen Eingänge werden als digitale Taster eingerichtet."""
        try:
            import ftrobopy
        except ImportError as e:
            raise HardwareFehler("ftrobopy ist nicht installiert: py -m pip install ftrobopy") from e
        _ftrobopy_reparieren(ftrobopy)

        self.ip = ip
        try:
            if erweiterung:
                self.txt = ftrobopy.ftrobopy(ip, port, use_extension=True)
            else:
                self.txt = ftrobopy.ftrobopy(ip, port)
        except Exception as e:  # ftrobopy wirft je nach Fehler verschiedene Typen
            raise HardwareFehler(
                f"Keine Verbindung zum TXT unter {ip}:{port}. "
                "Ist der Rechner im Lernfabrik-WLAN und läuft auf diesem TXT kein anderes Programm?"
            ) from e
        if not self.txt.isOnline():
            raise HardwareFehler(f"TXT unter {ip}:{port} antwortet nicht (Online-Modus nicht gestartet).")

        def obj(fabrik, nr):
            n, ext = _teile(nr)
            return fabrik(n, ext=ext) if ext else fabrik(n)

        self._motoren = {nr: obj(self.txt.motor, nr) for nr in motoren}
        self._ausgaenge = {nr: obj(self.txt.output, nr) for nr in ausgaenge}
        self._spannung = {nr: obj(self.txt.voltage, nr) for nr in spannung}
        # Digitale Taster (C++: MODE_R, digital = 1)
        alle = list(range(1, 9)) + (list(range(cfg.ERW + 1, cfg.ERW + 9)) if erweiterung else [])
        self._eingaenge = {nr: obj(self.txt.input, nr) for nr in alle if nr not in self._spannung}
        self.alles_aus()

    # --- Verbindungsüberwachung ---------------------------------------------
    VERBINDUNG_TIMEOUT_S = 2.0

    def _pruefe_verbindung(self):
        """ftrobopy beendet seinen Datenaustausch-Thread bei einem Netzwerk-/CRC-Fehler
        stillschweigend (nur eine print-Ausgabe). Danach bleiben alle Eingänge auf dem
        letzten Wert stehen und der TXT fällt aus dem Online-Modus ("kein Programm").
        Hier wird das erkannt, damit die Station mit einer klaren Meldung anhält."""
        stop = getattr(self.txt, "_txt_stop_event", None)
        zuletzt = getattr(self.txt, "_update_timer", None)
        if (stop is not None and stop.is_set()) or \
                (zuletzt is not None and time.time() - zuletzt > self.VERBINDUNG_TIMEOUT_S):
            raise HardwareFehler(
                f"Verbindung zum TXT {self.ip} verloren (Datenaustausch von ftrobopy gestoppt). "
                "Eingänge sind nicht mehr aktuell – Konsole auf 'Connection to TXT aborted', "
                "'Network error' oder 'CRC' prüfen und das Programm neu starten.")

    # --- Motoren -----------------------------------------------------------
    def motor(self, nr, geschwindigkeit):
        """Motor mit Geschwindigkeit -512..512 laufen lassen (0 = aus)."""
        self._pruefe_verbindung()
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
        """Ausgang setzen, 0..512 (C++: duty[ch] = 512)."""
        self._pruefe_verbindung()
        self._ausgaenge[nr].setLevel(int(wert))

    # --- Zähler (nur Master) ----------------------------------------------
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
        """Digitaler Eingang: 1 = Taster gedrückt / Licht kommt an (C++: uni[] == 1)."""
        self._pruefe_verbindung()
        if nr in self._spannung:
            return int(self._spannung[nr].voltage() > 600)
        return int(self._eingaenge[nr].state())

    def spannung(self, nr):
        """Analoger Eingang in mV (C++: MODE_U, uni[]) – z. B. Farbsensor."""
        self._pruefe_verbindung()
        return int(self._spannung[nr].voltage())

    # --- Sonstiges ---------------------------------------------------------
    def alles_aus(self):
        """Alle Motoren aus. Ausgänge (Kompressor/Ventil) bleiben – sonst fällt
        ein angesaugtes Werkstück herunter. Die Ablaufsteuerung schaltet sie selbst."""
        for m in self._motoren.values():
            m.setSpeed(0)

    def sim_ereignis(self, name, **daten):
        """Nur für die Simulation (dort wird die Werkstück-Welt informiert)."""

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
# Simulation – Grundgerüst
# ===========================================================================
SIM_PROFILE = {
    # Encoder-Achsen:  Motor -> (Referenztaster, Endlage, Startposition)
    # Ausleger:        Motor -> (Taster eingefahren, Taster ausgefahren)
    # Schalterachsen:  Motor -> (Taster S1, S2[, S3], Fahrzeit S1->S2 in s)  – ohne Encoder
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
        "eingaenge": {},
        "zaehler_eingaenge": {},
    },
    "mpo": {
        "achsen": {},
        "ausleger": {},
        "schalterachsen": {cfg.MPO_MOTOR_TISCH: (*cfg.MPO_TISCH_TASTER, 1.2),
                           cfg.MPO_MOTOR_GREIFER: (*cfg.MPO_GREIFER_TASTER, 2.0),
                           cfg.MPO_MOTOR_SCHIEBER: (*cfg.MPO_SCHIEBER_TASTER, 1.5)},
        "eingaenge": {},
        "zaehler_eingaenge": {},
    },
    "sld": {
        "achsen": {},
        "ausleger": {},
        "eingaenge": {},
        "zaehler_eingaenge": {},
    },
}


class SimHardware:
    """Einfaches Physikmodell. Encoder-Achsen: Referenztaster bei Position <= 0.
    Ausleger: 0.0 (eingefahren) .. 1.0 (ausgefahren).
    Schalterachsen: Position 0 .. n-1, Taster k ist an Position k gedrückt.

    Unterklassen (simulation.py) ergänzen das Verhalten der Werkstücke über
    die Haken _welt_schritt, _welt_eingang, _welt_spannung und _ausgang_geaendert."""
    modus = "Simulation"
    IMPULSE_PRO_S = 450      # Encoder-Impulse pro Sekunde bei voller Geschwindigkeit
    AUSLEGER_ZEIT_S = 1.2    # Zeit für einmal komplett aus-/einfahren
    TASTER_NACHLAUF_S = 0.06 # so lange gilt ein überfahrener Taster noch als gedrückt

    def __init__(self, zeitfaktor=1.0, profil="hbw", welt=None):
        p = SIM_PROFILE[profil]
        self.profil = profil
        self.zf = zeitfaktor
        self.welt = welt
        self._lock = threading.RLock()
        self._achsen = dict(p["achsen"])
        self._ausleger = dict(p["ausleger"])
        self._schalterachsen = {m: (list(a[:-1]), a[-1]) for m, a in p.get("schalterachsen", {}).items()}
        self.pos = {m: float(a[2]) for m, a in self._achsen.items()}
        self.ausleger_pos = {m: 0.6 for m in self._ausleger}
        self.schalter_pos = {m: 0.5 for m in self._schalterachsen}
        self._ueberfahren = {}                 # (Motor, Taster-Index) -> Zeitpunkt
        nummern = list(range(1, 5)) + list(range(cfg.ERW + 1, cfg.ERW + 5))
        self._v = {m: 0 for m in nummern}
        self._zaehler = {m: 0.0 for m in nummern}
        self._strecke = {m: 0 for m in nummern}
        self._fertig = {m: True for m in nummern}
        self._ausgaenge = {n: 0 for n in list(range(1, 9)) + list(range(cfg.ERW + 1, cfg.ERW + 9))}
        self.sim_eingaenge = dict(p["eingaenge"])
        self.sim_zaehler_eingaenge = dict(p["zaehler_eingaenge"])
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
                for m, (taster, zeit) in self._schalterachsen.items():
                    v = self._v[m]
                    if not v:
                        continue
                    alt = self.schalter_pos[m]
                    d = abs(v) / 512 * dt / zeit * self.zf
                    neu = max(0.0, min(len(taster) - 1.0, alt - d if v > 0 else alt + d))
                    self.schalter_pos[m] = neu
                    for k in range(len(taster)):        # überfahrene Taster merken
                        if min(alt, neu) <= k <= max(alt, neu):
                            self._ueberfahren[(m, k)] = time.time()
                self._welt_schritt(dt * self.zf)

    # --- Haken für Unterklassen --------------------------------------------
    def _welt_schritt(self, dt):
        """Werkstücke bewegen (dt = simulierte Zeit)."""

    def _welt_eingang(self, nr):
        """Eingänge, die von Werkstücken abhängen. None = nicht zuständig."""
        return None

    def _welt_spannung(self, nr):
        return 100

    def _ausgang_geaendert(self, nr, alt, neu):
        """Ventil/Kompressor/Licht wurde geschaltet."""

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

    def motor_laeuft(self, nr):
        with self._lock:
            return self._v[nr]

    # --- Ausgänge ----------------------------------------------------------
    def ausgang(self, nr, wert):
        with self._lock:
            alt = self._ausgaenge[nr]
            self._ausgaenge[nr] = int(wert)
            if bool(alt) != bool(wert):
                self._ausgang_geaendert(nr, alt, int(wert))

    def ausgang_wert(self, nr):
        with self._lock:
            return self._ausgaenge[nr]

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
    def schalter_gedrueckt(self, m, k):
        p = self.schalter_pos[m]
        return abs(p - k) < 0.02 or time.time() - self._ueberfahren.get((m, k), 0) < self.TASTER_NACHLAUF_S

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
            for m, (taster, _) in self._schalterachsen.items():
                if nr in taster:
                    return int(self.schalter_gedrueckt(m, taster.index(nr)))
            w = self._welt_eingang(nr)
            if w is not None:
                return int(w)
            return int(self.sim_eingaenge.get(nr, 0))

    def spannung(self, nr):
        with self._lock:
            return int(self._welt_spannung(nr))

    # --- Sonstiges ---------------------------------------------------------
    def alles_aus(self):
        with self._lock:
            for nr in self._v:
                self._v[nr] = 0

    def sim_ereignis(self, name, **daten):
        """Station meldet der Simulation ein logisches Ereignis (z. B. welches
        Werkstück das Hochregallager gerade aufs Band gestellt hat)."""
        if self.welt is not None:
            self.welt.ereignis(name, **daten)

    def trennen(self):
        self.alles_aus()
        self._stop.set()
