"""
Hardware-Schicht: Zugriff auf Motoren, Zähler und Eingänge.

Es gibt zwei austauschbare Varianten mit identischer Schnittstelle:

* TxtHardware  – echter TXT-Controller über WLAN mit ftrobopy
* SimHardware  – Simulation am Mac ohne Anlage (zum Testen des Ablaufs
                 und des Dashboards)

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

    def __init__(self, ip=cfg.TXT_IP, port=cfg.TXT_PORT):
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
                "Ist der Mac im Lernfabrik-WLAN und läuft auf dem HBW-TXT kein anderes Programm?"
            ) from e
        if not self.txt.isOnline():
            raise HardwareFehler(f"TXT unter {ip}:{port} antwortet nicht (Online-Modus nicht gestartet).")

        # Motoren M1..M4 anlegen (setzt die Ausgänge auf Motorbetrieb)
        self._motoren = {nr: self.txt.motor(nr) for nr in (1, 2, 3, 4)}
        # Alle Eingänge als digitale Taster (C++: MODE_R, digital = 1)
        self._eingaenge = {nr: self.txt.input(nr) for nr in range(1, 9)}
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

    # --- Eingänge ----------------------------------------------------------
    def eingang(self, nr):
        """Digitaler Eingang I<nr>: 1 = Taster gedrückt (wie C++ isSwitchPressed)."""
        return int(self._eingaenge[nr].state())

    # --- Sonstiges ---------------------------------------------------------
    def alles_aus(self):
        for m in self._motoren.values():
            m.setSpeed(0)

    def trennen(self):
        try:
            self.alles_aus()
            time.sleep(0.2)   # damit der Stopp-Befehl noch übertragen wird
            self.txt.stopOnline()
        except Exception:
            pass


# ===========================================================================
# Simulation
# ===========================================================================
class SimHardware:
    """Einfaches Physikmodell des Hochregallagers.

    X/Y: Encoder-Positionen, Referenztaster bei Position <= 0.
    Z  : Ausleger 0.0 (eingefahren, Taster I6) .. 1.0 (ausgefahren, Taster I7).
    """
    modus = "Simulation"
    IMPULSE_PRO_S = 450      # Encoder-Impulse pro Sekunde bei voller Geschwindigkeit
    AUSLEGER_ZEIT_S = 1.2    # Zeit für einmal komplett aus-/einfahren

    def __init__(self, zeitfaktor=1.0):
        self.zf = zeitfaktor
        self._lock = threading.Lock()
        self._v = {1: 0, 2: 0, 3: 0, 4: 0}
        self._zaehler = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
        self._strecke = {1: 0, 2: 0, 3: 0, 4: 0}
        self._fertig = {1: True, 2: True, 3: True, 4: True}
        # Startposition irgendwo im Regal, damit die Referenzfahrt etwas zu tun hat
        self.x, self.y, self.z = 900.0, 400.0, 0.6
        self._stop = threading.Event()
        threading.Thread(target=self._physik, daemon=True).start()

    def _physik(self):
        dt = 0.005
        while not self._stop.is_set():
            time.sleep(dt)
            with self._lock:
                for nr in (2, 4):
                    v = self._v[nr]
                    if v == 0:
                        continue
                    d = abs(v) / 512 * self.IMPULSE_PRO_S * dt * self.zf
                    if nr == 2:
                        self.x = max(-5.0, min(cfg.ENDE_X + 60, self.x - d if v > 0 else self.x + d))
                    else:
                        self.y = max(-5.0, min(cfg.ENDE_Y + 60, self.y - d if v > 0 else self.y + d))
                    self._zaehler[nr] += d
                    if self._strecke[nr] > 0 and self._zaehler[nr] >= self._strecke[nr]:
                        self._v[nr] = 0
                        self._fertig[nr] = True
                v = self._v[3]
                if v:
                    d = abs(v) / 512 * dt / self.AUSLEGER_ZEIT_S * self.zf
                    self.z = max(0.0, min(1.0, self.z - d if v > 0 else self.z + d))

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

    def zaehler(self, nr):
        with self._lock:
            return int(self._zaehler[nr])

    def zaehler_reset(self, nr, timeout=1.0):
        with self._lock:
            self._zaehler[nr] = 0.0

    def eingang(self, nr):
        with self._lock:
            werte = {
                cfg.REF_X: self.x <= 0,
                cfg.REF_Y: self.y <= 0,
                cfg.TASTER_Z_EINGEFAHREN: self.z <= 0.0,
                cfg.TASTER_Z_AUSGEFAHREN: self.z >= 1.0,
            }
        return int(werte.get(nr, False))

    def alles_aus(self):
        with self._lock:
            for nr in self._v:
                self._v[nr] = 0

    def trennen(self):
        self.alles_aus()
        self._stop.set()
