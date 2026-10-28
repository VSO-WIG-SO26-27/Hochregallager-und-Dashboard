"""
Sortierstrecke mit Farberkennung (SLD = Sorting Line with Color Detection).

  TxtSortingLine.cpp           ->  Lichtschranken, Farbsensor, Auswerfer
  TxtSortingLineRun.cpp        ->  Zustandsautomat (_schritt)
  TxtSortingLineCalibData.cpp  ->  daten.KalibrierungSortierung

So sortiert die Station (wie im Original):
  1. Ein Werkstück unterbricht die Lichtschranke am Anfang (I1) -> Band startet.
  2. Unter dem Farbsensor (I2) wird laufend gemessen; der KLEINSTE Wert zählt
     (helle Werkstücke reflektieren mehr -> kleinerer Wert).
  3. An der Lichtschranke vor den Auswerfern (I3) beginnt die Zählung der
     Bandimpulse (C1). Je nach Farbe wird nach 5 / 15 / 26 Flanken das passende
     Ventil geöffnet und das Werkstück in seine Ablage geschoben.
  4. Meldung "sortiert" mit der Farbe an den Greifer, der es dort abholt.

Unterschiede zum Original:
  * Zeitüberschreitungen beim Farbmessen und Zählen führen in den Fehlerzustand
    (im Original hätte die Station endlos gewartet).
  * Nach dem Sortieren wird der Kompressor wieder abgeschaltet.
  * Ob das Werkstück in der Ablage angekommen ist (I6..I8), wird geprüft und
    gemeldet – im Original war diese Prüfung auskommentiert. Es ist nur ein
    Hinweis, kein Fehler.
"""

import time

from . import konfiguration as cfg
from .achsen import AchsenFehler, Foerderband
from .daten import KalibrierungSortierung
from .station import Station

# Quittungscodes (TxtSldAckCode_t)
ACK_EXIT, ACK_STARTED, ACK_SORTED, ACK_CALIB_END = 0, 1, 2, 3

FARBEN = ("WHITE", "RED", "BLUE")

HINWEISE = {
    "INIT": "Startet …",
    "IDLE": "Bereit – wartet auf ein Werkstück am Bandanfang.",
    "START": "Werkstück erkannt – Band läuft …",
    "COLOR_DETECTION": "Farbe wird gemessen …",
    "START_COUNT": "Zählung startet …",
    "CHECK_COUNT": "Werkstück fährt zum Auswerfer …",
    "EJECTION": "Werkstück wird ausgeworfen …",
    "SORTED": "Sortiert.",
    "CALIB_SLD_DETECTION": "Farbkalibrierung: Werkstück an den Bandanfang legen (Reihenfolge weiß, rot, blau).",
    "FAULT": "Fehler – Werkstück vom Band nehmen, dann quittieren.",
}


