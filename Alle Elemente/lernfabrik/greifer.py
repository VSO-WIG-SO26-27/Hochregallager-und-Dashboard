"""
Vakuum-Sauggreifer (VGR) mit Ein-/Ausgabestation (DPS).

  TxtVacuumGripperRobot.cpp     ->  Bewegungsabläufe (fahre, eingabe_greifen, ...)
  TxtVacuumGripper.cpp          ->  greifen / loslassen
  TxtDeliveryPickupStation.cpp  ->  Lichtschranken, Farbsensor
  TxtVacuumGripperRobotRun.cpp  ->  Zustandsautomat (_schritt)

Der Greifer ist – wie im Original – der Auftraggeber des Hochregallagers:
er schickt ihm dieselben Befehle, die im Original per MQTT (fl/vgr/do)
verschickt werden, und wartet auf dessen Quittung (fl/hbw/ack).

Genauso beauftragt er die Bearbeitungsstation (ofen.py, im Original fl/vgr/do
Code 7 / fl/mpo/ack) und holt sortierte Werkstücke an der Sortierstrecke ab
(sortierung.py, im Original fl/sld/ack).

Bestellweg (wie im Original, wenn Bearbeitungsstation und Sortierstrecke laufen):
  Hochregallager -> Greifer -> Ofen/Säge -> Band -> Sortierstrecke
  -> Greifer holt das Werkstück aus der Ablage seiner Farbe -> Ausgabe
Ohne diese beiden Stationen (oder im Dashboard auf "direkt" gestellt):
  Hochregallager -> Greifer -> Ausgabe

Abweichungen vom Original (mit dem Team abgestimmt):
  * kein NFC: die NFC-Position wird wie im Original angefahren, gelesen/
    geschrieben wird aber nichts; jedes Werkstück bekommt eine erzeugte ID
  * Bestellweg "direkt" als zusätzliche Möglichkeit (s. o.)
"""

import time
import uuid

from . import konfiguration as cfg
from .achsen import AchsenFehler, ReferenzAchse
from .daten import KalibrierungFarbsensor, KalibrierungGreifer
from .station import Station
from .steuerung import ACK_FETCHED

# Quittungscodes der Bearbeitungsstation und der Sortierstrecke (wie in ofen.py / sortierung.py)
MPO_STARTED = 1
SLD_SORTED = 2

# Farbe -> Ablage an der Sortierstrecke (gespeicherte Greiferpunkte SSD1..3)
ABLAGE_PUNKT = {"WHITE": "SSD1", "RED": "SSD2", "BLUE": "SSD3"}

# Kalibrierpositionen (TxtVgrCalibPos_t) -> gespeicherter Punkt, Anzeigename
KALIB_PUNKTE = {
    "DSI": ("DIN", "Eingabe"),
    "DCS": ("DCS", "Farbsensor"),
    "NFC": ("DNFC", "NFC-Leser"),
    "WDC": ("WDC", "Ablage falsche Farbe"),
    "DSO": ("DOUT", "Ausgabe"),
    "HBW": ("HBW1", "Hochregallager"),
    "MPO": ("MPO", "Bearbeitungsstation"),
    "SL1": ("SSD1", "Sortierung weiß"),
    "SL2": ("SSD2", "Sortierung rot"),
    "SL3": ("SSD3", "Sortierung blau"),
}

# Zustände, in denen der Greifer gerade mit dem Hochregallager zusammenarbeitet
ABLAUF_ZUSTAENDE = {"START_DELIVERY", "COLOR_DETECTION", "NFC_RAW", "NFC_REJECTED", "WRONG_COLOR",
                    "STORE_WP_VGR", "STORE_WP", "FETCH_WP_VGR", "VGR_WAIT_FETCHED",
                    "MOVE_PICKUP_WAIT", "MOVE_PICKUP"}

# Zustände, in denen der Greifer gerade mit der Bearbeitungsstation zusammenarbeitet
MPO_ZUSTAENDE = {"MOVE_VGR2MPO", "START_PRODUCE"}


