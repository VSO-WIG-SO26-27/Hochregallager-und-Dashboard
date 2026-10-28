"""
Bearbeitungsstation mit Ofen (MPO = Multi-Processing Station with Oven).

  TxtMultiProcessingStation.cpp     ->  Ein-/Ausgänge (Ventile, Säge, Lichtschranken)
  TxtMultiProcessingStationRun.cpp  ->  Zustandsautomat (_schritt)

Ablauf im Original (eine Bearbeitung):
  IDLE           wartet auf den Auftrag des Greifers und das Werkstück am Ofen
  BURN           Schieber fährt in den Ofen, Tür zu, Licht blinkt ("brennen"), Tür auf, Schieber raus
                 (gleichzeitig fährt der interne Sauger zum Ofen)
  VGR_TRANSPORT  interner Sauger setzt das Werkstück vom Ofen auf den Drehtisch
                 (Name aus dem Original – hat nichts mit dem großen Greifer zu tun)
  TABLE_SAW      Drehtisch zur Säge, Säge rechts und links
  TABLE_BELT     Drehtisch zum Band
  EJECT          Auswerfer schiebt das Werkstück aufs Band  -> Meldung "fertig"
  TRANSPORT      Band läuft, bis das Werkstück am Bandende war -> weiter zur Sortierstrecke

Unterschiede zum Original (siehe auch MPO-Repo, docs/QUALITAETSPRUEFUNG.md):
  * Zeitüberschreitungen (kein Werkstück am Ofen, Band leer) führen wirklich in
    den Fehlerzustand. Im C++-Code wurde der FAULT-Übergang sofort überschrieben.
  * Bei Fehlern werden Motoren, Kompressor, Auswerfer und Licht abgeschaltet.
  * Nach dem Quittieren fährt die Station zuerst in die Grundstellung (INIT).
"""

import threading
import time

from . import konfiguration as cfg
from .achsen import AchsenFehler, EndschalterAchse, Foerderband
from .station import Station

# Quittungscodes (TxtMpoAckCode_t)
ACK_EXIT, ACK_STARTED, ACK_PRODUCED = 0, 1, 2

TISCH_NAMEN = ["Sauger", "Säge", "Band"]

HINWEISE = {
    "INIT": "Fährt in die Grundstellung …",
    "IDLE": "Bereit – wartet auf ein Werkstück vom Greifer.",
    "BURN": "Werkstück wird im Ofen „gebrannt“ …",
    "VGR_TRANSPORT": "Interner Sauger setzt das Werkstück auf den Drehtisch …",
    "TABLE_SAW": "Werkstück wird bearbeitet (Säge) …",
    "TABLE_BELT": "Drehtisch fährt zum Band …",
    "EJECT": "Werkstück wird aufs Band geschoben …",
    "TRANSPORT": "Band bringt das Werkstück zur Sortierstrecke …",
    "FAULT": "Fehler – Ursache beheben, dann quittieren (danach Grundstellung).",
}