class Sortierstrecke(Station):
    kuerzel = "SLD"
    ERLAUBT = {
        "IDLE": {"farbkalibrieren", "hand"},
        "FAULT": {"quittieren"},
        "CALIB_SLD_DETECTION": {"farbkalib_ende"},
    }

    def __init__(self, hw):
        super().__init__(hw)
        self.band = Foerderband(hw, cfg.SLD_MOTOR_BAND, self.nothalt)
        self.kalib = KalibrierungSortierung()
        self.messwert = 3000            # C++: detectedColorValue (kleinster Wert)
        self.farbe = "NONE"             # zuletzt erkannte Farbe
        self.letzter_messwert = None    # kleinster Wert des letzten Werkstücks
        self.flanken = 0
        self.sortiert = {f: 0 for f in FARBEN}
        self.kalib_farbe = None
        self.kalib_werte = {}
        self.hinweis_extra = ""
        self._t0 = 0.0

    # =======================================================================
    # Sensoren und Aktoren (TxtSortingLine.cpp)
    # =======================================================================
    def am_start(self):                 # isColorSensorTriggered
        return self.hw.eingang(cfg.SLD_LICHTSCHRANKE_START) == 0

    def am_auswurf(self):               # isEjectionTriggered
        return self.hw.eingang(cfg.SLD_LICHTSCHRANKE_AUSWURF) == 0

    def ablage_belegt(self, farbe):     # isWhite / isRed / isBlue
        return self.hw.eingang(cfg.SLD_LICHTSCHRANKE_ABLAGE[farbe]) == 0

    def farbwert(self):                 # readColorValue
        return self.hw.spannung(cfg.SLD_FARBSENSOR)

    def kompressor(self, an):           # setCompressor
        self.hw.ausgang(cfg.SLD_KOMPRESSOR, 512 if an else 0)

    def auswerfen(self, farbe):         # ejectWhite / ejectRed / ejectBlue
        self.hw.ausgang(cfg.SLD_VENTIL[farbe], 512)
        self._warte(cfg.SLD_AUSWERFEN_S)
        self.hw.ausgang(cfg.SLD_VENTIL[farbe], 0)

    def zaehlerstand(self):
        """Bandimpulse seit dem Zurücksetzen, umgerechnet in Flanken wie im Original."""
        return self.hw.zaehler(cfg.SLD_ZAEHLER) * cfg.SLD_FLANKEN_PRO_IMPULS

    def _warte(self, sekunden):
        ende = time.time() + sekunden
        while time.time() < ende:
            if self.nothalt.is_set():
                raise AchsenFehler("Not-Halt")
            time.sleep(0.01)

    def anfordern(self, befehl, quelle="dashboard", **daten):
        # Simulationsknöpfe – gehen in jedem Zustand
        if befehl in ("sim_band_einlegen", "sim_ablagen_leeren"):
            if not hasattr(self.hw, "sim_band_einlegen"):
                return False, "Nur in der Simulation"
            if befehl == "sim_band_einlegen":
                self.hw.sim_band_einlegen(daten.get("farbe", "WHITE"))
                return True, "Sim: Werkstück liegt am Bandanfang"
            self.hw.sim_ablagen_leeren()
            return True, "Sim: Ablagen geleert"
        return super().anfordern(befehl, quelle, **daten)

    # =======================================================================
    # Zustandsautomat (TxtSortingLineRun.cpp)
    # =======================================================================
    def _bei_eintritt(self, zustand):
        if zustand == "FAULT":
            try:
                self.kompressor(False)
                for f in FARBEN:
                    self.hw.ausgang(cfg.SLD_VENTIL[f], 0)
            except Exception:
                pass
        elif zustand == "IDLE":
            self._setze_status(False, "READY")
            self.kalib_farbe = None

    def _schritt(self):
        z = self.zustand

        if z == "FAULT":
            self._fault_schritt()

        elif z == "INIT":
            self.band.aus()
            self._wechsel("IDLE")

        elif z == "IDLE":
            if self.am_start():
                self.log("Werkstück am Bandanfang erkannt")
                self._wechsel("START")
            elif self._hole("farbkalibrieren") is not None:
                self._wechsel("CALIB_SLD")
            else:
                hand = self._hole("hand")
                if hand is not None:
                    self._handbetrieb(hand)

        elif z == "START":
            self._setze_status(True, "BUSY")
            self.band.rechts_an()
            self.messwert = 3000
            self._t0 = time.time()
            self._quittung(ACK_STARTED, None)
            self._wechsel("COLOR_DETECTION")

        elif z == "COLOR_DETECTION":
            self.messwert = min(self.messwert, self.farbwert())     # kleinster Wert zählt
            if self.am_auswurf():
                self.farbe = self.kalib.farbe(self.messwert)
                self.letzter_messwert = self.messwert
                self.log(f"Farbe: {self.messwert} mV -> {self.farbe}")
                self._wechsel("START_COUNT")
            elif time.time() - self._t0 > cfg.SLD_TIMEOUT_FARBE_S:
                raise AchsenFehler("Werkstück kam nicht an der Auswurf-Lichtschranke an "
                                   f"(nach {cfg.SLD_TIMEOUT_FARBE_S:.0f} s)")

        elif z == "START_COUNT":
            self.kompressor(True)
            self.hw.zaehler_reset(cfg.SLD_ZAEHLER)
            self._t0 = time.time()
            self._wechsel("CHECK_COUNT")

        elif z == "CHECK_COUNT":
            self.flanken = self.zaehlerstand()
            if self.farbe in FARBEN:
                if self.flanken >= self.kalib.zaehler[self.farbe]:
                    self._wechsel("EJECTION")
            elif self.flanken >= cfg.SLD_ZAEHLER_FALSCH:
                raise AchsenFehler(f"Farbe nicht erkannt ({self.letzter_messwert} mV) – Werkstück nicht sortiert")
            if time.time() - self._t0 > cfg.SLD_TIMEOUT_ZAEHLEN_S:
                raise AchsenFehler("Bandzähler C1 zählt nicht (Band blockiert oder Zähler defekt?)")

        elif z == "EJECTION":
            self.auswerfen(self.farbe)
            self._wechsel("SORTED")

        elif z == "SORTED":
            self.band.aus()
            self.kompressor(False)
            self._setze_status(False, "READY")
            self.sortiert[self.farbe] += 1
            # Ergänzung: kommt das Werkstück in der Ablage an? (im Original auskommentiert)
            start = time.time()
            while not self.ablage_belegt(self.farbe) and time.time() - start < 1.5:
                self._warte(0.02)
            if not self.ablage_belegt(self.farbe):
                self.log(f"Hinweis: Lichtschranke der Ablage {self.farbe} meldet kein Werkstück")
            wp = {"id": "", "type": self.farbe, "state": "PROCESSED"}
            self.log(f"Werkstück {self.farbe} sortiert")
            self._quittung(ACK_SORTED, {"werkstueck": wp, "farbwert": self.letzter_messwert})
            self._warte(1.0)
            self._wechsel("IDLE")

        # --- Farbkalibrierung (CALIB_SLD ...) ------------------------------
        elif z == "CALIB_SLD":
            self._setze_status(False, "CALIB")
            self.kalib_werte = {}
            self.messwert = 3000
            self.band.rechts_an()
            self.kalib_farbe = "WHITE"
            self.log("Farbkalibrierung: weißes Werkstück an den Bandanfang legen")
            self._wechsel("CALIB_SLD_DETECTION")

        elif z == "CALIB_SLD_DETECTION":
            if self._hole("farbkalib_ende") is not None:
                self.band.aus()
                self._quittung(ACK_CALIB_END, None)
                self.log("Farbkalibrierung abgebrochen")
                self._wechsel("IDLE")
                return
            self.messwert = min(self.messwert, self.farbwert())
            if self.am_auswurf():
                self.kalib_werte[self.kalib_farbe] = self.messwert
                self.log(f"Farbkalibrierung {self.kalib_farbe}: {self.messwert} mV")
                self._warte(1.0)
                self._wechsel("CALIB_SLD_NEXT")

        elif z == "CALIB_SLD_NEXT":
            w = [self.kalib_werte.get(f, -1) for f in FARBEN]
            if min(w) > 0:
                th1, th2 = (w[0] + w[1]) // 2, (w[1] + w[2]) // 2
                if w[0] < w[1] < w[2] and th1 >= 200 and th2 < 2000:
                    self.kalib.th1, self.kalib.th2 = th1, th2
                    self.kalib.speichern()
                    self.log(f"Farbschwellen gespeichert: weiß < {th1} mV <= rot < {th2} mV <= blau")
                else:
                    self.log(f"Messwerte nicht plausibel (weiß {w[0]}, rot {w[1]}, blau {w[2]} mV) – nicht gespeichert")
                self.band.aus()
                self._quittung(ACK_CALIB_END, None)
                self._wechsel("IDLE")
            else:
                self.kalib_farbe = FARBEN[FARBEN.index(self.kalib_farbe) + 1]
                self.log(f"Farbkalibrierung: jetzt {self.kalib_farbe} an den Bandanfang legen")
                self.messwert = 3000
                self._setze_status(False, "CALIB")
                self._wechsel("CALIB_SLD_DETECTION")

    # --- Handbetrieb (nur im Dashboard) -----------------------------------
    def _handbetrieb(self, h):
        aktion = h.get("aktion")
        self.log(f"Handbetrieb: {aktion} {h.get('farbe', '')}")
        self._setze_status(True, "BUSY")
        try:
            if aktion == "band":
                self.band.laeuft_fuer(3.0)
            elif aktion == "auswerfen" and h.get("farbe") in FARBEN:
                self.kompressor(True)
                self._warte(0.3)
                self.auswerfen(h["farbe"])
                self.kompressor(False)
        finally:
            self._setze_status(False, "READY")

    # =======================================================================
    # Zustand für das Dashboard
    # =======================================================================
    def zustandsbericht(self):
        b = self.basis_bericht()
        try:
            eingaenge = {f"I{n}": self.hw.eingang(n) for n in range(1, 9)}
            mv = self.farbwert()
            flanken = self.zaehlerstand()
        except Exception:
            eingaenge, mv, flanken = {}, None, None
        b.update({
            "eingaenge": eingaenge,
            "farbwert": mv,
            "farbe_live": self.kalib.farbe(mv) if mv is not None else "NONE",
            "messwert": self.letzter_messwert,
            "farbe": self.farbe,
            "flanken": flanken,
            "ablagen": {f: eingaenge.get(f"I{cfg.SLD_LICHTSCHRANKE_ABLAGE[f]}", 1) == 0 for f in FARBEN},
            "sortiert": dict(self.sortiert),
            "kalibrierung": self.kalib.als_dict(),
            "kalib_farbe": self.kalib_farbe,
            "kalib_werte": dict(self.kalib_werte),
            "hinweis": HINWEISE.get(self.zustand, ""),
            "sim": hasattr(self.hw, "sim_band_einlegen"),
        })
        return b