class Sauggreifer(Station):
    kuerzel = "VGR"
    ERLAUBT = {
        "IDLE": {"bestellen", "annehmen", "kalibrieren", "farbkalibrieren", "referenzfahrt", "hand",
                 "bestellung_abbrechen"},
        "FAULT": {"quittieren"},
        "CALIB_VGR_MOVE": {"kalib_waehle", "kalib_jog", "kalib_speichern", "kalib_ende"},
        "CALIB_DPS_NEXT": {"farbe_messen", "farbkalib_speichern", "farbkalib_ende"},
    }

    def __init__(self, hw, hbw=None):
        super().__init__(hw)
        self.achse_x = ReferenzAchse("Dreh", hw, cfg.VGR_MOTOR_X, cfg.VGR_REF_X, cfg.VGR_ENDE_X, self.nothalt)
        self.achse_y = ReferenzAchse("Hub", hw, cfg.VGR_MOTOR_Y, cfg.VGR_REF_Y, cfg.VGR_ENDE_Y, self.nothalt)
        self.achse_z = ReferenzAchse("Arm", hw, cfg.VGR_MOTOR_Z, cfg.VGR_REF_Z, cfg.VGR_ENDE_Z, self.nothalt)
        self.kalib = KalibrierungGreifer()
        self.farbkalib = KalibrierungFarbsensor()
        self.gegriffen = False
        self.werkstueck = None          # C++: reqWP_HBW
        self.bestell_werkstueck = None  # C++: reqWP_MPO / reqWP_SLD – das bestellte Werkstück
        self.bestellung = {"type": "NONE", "state": "WAITING_FOR_ORDER"}   # C++: ord_state
        self.letzter_farbwert = None
        self.auto_annahme = True        # Werkstück in der Eingabe automatisch annehmen (wie Original)
        self.kalib_punkt = None
        self.farb_messungen = {}
        self.hinweis = ""
        self._eingabe_seit = None
        self.hbw = None
        self.mpo = None                 # Bearbeitungsstation (ofen.py)
        self.sld = None                 # Sortierstrecke (sortierung.py)
        self.ueber_bearbeitung = True   # Bestellweg: True = über Ofen + Sortierung, False = direkt
        if hasattr(hw, "sim_positionen"):
            hw.sim_positionen = self.kalib.pos    # Simulation kennt die kalibrierten Punkte
        if hbw is not None:
            self.verbinde(hbw)

    # --- Kopplung mit dem Hochregallager -----------------------------------
    def verbinde(self, hbw):
        """Ersetzt die MQTT-Verbindung des Originals: Quittungen des HBW
        (fl/hbw/ack) kommen hier direkt an, Aufträge gehen direkt ans HBW."""
        self.hbw = hbw
        hbw.bei_quittung.append(self._hbw_quittung)
        hbw.sperre = lambda: ("Der Greifer arbeitet gerade mit dem Hochregallager – bitte warten"
                              if self.zustand in ABLAUF_ZUSTAENDE else "")

    def _hbw_quittung(self, code, wp):
        if code == ACK_FETCHED:
            self.anfordern("hbw_fetched", quelle="hbw", werkstueck=wp)

    def _an_hbw(self, befehl, wp=None):
        """C++: mqttclient->publishVGR_Do(...)"""
        if self.hbw is None:
            raise RuntimeError("Kein Hochregallager verbunden")
        self.log(f"-> HBW: {befehl}")
        ok, meldung = self.hbw.anfordern(befehl, quelle="vgr", werkstueck=wp)
        if not ok:
            raise AchsenFehler(f"Hochregallager nimmt '{befehl}' nicht an: {meldung}")

    # --- Kopplung mit Bearbeitungsstation und Sortierstrecke ---------------
    def verbinde_bearbeitung(self, mpo, sld):
        """Ersetzt fl/vgr/do (Code 7), fl/mpo/ack und fl/sld/ack des Originals."""
        self.mpo, self.sld = mpo, sld
        mpo.bei_quittung.append(self._mpo_quittung)
        sld.bei_quittung.append(self._sld_quittung)

        def sperre():
            if self.zustand in MPO_ZUSTAENDE or self.bestellung["state"] == "IN_PROCESS":
                return "Eine Bestellung ist gerade in Bearbeitung – bitte warten"
            return ""
        mpo.sperre = sperre
        sld.sperre = sperre

    def _mpo_quittung(self, code, wp):
        if code == MPO_STARTED:
            self.anfordern("mpo_gestartet", quelle="mpo", werkstueck=wp)

    def _sld_quittung(self, code, daten):
        if code == SLD_SORTED:
            self.anfordern("sld_sortiert", quelle="sld", werkstueck=(daten or {}).get("werkstueck"))

    def bearbeitung_aktiv(self):
        """Läuft die Bestellung über Ofen und Sortierstrecke?"""
        return self.ueber_bearbeitung and self.mpo is not None and self.sld is not None

    def bearbeitung_bereit(self):
        """Grund, warum Ofen/Sortierung gerade nicht können – oder ''."""
        for st, name in ((self.mpo, "Bearbeitungsstation"), (self.sld, "Sortierstrecke")):
            if st.zustand != "IDLE" or st.aktiv:
                return f"{name} ist nicht bereit (Zustand {st.zustand})"
        return ""

    def _pruefe_fehler(self, station, name):
        if station is not None and station.zustand == "FAULT":
            raise AchsenFehler(f"{name} meldet einen Fehler – Bestellung abgebrochen")

    def hbw_bereit_fuer(self, aufgabe, typ=None):
        """Gibt einen Grund zurück, warum das HBW nicht kann – oder '' wenn alles passt."""
        if self.hbw is None:
            return "Kein Hochregallager verbunden"
        if self.hbw.zustand != "IDLE" or self.hbw.aktiv:
            return f"Hochregallager ist nicht bereit (Zustand {self.hbw.zustand})"
        if aufgabe == "einlagern" and not self.hbw.hat_leeren_behaelter():
            return "Kein leerer Behälter im Hochregallager"
        if aufgabe == "auslagern" and not self.hbw.hat_farbe(typ):
            return f"Kein Werkstück {typ} im Hochregallager"
        return ""

    # =======================================================================
    # Sauggreifer (TxtVacuumGripper.cpp)
    # =======================================================================
    def greifen(self):
        """Kompressor an, 2 s warten, Ventil auf."""
        self.hw.ausgang(cfg.VGR_KOMPRESSOR, 512)
        ende = time.time() + cfg.VGR_ANSAUGZEIT_S
        while time.time() < ende:
            time.sleep(0.02)
        self.hw.ausgang(cfg.VGR_VENTIL, 512)
        self.gegriffen = True

    def loslassen(self):
        """Ventil zu, Kompressor aus."""
        self.hw.ausgang(cfg.VGR_VENTIL, 0)
        self.hw.ausgang(cfg.VGR_KOMPRESSOR, 0)
        self.gegriffen = False

    # =======================================================================
    # Bewegungsabläufe (TxtVacuumGripperRobot.cpp)
    # =======================================================================
    def set_geschwindigkeit(self, v):
        for a in (self.achse_x, self.achse_y, self.achse_z):
            a.geschwindigkeit = v

    def referenzfahrt(self):
        """C++: moveRef – zuerst Hubachse hoch, dann Drehung und Arm gleichzeitig."""
        self._setze_status(True, "BUSY")
        self.achse_y.referenzfahrt()
        self._parallel(self.achse_x.referenzfahrt, self.achse_z.referenzfahrt)
        self._setze_status(False, "READY")

    def fahre_xyz(self, x, y, z):
        """C++: move(x, y, z, VGRMOV_PTP) – alle drei Achsen gleichzeitig."""
        self._parallel(lambda: self.achse_x.fahre_abs(x),
                       lambda: self.achse_y.fahre_abs(y),
                       lambda: self.achse_z.fahre_abs(z))

    def fahre(self, name):
        """C++: move("NAME") – zu einem gespeicherten Punkt fahren."""
        self._setze_status(True, "BUSY")
        x, y, z = self.kalib.pos[name]
        self.fahre_xyz(x, y, z)
        self._setze_status(False, "READY")

    def eingabe_greifen(self):          # C++: moveDeliveryInAndGrip
        self.fahre("DIN0")
        self.fahre("DIN")
        self.greifen()
        self.fahre("DIN0")

    def ausgabe_ablegen(self):          # C++: moveDeliveryOutAndRelease
        self.achse_y.referenzfahrt()
        self.fahre("DOUT0")
        self.fahre("DOUT")
        self.loslassen()
        self.referenzfahrt()

    def zum_farbsensor(self, halb=False):   # C++: moveColorSensor
        self.fahre("DCS0")
        if not halb:
            self.fahre("DCS")

    def zum_nfc_mit_yref(self):         # C++: moveRefYNFC
        self.achse_y.referenzfahrt()
        self.zum_nfc()

    def zum_nfc(self):                  # C++: moveNFC
        self.fahre("DNFC0")
        self.fahre("DNFC")

    def falsch_ablegen(self):           # C++: moveWrongRelease
        self.fahre("WDC0")
        self.fahre("WDC")
        self.loslassen()
        self.referenzfahrt()

    def zum_hbw(self):                  # C++: moveToHBW / moveFromHBW1
        self.fahre("HBW0")
        self.fahre("HBW")

    def zur_bearbeitung(self):          # C++: moveMPO
        """Werkstück auf den Ofenschieber der Bearbeitungsstation legen."""
        self.fahre("MPO0")
        self.fahre("MPO")
        self.loslassen()
        time.sleep(1.0)
        self.achse_y.referenzfahrt()
        self.achse_z.referenzfahrt()

    def zur_ablage(self, farbe):        # C++: moveSSD1 / moveSSD2 / moveSSD3
        """Über die Ablage der Sortierstrecke für diese Farbe fahren."""
        punkt = ABLAGE_PUNKT[farbe]
        self.fahre(punkt + "0")
        self.fahre(punkt)

    def von_hbw_greifen(self):          # C++: moveFromHBW2
        self.fahre("HBW1")
        self.greifen()
        self.fahre("HBW")
        self.fahre("HBW0")

    def fahre_kalib_punkt(self, punkt):
        """C++: moveCalibPos"""
        if punkt in ("SL1", "SL2", "SL3"):
            self.achse_y.referenzfahrt()
        self.referenzfahrt()
        if punkt == "HBW":
            for n in ("HBW0", "HBW", "HBW1"):
                self.fahre(n)
        elif punkt == "MPO":
            for n in ("HBW", "HBW0", "MPO0", "MPO"):
                self.fahre(n)
        elif punkt in ("SL1", "SL2", "SL3"):
            ziel = KALIB_PUNKTE[punkt][0]
            self.fahre(ziel + "0")
            self.fahre(ziel)
        else:
            self.fahre(KALIB_PUNKTE[punkt][0])

    def _kalib_speichern(self, punkt):
        """C++: CALIB_VGR_MOVE, Knopf b1 – inkl. der Kopierregeln für die Anfahrpunkte."""
        p = [self.achse_x.pos, self.achse_y.pos, self.achse_z.pos]
        k = self.kalib
        ziel = KALIB_PUNKTE[punkt][0]
        k.setze(ziel, p)
        regeln = {
            "DSI": [("DIN", "DIN0", "x"), ("DIN", "DIN0", "z")],
            "DCS": [("DCS", "DCS0", "x"), ("DCS", "DCS0", "z")],
            "NFC": [("DNFC", "DNFC0", "x"), ("DNFC", "DNFC0", "z")],
            "WDC": [("WDC", "WDC0", "x")],
            "DSO": [("DOUT", "DOUT0", "x")],
            "HBW": [("HBW1", "HBW", "x"), ("HBW1", "HBW", "z"), ("HBW1", "HBW0", "x")],
            "MPO": [("MPO", "MPO0", "x"), ("MPO", "MPO0", "z")],
            "SL1": [("SSD1", "SSD10", "x")],
            "SL2": [("SSD2", "SSD20", "x")],
            "SL3": [("SSD3", "SSD30", "x")],
        }
        for quelle, kopie, achse in regeln[punkt]:
            k.kopiere(quelle, kopie, achse)
        k.speichern()
        self.log(f"Kalibrierung {punkt} ({ziel}) gespeichert: x={p[0]} y={p[1]} z={p[2]}")

    # =======================================================================
    # Sensoren der Ein-/Ausgabestation
    # =======================================================================
    def eingabe_belegt(self):
        """C++: !dps.is_DIN() – Lichtschranke I7 unterbrochen."""
        return self.hw.eingang(cfg.DPS_LICHTSCHRANKE_EINGABE) == 0

    def ausgabe_frei(self):
        """C++: dps.is_DOUT() – Lichtschranke an C4 frei."""
        return self.hw.zaehler_eingang(cfg.DPS_LICHTSCHRANKE_AUSGABE) == 1

    def farbe_messen(self):
        mv = self.hw.spannung(cfg.DPS_FARBSENSOR)
        self.letzter_farbwert = mv
        return mv, self.farbkalib.farbe(mv)

    # =======================================================================
    # Anforderungen
    # =======================================================================
    def anfordern(self, befehl, quelle="dashboard", **daten):
        # Einstellungen und Simulationsknöpfe gehen in jedem Zustand
        if befehl == "auto_annahme":
            self.auto_annahme = bool(daten.get("an"))
            self.log(f"Automatische Annahme an der Eingabe: {'an' if self.auto_annahme else 'aus'}")
            return True, "Einstellung übernommen"
        if befehl == "sim_einlegen":
            if not hasattr(self.hw, "sim_werkstueck_einlegen"):
                return False, "Nur in der Simulation"
            self.hw.sim_werkstueck_einlegen(daten.get("farbe", "WHITE"))
            return True, "Sim: Werkstück liegt in der Eingabe"
        if befehl == "sim_ausgabe":
            if not hasattr(self.hw, "sim_ausgabe"):
                return False, "Nur in der Simulation"
            self.hw.sim_ausgabe(bool(daten.get("belegt")))
            return True, "Sim: Ausgabe " + ("belegt" if daten.get("belegt") else "frei")
        if befehl == "bestellweg":
            if self.bestellung["state"] != "WAITING_FOR_ORDER":
                return False, "Erst nach der laufenden Bestellung änderbar"
            self.ueber_bearbeitung = daten.get("weg") != "direkt"
            self.log("Bestellweg: " + ("über Bearbeitung und Sortierung" if self.ueber_bearbeitung else "direkt zur Ausgabe"))
            return True, "Einstellung übernommen"
        return super().anfordern(befehl, quelle, **daten)

    def _pruefe_dashboard(self, befehl, daten):
        if befehl == "bestellen":
            typ = daten.get("farbe")
            if typ not in ("WHITE", "RED", "BLUE"):
                return False, "Bitte Farbe wählen"
            if self.bestellung["state"] != "WAITING_FOR_ORDER":
                return False, "Es läuft bereits eine Bestellung"
            grund = self.hbw_bereit_fuer("auslagern", typ)
            if not grund and self.bearbeitung_aktiv():
                grund = self.bearbeitung_bereit()
            if grund:
                return False, grund
        if befehl == "bestellung_abbrechen":
            if self.bestellung["state"] == "WAITING_FOR_ORDER":
                return False, "Es ist keine Bestellung offen"
            self.log(f"Bestellung {self.bestellung['type']} von Hand abgebrochen")
            self.bestellung = {"type": "NONE", "state": "WAITING_FOR_ORDER"}
            return None, "Bestellung abgebrochen"
        if befehl == "annehmen":
            if not self.eingabe_belegt():
                return False, "Es liegt kein Werkstück in der Eingabe"
            grund = self.hbw_bereit_fuer("einlagern")
            if grund:
                return False, grund
        if befehl == "kalib_waehle" and daten.get("punkt") not in KALIB_PUNKTE:
            return False, "Unbekannter Kalibrierpunkt"
        if befehl == "kalib_speichern" and not self.kalib_punkt:
            return False, "Zuerst einen Kalibrierpunkt anfahren"
        if befehl == "farbe_messen" and daten.get("farbe") not in ("WHITE", "RED", "BLUE"):
            return False, "Bitte Farbe wählen"
        return True, ""

    # =======================================================================
    # Zustandsautomat (TxtVacuumGripperRobotRun.cpp)
    # =======================================================================
    def _bei_eintritt(self, zustand):
        if zustand == "FAULT":
            self.loslassen()                 # wie Original: release()
        elif zustand == "IDLE":
            self.loslassen()
            self.set_geschwindigkeit(cfg.GESCHWINDIGKEIT)
            self.referenzfahrt()
            self._setze_status(False, "READY")
            self.hinweis = ""
            self._eingabe_seit = None
        elif zustand == "CALIB_VGR":
            self.referenzfahrt()

    def _schritt(self):
        z = self.zustand

        if z == "FAULT":
            self._fault_schritt()

        elif z == "INIT":
            self.referenzfahrt()
            self._wechsel("IDLE")

        elif z == "IDLE":
            self._idle_schritt()

        # --- Einlagern: Eingabe -> Farbsensor -> Hochregallager ------------
        elif z == "START_DELIVERY":
            self.hinweis = "Werkstück wird aus der Eingabe geholt"
            self.eingabe_greifen()
            self.zum_nfc()                    # NFC-Position wie im Original (ohne Lesen)
            self.werkstueck = {"id": uuid.uuid4().hex[:14].upper(), "type": "NONE", "state": "RAW"}
            self._wechsel("COLOR_DETECTION")

        elif z == "COLOR_DETECTION":
            self.zum_farbsensor()
            mv, farbe = self.farbe_messen()
            self.log(f"Farbsensor: {mv} mV -> {farbe}")
            if farbe != "NONE":
                self.werkstueck["type"] = farbe
                self._wechsel("NFC_RAW")
            else:
                self._wechsel("NFC_REJECTED")

        elif z == "NFC_RAW":
            self.zum_nfc()
            self._wechsel("STORE_WP_VGR")

        elif z == "NFC_REJECTED":
            self.zum_nfc_mit_yref()
            self._wechsel("WRONG_COLOR")

        elif z == "WRONG_COLOR":
            self.hinweis = "Farbe nicht erkannt – Werkstück wird abgelegt"
            self.log("Farbe nicht erkannt – Ablage für falsche Werkstücke")
            self.falsch_ablegen()
            self._wechsel("IDLE")

        elif z == "STORE_WP_VGR":
            self._pruefe_hbw_fehler()
            self._an_hbw("behaelter_holen", self.werkstueck)
            self.hinweis = f"Bringe Werkstück {self.werkstueck['type']} zum Hochregallager"
            self.zum_hbw()
            self._wechsel("STORE_WP")

        elif z == "STORE_WP":
            self.hinweis = "Warte, bis das Hochregallager den leeren Behälter bereitgestellt hat"
            self._pruefe_hbw_fehler()
            if self._hole("hbw_fetched") is not None:
                self.loslassen()
                self._an_hbw("einlagern", self.werkstueck)
                self.log(f"Werkstück {self.werkstueck['type']} an Hochregallager übergeben")
                self.referenzfahrt()
                self._wechsel("IDLE")

        # --- Auslagern: Hochregallager -> Ausgabe --------------------------
        elif z == "FETCH_WP_VGR":
            self._pruefe_hbw_fehler()
            wp = {"id": "", "type": self.bestellung["type"], "state": "RAW"}
            self._an_hbw("auslagern", wp)
            self.hinweis = f"Hochregallager lagert {wp['type']} aus"
            self.zum_hbw()
            self._wechsel("VGR_WAIT_FETCHED")

        elif z == "VGR_WAIT_FETCHED":
            self._pruefe_hbw_fehler()
            f = self._hole("hbw_fetched")
            if f is not None:
                self.werkstueck = f.get("werkstueck") or {"id": "", "type": self.bestellung["type"], "state": "RAW"}
                self.bestell_werkstueck = dict(self.werkstueck)   # C++: reqWP_MPO
                self.von_hbw_greifen()
                self._an_hbw("behaelter_zurueck", self.werkstueck)
                self.bestellung["state"] = "IN_PROCESS"
                if self.bearbeitung_aktiv():
                    self._wechsel("MOVE_VGR2MPO")         # wie Original: weiter zur Bearbeitungsstation
                else:
                    self._wechsel("MOVE_PICKUP_WAIT")     # Bestellweg "direkt"

        # --- Bestellung über Bearbeitungsstation und Sortierstrecke --------
        elif z == "MOVE_VGR2MPO":
            self._pruefe_fehler(self.mpo, "Bearbeitungsstation")
            self.hinweis = f"Bringe {self.bestell_werkstueck['type']} zur Bearbeitungsstation"
            self.zur_bearbeitung()
            self.gegriffen = False
            self.log("-> MPO: produzieren")
            ok, meldung = self.mpo.anfordern("produzieren", quelle="vgr", werkstueck=dict(self.bestell_werkstueck))
            if not ok:
                raise AchsenFehler(f"Bearbeitungsstation nimmt den Auftrag nicht an: {meldung}")
            self.referenzfahrt()
            self._wechsel("START_PRODUCE")

        elif z == "START_PRODUCE":
            self.hinweis = "Warte, bis die Bearbeitungsstation das Werkstück übernommen hat"
            self._pruefe_fehler(self.mpo, "Bearbeitungsstation")
            if self._hole("mpo_gestartet") is not None:
                self.log("Bearbeitungsstation hat das Werkstück übernommen")
                self._wechsel("IDLE")     # wie Original: Greifer ist frei, bis die Sortierung meldet

        elif z == "NFC_PRODUCED":
            # C++: grip(), moveRefYNFC(), NFC beschreiben – hier ohne NFC
            self.hinweis = f"Hole {self.bestell_werkstueck['type']} aus der Ablage der Sortierstrecke"
            self.greifen()
            self.zum_nfc_mit_yref()
            self.bestell_werkstueck["state"] = "PROCESSED"
            self.werkstueck = dict(self.bestell_werkstueck)
            self._wechsel("MOVE_PICKUP_WAIT")

        elif z == "MOVE_PICKUP_WAIT":
            if self.ausgabe_frei():
                self.hinweis = "Werkstück wird in die Ausgabe gelegt"
                self.ausgabe_ablegen()
                self.bestellung["state"] = "SHIPPED"
                self.log(f"Bestellung {self.bestellung['type']} ausgeliefert")
                time.sleep(2)
                self._wechsel("MOVE_PICKUP")
            elif not self.hinweis.startswith("Ausgabe belegt"):
                self.hinweis = "Ausgabe belegt – bitte das Werkstück aus der Ausgabe nehmen"
                self.log(self.hinweis)

        elif z == "MOVE_PICKUP":
            self.bestellung = {"type": "NONE", "state": "WAITING_FOR_ORDER"}
            self._wechsel("IDLE")

        # --- Kalibrierung der Positionen -----------------------------------
        elif z == "CALIB_VGR":
            self._setze_status(False, "CALIB")
            self.kalib_punkt = None
            self.hinweis = "Kalibrierung: Punkt wählen"
            self._wechsel("CALIB_VGR_MOVE")

        elif z == "CALIB_VGR_MOVE":
            self._kalibrier_schritt()

        # --- Kalibrierung des Farbsensors ----------------------------------
        elif z == "CALIB_DPS":
            self._setze_status(False, "CALIB")
            self.referenzfahrt()
            self.zum_farbsensor(halb=True)
            self._setze_status(False, "CALIB")
            self.farb_messungen = {}
            self.hinweis = "Farbkalibrierung: Werkstück unter den Farbsensor legen und Messwert übernehmen"
            self._wechsel("CALIB_DPS_NEXT")

        elif z == "CALIB_DPS_NEXT":
            self._farbkalib_schritt()

    def _pruefe_hbw_fehler(self):
        """Ergänzung zum Original: geht das HBW während der Übergabe in FAULT,
        wartet der Greifer nicht endlos, sondern meldet ebenfalls einen Fehler."""
        if self.hbw is not None and self.hbw.zustand == "FAULT":
            from .achsen import AchsenFehler
            raise AchsenFehler("Hochregallager meldet einen Fehler – Übergabe abgebrochen")

    def _idle_schritt(self):
        # Sortierstrecke meldet ein fertiges Werkstück (C++: reqSLDsorted, hat Vorrang)
        s = self._hole("sld_sortiert")
        if s is not None:
            farbe = (s.get("werkstueck") or {}).get("type", "NONE")
            if self.bestellung["state"] != "IN_PROCESS":
                self.log(f"Sortierstrecke meldet {farbe}, aber keine Bestellung ist unterwegs – bleibt in der Ablage")
            elif farbe not in ABLAGE_PUNKT:
                raise AchsenFehler(f"Sortierstrecke meldet unbekannte Farbe '{farbe}'")
            else:
                if farbe != self.bestell_werkstueck["type"]:
                    self.log(f"Hinweis: bestellt war {self.bestell_werkstueck['type']}, sortiert wurde {farbe}")
                self.bestell_werkstueck["type"] = farbe
                self._setze_status(True, "BUSY")
                self.zur_ablage(farbe)
                self._wechsel("NFC_PRODUCED")
            return
        b = self._hole("bestellen")
        if b is not None:
            self.bestellung = {"type": b["farbe"], "state": "ORDERED"}
            self.log(f"Bestellung: {b['farbe']}")
            self._wechsel("FETCH_WP_VGR")
            return
        if self._hole("kalibrieren") is not None:
            self._wechsel("CALIB_VGR")
            return
        if self._hole("farbkalibrieren") is not None:
            self._wechsel("CALIB_DPS")
            return
        if self._hole("referenzfahrt") is not None:
            self.referenzfahrt()
            return
        hand = self._hole("hand")
        if hand is not None:
            self._handbetrieb(hand)
            return

        # Werkstück in der Eingabe? (C++: else if (!dps.is_DIN()) -> START_DELIVERY)
        manuell = self._hole("annehmen") is not None
        if self.eingabe_belegt():
            if self._eingabe_seit is None:
                self._eingabe_seit = time.time()
            stabil = time.time() - self._eingabe_seit >= cfg.DPS_ENTPRELLZEIT_S
            if manuell or (self.auto_annahme and stabil):
                grund = self.hbw_bereit_fuer("einlagern")
                if grund:
                    if self.hinweis != grund:
                        self.hinweis = grund
                        self.log(f"Werkstück in der Eingabe wartet: {grund}")
                else:
                    self.log("Werkstück in der Eingabe erkannt – Einlagerung startet")
                    self._wechsel("START_DELIVERY")
        else:
            self._eingabe_seit = None
            if self.hinweis and not self.hinweis.startswith("Farbe"):
                self.hinweis = ""

    def _kalibrier_schritt(self):
        if self._hole("kalib_ende") is not None:
            self.log("Kalibrierung beendet")
            self.kalib_punkt = None
            self._wechsel("IDLE")
            return
        w = self._hole("kalib_waehle")
        if w is not None:
            self.kalib_punkt = w["punkt"]
            self.fahre_kalib_punkt(self.kalib_punkt)
            self._setze_status(False, "CALIB")
            self.hinweis = f"{KALIB_PUNKTE[self.kalib_punkt][1]} angefahren – justieren und speichern"
            self.log(self.hinweis)
            return
        j = self._hole("kalib_jog")
        if j is not None:
            achse = {"x": self.achse_x, "y": self.achse_y, "z": self.achse_z}[j.get("achse", "x")]
            achse.fahre_rel(int(j.get("schritte", 0)))
            return
        if self._hole("kalib_speichern") is not None and self.kalib_punkt:
            self._kalib_speichern(self.kalib_punkt)
            # wie Original: Punkt wird zur Kontrolle neu angefahren
            self.fahre_kalib_punkt(self.kalib_punkt)
            self._setze_status(False, "CALIB")

    def _farbkalib_schritt(self):
        if self._hole("farbkalib_ende") is not None:
            self.log("Farbkalibrierung beendet")
            self._wechsel("IDLE")
            return
        m = self._hole("farbe_messen")
        if m is not None:
            mv, _ = self.farbe_messen()
            self.farb_messungen[m["farbe"]] = mv
            self.log(f"Farbsensor {m['farbe']}: {mv} mV")
            return
        if self._hole("farbkalib_speichern") is not None:
            w = [self.farb_messungen.get(f, -1) for f in ("WHITE", "RED", "BLUE")]
            if min(w) <= 0:
                self.log("Farbkalibrierung: bitte zuerst alle drei Farben messen")
                return
            th1, th2 = (w[0] + w[1]) // 2, (w[1] + w[2]) // 2
            # gleiche Plausibilitätsprüfung wie das Original
            if w[0] < w[1] < w[2] and th1 >= 200 and th2 < 2000:
                self.farbkalib.th1, self.farbkalib.th2 = th1, th2
                self.farbkalib.speichern()
                self.log(f"Farbschwellen gespeichert: weiß < {th1} mV <= rot < {th2} mV <= blau")
                self._wechsel("IDLE")
            else:
                self.log(f"Messwerte nicht plausibel (weiß {w[0]}, rot {w[1]}, blau {w[2]} mV) – nicht gespeichert")

    # --- Handbetrieb (nur im Dashboard) -----------------------------------
    def _handbetrieb(self, h):
        aktion = h.get("aktion")
        self.log(f"Handbetrieb: {aktion} {h.get('punkt', '')}{h.get('schritte', '')}")
        self._setze_status(True, "BUSY")
        try:
            if aktion in ("x", "y", "z"):
                achse = {"x": self.achse_x, "y": self.achse_y, "z": self.achse_z}[aktion]
                achse.fahre_rel(int(h.get("schritte", 0)))
            elif aktion == "punkt" and h.get("punkt") in self.kalib.pos:
                self.achse_y.referenzfahrt()          # zuerst hoch, damit nichts anstößt
                self.fahre(h["punkt"])
            elif aktion == "greifen":
                self.greifen()
            elif aktion == "loslassen":
                self.loslassen()
        finally:
            self._setze_status(False, "READY")

    # =======================================================================
    # Zustand für das Dashboard
    # =======================================================================
    def zustandsbericht(self):
        b = self.basis_bericht()
        try:
            eingaenge = {f"I{n}": self.hw.eingang(n) for n in range(1, 9)}
            mv = self.hw.spannung(cfg.DPS_FARBSENSOR)
            ausgabe_frei = self.ausgabe_frei()
        except Exception:
            eingaenge, mv, ausgabe_frei = {}, None, None
        b.update({
            "x": {"pos": self.achse_x.pos, "status": self.achse_x.status, "ende": cfg.VGR_ENDE_X},
            "y": {"pos": self.achse_y.pos, "status": self.achse_y.status, "ende": cfg.VGR_ENDE_Y},
            "z": {"pos": self.achse_z.pos, "status": self.achse_z.status, "ende": cfg.VGR_ENDE_Z},
            "eingaenge": eingaenge,
            "eingabe_belegt": eingaenge.get(f"I{cfg.DPS_LICHTSCHRANKE_EINGABE}", 1) == 0,
            "ausgabe_frei": ausgabe_frei,
            "farbwert": mv,
            "farbe_live": self.farbkalib.farbe(mv) if mv is not None else "NONE",
            "farbschwellen": {"th1": self.farbkalib.th1, "th2": self.farbkalib.th2},
            "letzter_farbwert": self.letzter_farbwert,
            "farb_messungen": self.farb_messungen,
            "gegriffen": self.gegriffen,
            "werkstueck": self.werkstueck,
            "bestellung": self.bestellung,
            "auto_annahme": self.auto_annahme,
            "hinweis": self.hinweis,
            "kalib_punkt": self.kalib_punkt,
            "kalib_punkte": {k: v[1] for k, v in KALIB_PUNKTE.items()},
            "positionen": self.kalib.als_dict(),
            "sim": hasattr(self.hw, "sim_werkstueck_einlegen"),
            "bestell_werkstueck": self.bestell_werkstueck,
            "bestellweg": "bearbeitung" if self.bearbeitung_aktiv() else "direkt",
            "bearbeitung_verfuegbar": self.mpo is not None and self.sld is not None,
        })
        return b
