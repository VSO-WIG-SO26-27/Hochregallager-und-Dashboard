"""
Gemeinsame Grundlage aller Stationen (Hochregallager, Sauggreifer, ...).

Entspricht im C++-Code TxtSimulationModel: Status (bereit/beschäftigt/Fehler/
Kalibrierung), Beobachter für MQTT/Dashboard und die Hauptschleife, die den
Zustandsautomaten alle 10 ms einen Schritt weiterschaltet (run() -> fsmStep()).
"""

import collections
import threading
import time

from .achsen import AchsenFehler
from .hardware import HardwareFehler

# Statuscodes wie im Original (TxtLEDSCode_t) – werden per MQTT verschickt
STATUS_CODES = {"READY": 1, "BUSY": 2, "ERROR": 4, "CALIB": 7}


class Station:
    kuerzel = "?"
    ERLAUBT = {}          # Zustand -> Befehle, die das Dashboard dort auslösen darf

    def __init__(self, hw):
        self.hw = hw
        self.nothalt = threading.Event()
        self.zustand = "INIT"
        self._neuer_zustand = "INIT"
        self.status = "READY"
        self.aktiv = False
        self.fehler = ""

        # Anforderungen von außen (C++: req...-Flags)
        self._lock = threading.Lock()
        self._anf = {}

        self.bei_status = []     # f(code, aktiv)
        self.bei_quittung = []   # f(code, werkstueck)
        self.sperre = None       # optional: f() -> Text, wenn Dashboard-Befehle gesperrt sind

        self.protokoll = collections.deque(maxlen=200)
        self._laeuft = False
        self._thread = None

    # --- Hilfsfunktionen ---------------------------------------------------
    def log(self, text):
        zeile = time.strftime("%H:%M:%S ") + f"[{self.kuerzel}] " + text
        print(zeile, flush=True)
        self.protokoll.append(zeile)

    def _setze_status(self, aktiv, status):
        """C++: setActStatus / setStatus -> Observer -> MQTT f/i/state/..."""
        self.aktiv, self.status = aktiv, status
        for f in list(self.bei_status):
            try:
                f(STATUS_CODES.get(status, 0), aktiv)
            except Exception as e:
                self.log(f"Status-Beobachter-Fehler: {e}")

    def _quittung(self, code, wp):
        for f in list(self.bei_quittung):
            try:
                f(code, wp)
            except Exception as e:
                self.log(f"Quittungs-Beobachter-Fehler: {e}")

    @staticmethod
    def _parallel(*funktionen):
        """Mehrere Bewegungen gleichzeitig (C++: std::thread + join)."""
        fehler = []

        def lauf(f):
            try:
                f()
            except Exception as e:
                fehler.append(e)

        threads = [threading.Thread(target=lauf, args=(f,)) for f in funktionen]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if fehler:
            raise fehler[0]

    # --- Anforderungen -----------------------------------------------------
    def _pruefe_dashboard(self, befehl, daten):
        """Von Unterklassen erweiterbar: zusätzliche Prüfungen für Dashboard-Befehle.
        Gibt (ok, meldung) zurück; ok=None heißt 'sofort erledigt'."""
        return True, ""

    def anfordern(self, befehl, quelle="dashboard", **daten):
        """Gibt (ok, meldung) zurück. Dashboard-Befehle werden gegen den aktuellen
        Zustand geprüft; Befehle anderer Stationen/MQTT werden wie im Original als
        Flag gemerkt und im passenden Zustand abgearbeitet."""
        if befehl == "not_halt":
            self.not_halt()
            return True, "Not-Halt ausgelöst"

        if quelle == "dashboard":
            if befehl not in self.ERLAUBT.get(self.zustand, set()):
                return False, f"{self.kuerzel}: '{befehl}' ist im Zustand {self.zustand} nicht möglich"
            if self.sperre and befehl not in ("quittieren",):
                grund = self.sperre()
                if grund:
                    return False, grund
            ok, meldung = self._pruefe_dashboard(befehl, daten)
            if ok is None:
                return True, meldung
            if not ok:
                return False, meldung

        elif self.zustand == "FAULT" and befehl != "quittieren":
            # Aufträge anderer Stationen im Fehlerzustand nicht für später merken –
            # sonst würden sie nach dem Quittieren unerwartet ausgeführt
            self.log(f"Auftrag '{befehl}' von {quelle} verworfen (Zustand FAULT)")
            return False, f"{self.kuerzel} ist im Fehlerzustand"

        with self._lock:
            self._anf[befehl] = daten
        self._angefordert(befehl, daten)
        return True, f"{self.kuerzel}: Befehl '{befehl}' angenommen"

    def _angefordert(self, befehl, daten):
        """Hook für Unterklassen (z. B. Werkstück merken)."""

    def _hole(self, befehl):
        with self._lock:
            return self._anf.pop(befehl, None)

    def _verwerfe_anforderungen(self):
        with self._lock:
            self._anf.clear()

    def not_halt(self):
        self.nothalt.set()
        self.hw.alles_aus()
        self.log("NOT-HALT – alle Motoren aus")

    # --- Hauptschleife -----------------------------------------------------
    def start(self):
        self._laeuft = True
        self._thread = threading.Thread(target=self._lauf, daemon=True)
        self._thread.start()

    def stop(self):
        self._laeuft = False
        self.not_halt()
        if self._thread:
            self._thread.join(timeout=3)

    def _lauf(self):
        self.log("Ablaufsteuerung gestartet")
        while self._laeuft:
            try:
                if self.nothalt.is_set() and self.zustand != "FAULT":
                    raise AchsenFehler("Not-Halt")
                self._eintritt()
                self._schritt()
                self._eintritt()
            except (AchsenFehler, HardwareFehler) as e:
                self._fehler(str(e))
            except Exception as e:  # unerwartet – trotzdem sicher anhalten
                self._fehler(f"Programmfehler: {e!r}")
            time.sleep(0.01)

    def _fehler(self, text):
        self.hw.alles_aus()
        self.fehler = text
        self.log(f"FEHLER: {text}")
        self._neuer_zustand = "FAULT"
        try:
            self._eintritt()
        except Exception as e:
            self.log(f"Fehler beim Eintritt in FAULT: {e!r}")

    def _wechsel(self, z):
        self._neuer_zustand = z

    def _eintritt(self):
        """Eintrittsaktionen beim Zustandswechsel (erster switch in fsmStep)."""
        if self._neuer_zustand == self.zustand:
            return
        alt, self.zustand = self.zustand, self._neuer_zustand
        self.log(f"Zustand {alt} -> {self.zustand}")
        if self.zustand == "FAULT":
            self._verwerfe_anforderungen()      # alte Aufträge verwerfen, nur "Quittieren" zählt
            self._setze_status(False, "ERROR")
        self._bei_eintritt(self.zustand)

    def _bei_eintritt(self, zustand):
        """Von Unterklassen überschrieben."""

    def _schritt(self):
        """Von Unterklassen überschrieben."""

    def _fault_schritt(self):
        """Gemeinsamer FAULT-Zustand: auf 'quittieren' warten, dann IDLE."""
        if self._hole("quittieren") is not None:
            self._verwerfe_anforderungen()
            self.nothalt.clear()
            self.fehler = ""
            self._setze_status(False, "READY")
            self._wechsel("IDLE")

    def basis_bericht(self):
        return {
            "modus": self.hw.modus,
            "zustand": self.zustand,
            "status": self.status,
            "aktiv": self.aktiv,
            "fehler": self.fehler,
            "nothalt": self.nothalt.is_set(),
            "erlaubt": sorted(self.ERLAUBT.get(self.zustand, set())),
            "gesperrt": (self.sperre() if self.sperre else None) or "",
            "protokoll": list(self.protokoll)[-60:],
        }