class Bearbeitungsstation(Station):
    kuerzel = "MPO"
    ERLAUBT = {
        "IDLE": {"produzieren", "grundstellung", "hand"},
        "FAULT": {"quittieren"},
    }

    def __init__(self, hw):
        super().__init__(hw)
        self.tisch = EndschalterAchse("Drehtisch", hw, cfg.MPO_MOTOR_TISCH, cfg.MPO_TISCH_TASTER, self.nothalt)
        self.greifer = EndschalterAchse("Sauger", hw, cfg.MPO_MOTOR_GREIFER, cfg.MPO_GREIFER_TASTER, self.nothalt)
        self.schieber = EndschalterAchse("Ofenschieber", hw, cfg.MPO_MOTOR_SCHIEBER,
                                         cfg.MPO_SCHIEBER_TASTER, self.nothalt)
        self.band = Foerderband(hw, cfg.MPO_MOTOR_BAND, self.nothalt)
        self.werkstueck = None       # C++: reqVGRwp
        self.zyklen = 0
        # Zustand der Ausgänge merken (der TXT meldet Ausgänge nicht zurück)
        self.ausgaenge = {"kompressor": 0, "tuer_offen": 0, "licht": 0, "vakuum": 0, "senken": 0,
                          "auswerfer": 0, "saege": 0}

    # =======================================================================
    # Ein-/Ausgänge (TxtMultiProcessingStation.cpp)
    # =======================================================================
    def _setze(self, name, nr, an):
        self.hw.ausgang(nr, 512 if an else 0)
        self.ausgaenge[name] = 1 if an else 0

    def kompressor(self, an):     self._setze("kompressor", cfg.MPO_KOMPRESSOR, an)       # setCompressor
    def tuer(self, offen):        self._setze("tuer_offen", cfg.MPO_VENTIL_TUER, offen)    # setValveOvenDoor
    def licht(self, an):          self._setze("licht", cfg.MPO_OFENLICHT, an)              # setLightOven
    def vakuum(self, an):         self._setze("vakuum", cfg.MPO_VENTIL_VAKUUM, an)         # setValveVacuum
    def senken(self, an):         self._setze("senken", cfg.MPO_VENTIL_SENKEN, an)         # setValveLowering
    def auswerfer(self, an):      self._setze("auswerfer", cfg.MPO_VENTIL_AUSWERFER, an)   # setValveEjection

    def saege(self, richtung):
        """C++: setSawRight (-1), setSawLeft (+1), setSawOff (0)."""
        self.hw.motor(cfg.MPO_MOTOR_SAEGE, 512 * richtung)
        self.ausgaenge["saege"] = richtung

    def werkstueck_am_ofen(self):        # C++: isOvenTriggered
        return self.hw.eingang(cfg.MPO_LICHTSCHRANKE_OFEN) == 0

    def werkstueck_am_bandende(self):    # C++: isEndConveyorBeltTriggered
        return self.hw.eingang(cfg.MPO_LICHTSCHRANKE_BANDENDE) == 0

    # --- Hilfen ------------------------------------------------------------
    def _warte(self, sekunden):
        """Wie std::this_thread::sleep_for – bricht aber bei Not-Halt ab."""
        ende = time.time() + sekunden
        while time.time() < ende:
            if self.nothalt.is_set():
                raise AchsenFehler("Not-Halt")
            time.sleep(0.01)

    @staticmethod
    def _im_hintergrund(f):
        """Bewegung parallel starten (C++: std::thread). Gibt eine Funktion zum
        Warten zurück, die einen Fehler der Bewegung weiterreicht (C++: join)."""
        fehler = []

        def lauf():
            try:
                f()
            except Exception as e:
                fehler.append(e)

        t = threading.Thread(target=lauf, daemon=True)
        t.start()

        def warten():
            t.join()
            if fehler:
                raise fehler[0]
        return warten

    def fahre_tisch(self, ziel):
        """Drehtisch zu Position 0 (Sauger), 1 (Säge) oder 2 (Band).
        Der Motor fährt zu Position 0 "links", zu 1 und 2 "rechts". Damit er nie
        über den letzten Taster hinaus dreht, geht es bei Bedarf erst zurück zu 0."""
        if ziel > 0:
            jetzt = self.tisch.position()
            if jetzt is None or jetzt > ziel:
                self.tisch.fahre_zu(0)
        self.tisch.fahre_zu(ziel)

    def grundstellung(self):
        """C++: Zustand INIT – Sauger, Drehtisch und Ofenschieber gleichzeitig auf S1."""
        self._setze_status(True, "BUSY")
        self.kompressor(True)
        self.tuer(True)
        self._warte(0.3)
        self._parallel(lambda: self.greifer.fahre_zu(0), lambda: self.tisch.fahre_zu(0),
                       lambda: self.schieber.fahre_zu(0))
        self.kompressor(False)
        self._setze_status(False, "READY")

    # =======================================================================
    # Anforderungen
    # =======================================================================
    def anfordern(self, befehl, quelle="dashboard", **daten):
        if befehl == "sim_ofen_einlegen":        # Simulationsknopf – geht in jedem Zustand
            if not hasattr(self.hw, "sim_ofen_einlegen"):
                return False, "Nur in der Simulation"
            self.hw.sim_ofen_einlegen(daten.get("farbe", "WHITE"))
            return True, "Sim: Werkstück liegt auf dem Ofenschieber"
        return super().anfordern(befehl, quelle, **daten)

    def _pruefe_dashboard(self, befehl, daten):
        if befehl == "produzieren" and not self.werkstueck_am_ofen():
            return False, "Kein Werkstück auf dem Ofenschieber (Lichtschranke am Ofen ist frei)"
        return True, ""

    def _angefordert(self, befehl, daten):
        if befehl == "produzieren":
            self.werkstueck = daten.get("werkstueck") or {"id": "", "type": "NONE", "state": "RAW"}

    # =======================================================================
    # Zustandsautomat (TxtMultiProcessingStationRun.cpp)
    # =======================================================================
    def _bei_eintritt(self, zustand):
        if zustand == "FAULT":
            for aus in (self.kompressor, self.auswerfer, self.licht):
                try:
                    aus(False)
                except Exception:
                    pass
            self.ausgaenge["saege"] = 0
        elif zustand == "IDLE":
            self._setze_status(False, "READY")

    def _fault_schritt(self):
        """Wie in station.py – nach dem Quittieren aber zuerst in die Grundstellung."""
        if self._hole("quittieren") is not None:
            self._verwerfe_anforderungen()
            self.nothalt.clear()
            self.fehler = ""
            self._setze_status(False, "READY")
            self._wechsel("INIT")

    def _schritt(self):
        z = self.zustand

        if z == "FAULT":
            self._fault_schritt()

        elif z == "INIT":
            self.grundstellung()
            self._wechsel("IDLE")

        elif z == "IDLE":
            if self._hole("produzieren") is not None:
                start = time.time()
                while not self.werkstueck_am_ofen():
                    if time.time() - start > cfg.MPO_WARTE_OFEN_S:
                        raise AchsenFehler(f"Kein Werkstück am Ofen (nach {cfg.MPO_WARTE_OFEN_S:.0f} s)")
                    self._warte(0.01)
                self._quittung(ACK_STARTED, self.werkstueck)
                self.log("Werkstück am Ofen erkannt – Bearbeitung startet")
                self._wechsel("BURN")
            elif self._hole("grundstellung") is not None:
                self.grundstellung()
            else:
                hand = self._hole("hand")
                if hand is not None:
                    self._handbetrieb(hand)

        elif z == "BURN":
            self._setze_status(True, "BUSY")
            self.kompressor(True)                 # rein
            self._warte(2.5)
            self.schieber.fahre_zu(1)
            self.tuer(False)
            self._warte(1.0)
            greifer_fertig = self._im_hintergrund(lambda: self.greifer.fahre_zu(1))
            for _ in range(cfg.MPO_BRENNEN_BLINKEN):   # brennen
                self.licht(True)
                self._warte(cfg.MPO_BRENNEN_TAKT_S)
                self.licht(False)
                self._warte(cfg.MPO_BRENNEN_TAKT_S)
            self._warte(1.0)
            self.kompressor(True)                 # raus
            self.tuer(True)
            self.schieber.fahre_zu(0)
            greifer_fertig()
            self._wechsel("VGR_TRANSPORT")

        elif z == "VGR_TRANSPORT":
            self.fahre_tisch(0)
            self.senken(True)                     # aufnehmen
            self._warte(1.0)
            self.vakuum(True)
            self._warte(1.0)
            self.senken(False)
            self.greifer.fahre_zu(0)              # zum Drehtisch
            self.senken(True)                     # ablegen
            self._warte(0.4)
            self.vakuum(False)
            self._warte(0.3)
            self.senken(False)
            self._warte(0.5)
            self.kompressor(False)
            self._wechsel("TABLE_SAW")

        elif z == "TABLE_SAW":
            self.fahre_tisch(1)
            self._warte(1.0)
            self.saege(-1)
            self._warte(cfg.MPO_SAEGEN_S)
            self.saege(0)
            self.saege(+1)
            self._warte(cfg.MPO_SAEGEN_S)
            self.saege(0)
            self._warte(1.0)
            self._wechsel("TABLE_BELT")

        elif z == "TABLE_BELT":
            self.fahre_tisch(2)
            self._wechsel("EJECT")

        elif z == "EJECT":
            self.band.rechts_an()
            self.kompressor(True)
            self._warte(0.4)
            self.auswerfer(True)
            self._warte(0.1)
            self.auswerfer(False)
            self.kompressor(False)
            if self.werkstueck:
                self.werkstueck["state"] = "PROCESSED"
            self._quittung(ACK_PRODUCED, self.werkstueck)
            self._wechsel("TRANSPORT")

        elif z == "TRANSPORT":
            self._setze_status(False, "READY")
            start = time.time()
            while not self.werkstueck_am_bandende():
                if time.time() - start > cfg.MPO_WARTE_BANDENDE_S:
                    raise AchsenFehler("Werkstück kam nicht am Bandende an "
                                       f"(nach {cfg.MPO_WARTE_BANDENDE_S:.0f} s)")
                self._warte(0.01)
            self._warte(cfg.MPO_BAND_NACHLAUF_S)
            self.band.aus()
            self.zyklen += 1
            self.log("Werkstück an die Sortierstrecke übergeben")
            self._wechsel("IDLE")

    # --- Handbetrieb (nur im Dashboard, gibt es im Original nicht) ---------
    def _pneumatik(self, ventil, an):
        """Ventil umschalten; der Kompressor läuft dafür kurz mit."""
        self.kompressor(True)
        ventil(an)
        self._warte(1.0)
        self.kompressor(False)

    def _handbetrieb(self, h):
        aktion = h.get("aktion")
        self.log(f"Handbetrieb: {aktion} {h.get('ziel', '')}")
        self._setze_status(True, "BUSY")
        try:
            if aktion == "tisch":
                self.fahre_tisch(int(h.get("ziel", 0)))
            elif aktion == "sauger_ofen":
                self.greifer.fahre_zu(1)
            elif aktion == "sauger_tisch":
                self.greifer.fahre_zu(0)
            elif aktion == "schieber_rein":
                self.schieber.fahre_zu(1)
            elif aktion == "schieber_raus":
                self.schieber.fahre_zu(0)
            elif aktion == "tuer_auf":
                self._pneumatik(self.tuer, True)
            elif aktion == "tuer_zu":
                self._pneumatik(self.tuer, False)
            elif aktion == "licht":
                self.licht(not self.ausgaenge["licht"])
            elif aktion == "saege":
                self.saege(-1)
                self._warte(2.0)
                self.saege(0)
            elif aktion == "band":
                self.band.laeuft_fuer(3.0)
            elif aktion == "auswerfen":
                self.kompressor(True)
                self._warte(0.4)
                self.auswerfer(True)
                self._warte(0.1)
                self.auswerfer(False)
                self.kompressor(False)
            elif aktion in ("ansaugen", "loslassen"):
                self.kompressor(True)
                self.senken(True)
                self._warte(0.8)
                self.vakuum(aktion == "ansaugen")
                self._warte(0.5)
                self.senken(False)
                self._warte(0.5)
                self.kompressor(False)
        finally:
            self.saege(0)
            self._setze_status(False, "READY")

    # =======================================================================
    # Zustand für das Dashboard
    # =======================================================================
    def zustandsbericht(self):
        b = self.basis_bericht()
        try:
            eingaenge = {f"I{n}": self.hw.eingang(n) for n in range(1, 9)}
            eingaenge.update({f"E{n}": self.hw.eingang(cfg.ERW + n) for n in range(1, 9)})
            tisch = self.tisch.position()
            greifer = self.greifer.position()
            schieber = self.schieber.position()
        except Exception:
            eingaenge, tisch, greifer, schieber = {}, None, None, None
        b.update({
            "eingaenge": eingaenge,
            "tisch": None if tisch is None else TISCH_NAMEN[tisch],
            "sauger": {0: "Drehtisch", 1: "Ofen"}.get(greifer, "unterwegs"),
            "schieber": {0: "draußen", 1: "im Ofen"}.get(schieber, "unterwegs"),
            "am_ofen": eingaenge.get(f"E{cfg.MPO_LICHTSCHRANKE_OFEN - cfg.ERW}", 1) == 0,
            "am_bandende": eingaenge.get(f"I{cfg.MPO_LICHTSCHRANKE_BANDENDE}", 1) == 0,
            "ausgaenge": dict(self.ausgaenge),
            "werkstueck": self.werkstueck,
            "zyklen": self.zyklen,
            "hinweis": HINWEISE.get(self.zustand, ""),
            "sim": hasattr(self.hw, "sim_ofen_einlegen"),
        })
        return b
