"""
Hochregallager – Bewegungsabläufe und Ablaufsteuerung (Zustandsautomat).

  TxtHighBayWarehouse.cpp     ->  Bewegungsabläufe (hole_aus_regal, bringe_aufs_band, ...)
  TxtHighBayWarehouseRun.cpp  ->  Zustandsautomat (_schritt)

Änderungen gegenüber dem Original (Anforderungen des Teams):
  * Referenzfahrt (Position 0/0) nur beim Start – und nach einem Fehler nur dann,
    wenn eine Achse ihre Referenz verloren hat. Nach jeder Aktion bleibt das
    Regalbediengerät (RBG) stehen; die Position ist Ausgangspunkt der nächsten Fahrt.
  * Nach einer Auslagerung bleibt der leere Behälter am Förderband stehen:
      - kommt danach eine Einlagerung, wird er direkt wieder befüllt (spart zwei Fahrten)
      - kommt danach eine Auslagerung, wird er zuerst zurück ins Regal gestellt
  * Lagerplätze nach ABC-Zuordnung aus der Bestellhistorie (lagerstrategie.py)
  * Umlagerung automatisch im Stillstand und von Hand über das Dashboard
  * Lagerverwaltung mit Reservierung vor der Fahrt und Buchung nach der Fahrt
  * Kennzahlen je Spiel (kennzahlen.py)

Ablauf "Einlagern" (vom Greifer angestoßen):
  1. behaelter_holen  -> leerer Behälter steht am Band (ggf. sofort, wenn schon da)
  2. (Greifer legt das Werkstück hinein)
  3. einlagern        -> Behälter mit Werkstück auf den ABC-Platz, RBG bleibt dort

Ablauf "Auslagern":
  1. auslagern        -> ggf. leeren Behälter vom Band zurück, dann Werkstück aufs Band
  2. (Greifer nimmt das Werkstück heraus)
  3. entnommen        -> leerer Behälter bleibt am Band stehen
"""

import time

from . import konfiguration as cfg
from .achsen import AchsenFehler, EndschalterAchse, Foerderband, ReferenzAchse
from .daten import (BAND, BELEGT, FARBEN, FREI, LEER, PLAETZE, Einstellungen, Kalibrierung, Lager,
                    LagerFehler, platz_koordinaten)
from .kennzahlen import Kennzahlen
from .lagerstrategie import Strategie
from .station import STATUS_CODES, Station  # noqa: F401  (STATUS_CODES für Abwärtskompatibilität)

# Quittungscodes (TxtHbwAckCode_t)
ACK_EXIT, ACK_FETCHED, ACK_STORED, ACK_CALIB_NAV, ACK_CALIB_END = 0, 1, 2, 3, 4

KALIB_POSITIONEN = ["CV", "A1", "B2", "C3"]   # TxtHbwCalibPos_t

# Befehle, die das Dashboard immer schicken darf (Einstellungen, Auswertung)
IMMER_ERLAUBT = {"einstellung", "kennzahlen_reset"}

UMLAGER_ARTEN = {
    "tauschen": "Tauschen über die Förderstrecke",
    "verschieben": "Auf freien Platz verschieben",
    "band_einlagern": "Behälter vom Band einlagern",
    "abstellen": "Behälter auf dem Band abstellen",
}


class Hochregallager(Station):
    kuerzel = "HBW"
    # Welche Befehle in welchem Zustand im Dashboard erlaubt sind
    ERLAUBT = {
        "IDLE": {"behaelter_holen", "auslagern", "kalibrieren", "lager_reset", "referenzfahrt",
                 "hand", "platz_setzen", "band_setzen", "umlagern", "umlagern_jetzt", "klaerfall_ok"},
        "STORE_WP": {"einlagern", "abbrechen"},
        "FETCH_WP_WAIT": {"entnommen", "abbrechen"},
        "UMLAGERN": {"umlagern_stopp"},
        "FAULT": {"quittieren", "platz_setzen", "band_setzen", "klaerfall_ok"},
        "CALIB_HBW_MOVE": {"kalib_waehle", "kalib_jog", "kalib_speichern", "kalib_ende"},
    }

    def __init__(self, hw):
        super().__init__(hw)
        self.achse_x = ReferenzAchse("X", hw, cfg.MOTOR_X, cfg.REF_X, cfg.ENDE_X, self.nothalt)
        self.achse_y = ReferenzAchse("Y", hw, cfg.MOTOR_Y, cfg.REF_Y, cfg.ENDE_Y, self.nothalt)
        self.achse_z = EndschalterAchse("Z", hw, cfg.MOTOR_Z,
                                        (cfg.TASTER_Z_EINGEFAHREN, cfg.TASTER_Z_AUSGEFAHREN), self.nothalt)
        self.band = Foerderband(hw, cfg.MOTOR_BAND, self.nothalt)
        self.lager = Lager()
        self.kalib = Kalibrierung()
        self.einst = Einstellungen()
        self.kpi = Kennzahlen()
        self.strategie = Strategie(self.kalib, self.einst, self.kpi.bestellhistorie)
        self.kalib_pos = "CV"
        self.werkstueck = None        # C++: reqVGRwp
        self.stillstand = None        # optional f() -> True, wenn Greifer/Aufträge ruhen (für Auto-Umlagern)
        self.ack_zeit = None          # Zeitpunkt der letzten Quittung "Behälter am Band"
        self.taetigkeit = ""          # Klartext für das Dashboard
        self._spiel = None            # laufendes Spiel (Kennzahlen)
        self._plan = []               # Umlagerplan [(von, nach), ...]
        self._plan_art = ""           # "auto" | "manuell"
        self._plan_index = 0
        self._stillstand_seit = None
        self._letzte_planung = 0.0
        self.letzter_transport = ("", "")
        self.transporte_seit_ref = 0
        self._ruhe_seit = None
        if self.lager.klaerfall:
            self.log("KLÄRFALL: " + self.lager.klaerfall)

    def set_geschwindigkeit(self, v):
        for a in (self.achse_x, self.achse_y, self.achse_z, self.band):
            a.geschwindigkeit = v

    def stop(self):
        super().stop()
        self._quittung(ACK_EXIT, None)
        self.kpi.speichern()

    # =======================================================================
    # Bewegungsabläufe (TxtHighBayWarehouse.cpp)
    # =======================================================================
    def referenzfahrt(self):
        """C++: moveRef – Ausleger einfahren, dann X und Y gleichzeitig zum Referenztaster."""
        self._setze_status(True, "BUSY")
        self.taetigkeit = "Referenzfahrt"
        self.achse_z.einfahren()
        self._parallel(self.achse_x.referenzfahrt, self.achse_y.referenzfahrt)
        self.transporte_seit_ref = 0
        self.taetigkeit = ""
        self._setze_status(False, "READY")

    def nachreferenzieren(self):
        """Encoder-Abweichung zurücksetzen: auf 0/0 und zurück an die letzte Position."""
        x, y = self.position()
        self.log(f"Nachreferenzieren nach {self.transporte_seit_ref} Transporten (Encoder-Abgleich)")
        self.referenzfahrt()
        self._setze_status(True, "BUSY")
        self.taetigkeit = "Zurück an die letzte Position"
        self._fahre_xy(max(0, x), max(0, y))
        self.taetigkeit = ""
        self._setze_status(False, "READY")

    def referenz_falls_noetig(self):
        """Nur referenzieren, wenn eine Achse keine gültige Position hat (Start, nach Fehler)."""
        if "NOREF" in (self.achse_x.status, self.achse_y.status):
            self.log("Achse ohne Referenz – Referenzfahrt")
            self.referenzfahrt()

    def _fahre_xy(self, x, y):
        self._parallel(lambda: self.achse_x.fahre_abs(x), lambda: self.achse_y.fahre_abs(y))

    def _steht_am_band(self):
        p = self.kalib.conv
        return abs(self.achse_x.pos - p["x"]) <= 3 and abs(self.achse_y.pos - p["y"]) <= cfg.Y_DELTA + 3

    def position(self):
        return self.achse_x.pos, self.achse_y.pos

    def fahre_band(self, stop=False):
        """C++: moveConv – zur Übergabeposition am Förderband.
        stop=True: Ausleger bleibt ausgefahren – aber nur, wenn das RBG schon am Band
        steht (sonst würde es mit ausgefahrenem Ausleger durchs Regal fahren)."""
        if not stop or not self._steht_am_band():
            self.achse_z.einfahren()
        p = self.kalib.conv
        self._fahre_xy(p["x"], p["y"])
        return p["x"], p["y"]

    def fahre_platz(self, i, j):
        """C++: moveCR – vor Regalplatz Spalte i / Zeile j fahren."""
        self.achse_z.einfahren()
        x, y = self.kalib.hbx[i], self.kalib.hby[j]
        self._fahre_xy(x, y)
        return x, y

    def hole_aus_regal(self, i, j):
        """C++: getCR – Behälter aus dem Regal nehmen."""
        _, y = self.fahre_platz(i, j)
        self.achse_z.ausfahren()
        self.achse_y.fahre_abs(y - cfg.Y_HUB_AUFNEHMEN)   # anheben (etwas mehr als im Original)
        self.achse_z.einfahren()

    def bringe_ins_regal(self, i, j):
        """C++: putCR – Behälter ins Regal stellen."""
        _, y = self.fahre_platz(i, j)
        self.achse_y.fahre_abs(y - cfg.Y_DELTA)   # etwas höher anfahren
        self.achse_z.ausfahren()
        self.achse_y.fahre_abs(y + cfg.Y_DELTA)   # absenken = abstellen
        self.achse_z.einfahren()

    def hole_vom_band(self, stop=False):
        """C++: getConv – Behälter vom Band aufnehmen."""
        _, y = self.fahre_band(stop)
        self.achse_z.ausfahren()
        self.band.rein()
        self.achse_y.fahre_abs(y - cfg.Y_HUB_AUFNEHMEN)
        if not stop:
            self.achse_z.einfahren()

    def bringe_aufs_band(self, stop=False):
        """C++: putConv – Behälter aufs Band stellen und hinausfahren.
        Mit stop=True bleibt der Ausleger ausgefahren (wie im Original)."""
        _, y = self.fahre_band()
        self.achse_z.ausfahren()
        self.achse_y.fahre_abs(y + cfg.Y_DELTA)
        self.band.raus()
        if not stop:
            self.achse_z.einfahren()

    # --- Transport mit Lagerbuchung ----------------------------------------
    def transport(self, von, nach):
        """Einen Behälter bewegen: reservieren -> fahren -> buchen. Gibt die Fahrzeit zurück."""
        self.lager.beginne(von, nach)
        inhalt = self.lager.transport["wp"]
        ort = {BAND: "Förderstrecke"}
        farbe = {"WHITE": "Weiß", "RED": "Rot", "BLUE": "Blau"}.get(inhalt["type"], inhalt["type"]) if inhalt else ""
        self.taetigkeit = (f"{'Behälter mit ' + farbe if inhalt else 'Leerer Behälter'}: "
                           f"{ort.get(von, von)} → {ort.get(nach, nach)}")
        self.log(f"  Transport {von} → {nach} ({inhalt['type'] if inhalt else 'leerer Behälter'})")
        t0 = time.time()
        if von == BAND:
            self.hole_vom_band(stop=True)
        else:
            self.hole_aus_regal(*platz_koordinaten(von))
        if nach == BAND:
            self.bringe_aufs_band(stop=True)
        else:
            self.bringe_ins_regal(*platz_koordinaten(nach))
        self.lager.abschliessen()
        self.transporte_seit_ref += 1
        self.letzter_transport = (von, nach)
        for p in (von, nach):
            self.kpi.zugriff(p)
        self.taetigkeit = ""
        return time.time() - t0

    # =======================================================================
    # Lageraufträge
    # =======================================================================
    def leerbehaelter_bereitstellen(self, farbe):
        """C++: fetchContainer – leerer Behälter ans Band. Steht dort schon einer, sofort fertig."""
        z = self.lager.zustand(BAND)
        if z == LEER:
            self.log("  Leerer Behälter steht schon am Band – keine Fahrt nötig")
            return 0.0
        if z == BELEGT:
            raise AchsenFehler("Am Förderband steht ein Behälter mit Werkstück – erst auslagern/korrigieren")
        quelle = self.strategie.leerbehaelter_quelle(self.lager.snapshot(), farbe, self.position())
        if quelle is None:
            raise AchsenFehler("Kein leerer Behälter im Lager")
        return self.transport(quelle, BAND)

    def behaelter_einlagern(self, wp):
        """C++: store – Behälter mit Werkstück vom Band auf den ABC-Platz."""
        if self.lager.zustand(BAND) == LEER:
            self.lager.band_ablage(wp)       # Greifer hat das Werkstück hineingelegt
        if self.lager.zustand(BAND) != BELEGT:
            raise AchsenFehler("Am Förderband steht kein Behälter mit Werkstück")
        farbe = self.lager.werkstueck(BAND)["type"]
        ziel = self.strategie.einlager_ziel(self.lager.snapshot(), farbe)
        if ziel is None:
            raise AchsenFehler("Kein freier Lagerplatz")
        k = self.strategie.klasse(farbe)
        self.log(f"  Zielplatz {ziel} (Zone {self.strategie.zone(ziel)}"
                 f"{', Farbe Klasse ' + k if k else ''})")
        return self.transport(BAND, ziel)

    def werkstueck_auslagern(self, farbe):
        """C++: fetch – ggf. Leerbehälter vom Band zurück, dann Werkstück aufs Band."""
        dauer = 0.0
        snap = self.lager.snapshot()
        quelle = self.strategie.auslager_quelle(snap, farbe, BAND if snap[BAND] != FREI else self.position())
        if quelle is None:
            raise AchsenFehler(f"Kein Werkstück {farbe} im Lager")
        if snap[BAND] == LEER:
            ziel = self.strategie.leer_rueck_ziel(snap, folge=quelle)
            if ziel is None:
                raise AchsenFehler("Kein freier Platz für den leeren Behälter vom Band")
            self.log(f"  Leerer Behälter vom Band zurück auf {ziel}")
            dauer += self.transport(BAND, ziel)
        elif snap[BAND] == BELEGT:
            raise AchsenFehler("Am Förderband steht noch ein Behälter mit Werkstück")
        dauer += self.transport(quelle, BAND)
        return dauer

    # --- Spiele für die Kennzahlen -----------------------------------------
    def _spiel_start(self, art, farbe=""):
        self._spiel = {"art": art, "farbe": farbe, "t0": time.time(), "netto": 0.0, "von": "", "nach": ""}

    def _spiel_ende(self, von="", nach=""):
        s = self._spiel
        if s:
            self.kpi.spiel(s["art"], s["netto"], time.time() - s["t0"], s["farbe"], von or s["von"], nach or s["nach"])
        self._spiel = None

    # =======================================================================
    # Anforderungen
    # =======================================================================
    def anfordern(self, befehl, quelle="dashboard", **daten):
        if befehl == "behaelter_zurueck":       # alter Name (MQTT-Code 4) – Behälter bleibt jetzt am Band
            befehl = "entnommen"
        if befehl == "einstellung":
            try:
                wert = self.einst.setze(daten.get("name"), daten.get("wert"))
            except (KeyError, ValueError, TypeError) as e:
                return False, f"Ungültige Einstellung: {e}"
            self.log(f"Einstellung {daten.get('name')} = {wert}")
            self._stillstand_seit = None
            return True, "Einstellung übernommen"
        if befehl == "kennzahlen_reset":
            self.kpi.zuruecksetzen()
            self.log("Kennzahlen zurückgesetzt")
            return True, "Kennzahlen zurückgesetzt"
        return super().anfordern(befehl, quelle, **daten)

    def _pruefe_dashboard(self, befehl, daten):
        if befehl in ("einlagern", "auslagern", "behaelter_holen"):
            typ = daten.get("farbe")
            if typ not in FARBEN:
                return False, "Bitte Farbe wählen"
            if befehl == "einlagern" and self.lager.zustand(BAND) == LEER and not self.lager.farbe_einlagerbar(typ):
                return False, f"Es sind bereits {cfg.MAX_JE_FARBE} Werkstücke {typ} eingelagert"
            if befehl == "auslagern" and not self.hat_farbe(typ):
                return False, f"Kein Werkstück {typ} im Lager"
            if befehl == "behaelter_holen" and not self.lager.hat_leeren_behaelter():
                return False, "Kein leerer Behälter im Lager"
            daten["werkstueck"] = {"id": daten.get("id", ""), "type": typ, "state": "RAW"}
        if self.lager.klaerfall and befehl in ("behaelter_holen", "auslagern", "umlagern", "umlagern_jetzt"):
            return False, "Klärfall offen – zuerst Belegung prüfen und bestätigen"
        if befehl == "umlagern":
            ok, text = self._pruefe_umlagerung(daten)
            if not ok:
                return False, text
        if befehl == "platz_setzen":
            if daten.get("platz") not in PLAETZE or daten.get("wert") not in (FREI, LEER) + FARBEN:
                return False, "Ungültiger Platz oder Inhalt"
            self.lager.platz_setzen(daten["platz"], daten["wert"])
            self.log(f"Inventur: Lagerplatz {daten['platz']} = {daten['wert']}")
            return None, "Lagerplatz geändert"
        if befehl == "band_setzen":
            if daten.get("wert") not in (FREI, LEER) + FARBEN:
                return False, "Ungültiger Inhalt"
            self.lager.band_setzen(daten["wert"])
            self.log(f"Inventur: Förderband = {daten['wert']}")
            return None, "Förderband geändert"
        if befehl == "klaerfall_ok":
            if not self.lager.klaerfall:
                return None, "Kein Klärfall offen"
            self.lager.klaerfall_bestaetigen()
            self.log("Klärfall bestätigt – Lagerbestand geprüft")
            return None, "Klärfall bestätigt"
        if befehl == "umlagern_stopp":
            self._plan = self._plan[:self._plan_index + 1]
            self.log("Umlagerung wird nach dem laufenden Zug beendet")
            return None, "Umlagerung wird beendet"
        return True, ""

    def _pruefe_umlagerung(self, daten):
        """Manuelle Umlagerung prüfen und als Plan (Liste von Zügen) hinterlegen."""
        art, a, b = daten.get("art"), daten.get("von"), daten.get("nach")
        z = self.lager.zustand
        if art == "tauschen":
            if a not in PLAETZE or b not in PLAETZE or a == b:
                return False, "Zwei verschiedene Lagerplätze wählen"
            if z(BAND) != FREI:
                return False, "Förderstrecke ist belegt – zuerst den Behälter vom Band einlagern"
            if z(a) == FREI or z(b) == FREI:
                return False, "Beide Plätze müssen einen Behälter haben (sonst 'Verschieben' wählen)"
            plan = [(a, BAND), (b, a), (BAND, b)]
        elif art == "verschieben":
            if a not in PLAETZE or b not in PLAETZE:
                return False, "Lagerplätze wählen"
            if z(a) == FREI:
                return False, f"Auf {a} steht kein Behälter"
            if z(b) != FREI:
                return False, f"{b} ist nicht frei"
            plan = [(a, b)]
        elif art == "band_einlagern":
            if z(BAND) == FREI:
                return False, "Am Förderband steht kein Behälter"
            if b not in PLAETZE or z(b) != FREI:
                return False, "Freien Zielplatz wählen"
            plan = [(BAND, b)]
        elif art == "abstellen":
            if a not in PLAETZE or z(a) == FREI:
                return False, "Platz mit Behälter wählen"
            if z(BAND) != FREI:
                return False, "Förderstrecke ist belegt"
            plan = [(a, BAND)]
        else:
            return False, "Unbekannte Umlagerart"
        daten["plan"] = plan
        return True, ""

    def _angefordert(self, befehl, daten):
        if daten.get("werkstueck") is not None:
            self.werkstueck = daten["werkstueck"]

    def hat_farbe(self, typ):
        return self.lager.hat_farbe(typ)

    def hat_leeren_behaelter(self):
        return self.lager.hat_leeren_behaelter()

    # =======================================================================
    # Zustandsautomat (TxtHighBayWarehouseRun.cpp)
    # =======================================================================
    def _bei_eintritt(self, zustand):
        if zustand == "FAULT":
            if self.lager.transport:
                self.lager.klaerfall_melden(self.fehler)
                self.log("KLÄRFALL: " + self.lager.klaerfall)
            self._spiel = None
            self._plan = []
            self.taetigkeit = ""
        elif zustand == "IDLE":
            self.set_geschwindigkeit(cfg.GESCHWINDIGKEIT)
            self.referenz_falls_noetig()      # kein 0/0 nach Aufträgen – nur ohne gültige Referenz
            self._setze_status(False, "READY")
            self.taetigkeit = ""
            self.lager._geaendert()          # C++: publishStorage()

    def _schritt(self):
        z = self.zustand

        if z == "FAULT":
            self._fault_schritt()

        elif z == "INIT":
            self.referenzfahrt()              # einzige feste Referenzfahrt: beim Start
            self._wechsel("IDLE")

        elif z == "IDLE":
            self._idle_schritt()

        elif z == "FETCH_CONTAINER":
            farbe = (self.werkstueck or {}).get("type", "")
            self._setze_status(True, "BUSY")
            self._spiel_start("Einlagerung", farbe)
            self._spiel["netto"] += self.leerbehaelter_bereitstellen(farbe)
            self._setze_status(False, "READY")
            self.ack_zeit = time.time()
            self.taetigkeit = "Leerer Behälter am Band – wartet auf Werkstück"
            self._quittung(ACK_FETCHED, self.werkstueck)
            self.log("Leerer Behälter steht am Band – Werkstück einlegen und einlagern")
            self._wechsel("STORE_WP")

        elif z == "STORE_WP":
            e = self._hole("einlagern")
            if e is not None:
                wp = e.get("werkstueck") or self.werkstueck
                if not wp or wp.get("type") not in FARBEN:
                    raise AchsenFehler("Einlagern ohne gültiges Werkstück")
                self._setze_status(True, "BUSY")
                if self._spiel is None:
                    self._spiel_start("Einlagerung", wp["type"])
                self._spiel["farbe"] = wp["type"]       # Farbe steht erst nach der Farberkennung fest
                self._spiel["netto"] += self.behaelter_einlagern(wp)
                self._spiel_ende(BAND, self.letzter_transport[1])
                self._quittung(ACK_STORED, wp)
                self.log(f"Werkstück {wp['type']} eingelagert")
                self._wechsel("IDLE")
            elif self._hole("abbrechen") is not None:
                self._spiel = None
                self.log("Einlagerung abgebrochen – leerer Behälter bleibt am Band")
                self._wechsel("IDLE")

        elif z == "FETCH_WP":
            farbe = self.werkstueck["type"] if self.werkstueck else ""
            if farbe not in FARBEN or not self.hat_farbe(farbe):
                self.fehler = f"Kein Werkstück {farbe or '?'} im Lager"
                self._wechsel("FAULT")
                return
            self.kpi.bestellung(farbe)        # Bestellhistorie für die ABC-Analyse
            self._setze_status(True, "BUSY")
            self._spiel_start("Auslagerung", farbe)
            self._spiel["netto"] += self.werkstueck_auslagern(farbe)
            self._spiel["nach"] = BAND
            self._setze_status(False, "READY")
            self.werkstueck = self.lager.werkstueck(BAND) or self.werkstueck
            self.ack_zeit = time.time()
            self.taetigkeit = "Werkstück am Band – wartet auf Entnahme"
            self._quittung(ACK_FETCHED, self.werkstueck)
            self.log("Werkstück steht am Band – bitte entnehmen")
            self._wechsel("FETCH_WP_WAIT")

        elif z == "FETCH_WP_WAIT":
            if self._hole("entnommen") is not None:
                self.lager.band_entnahme()
                self._spiel_ende()
                self.log("Werkstück entnommen – leerer Behälter bleibt am Band")
                self._wechsel("IDLE")
            elif self._hole("abbrechen") is not None:
                self._spiel = None
                self.log("Auslagerung abgebrochen – Behälter mit Werkstück steht am Band")
                self._wechsel("IDLE")

        elif z == "UMLAGERN":
            self._umlager_schritt()

        elif z == "CALIB_HBW":
            self._setze_status(False, "CALIB")
            self.kalib_pos = "CV"
            self._wechsel("CALIB_HBW_NAV")

        elif z == "CALIB_HBW_NAV":
            self.fahre_kalib_pos()
            self._setze_status(False, "CALIB")
            self._quittung(ACK_CALIB_NAV, None)
            self.log(f"Kalibrierposition {self.kalib_pos} angefahren – mit Pfeilen justieren und speichern")
            self._wechsel("CALIB_HBW_MOVE")

        elif z == "CALIB_HBW_MOVE":
            self._kalibrier_schritt()

    def _idle_schritt(self):
        if self._hole("behaelter_holen") is not None:
            self._wechsel("FETCH_CONTAINER")
        elif self._hole("auslagern") is not None:
            self._wechsel("FETCH_WP")
        elif (u := self._hole("umlagern")) is not None:
            self._starte_plan(u["plan"], "manuell")
        elif self._hole("umlagern_jetzt") is not None:
            plan, vorher, nachher = self.strategie.umlager_plan(self.lager.snapshot())
            if plan:
                self.log(f"Umlagerung (von Hand angestoßen): Zonenabweichung {vorher} → {nachher}")
                self._starte_plan(plan, "auto")
            else:
                self.log("Umlagerung: Lager ist bereits optimal belegt"
                         if self.strategie.abc()[2] else "Umlagerung: zu wenig Bestellhistorie für die ABC-Analyse")
        elif self._hole("kalibrieren") is not None:
            self._wechsel("CALIB_HBW")
        elif self._hole("lager_reset") is not None:
            self.lager.zuruecksetzen()
            self.log("Lager zurückgesetzt (alle Plätze mit leerem Behälter, Band leer)")
        elif self._hole("referenzfahrt") is not None:
            self.referenzfahrt()
        elif (hand := self._hole("hand")) is not None:
            self._handbetrieb(hand)
        elif not self._nachreferenz_pruefen():
            self._auto_umlagern_pruefen()

    def _nachreferenz_pruefen(self):
        """In einer Pause nach einigen Transporten nachreferenzieren. True = ist passiert."""
        n = self.transporte_seit_ref
        if n == 0 or self.lager.transport:
            self._ruhe_seit = None
            return False
        ruhe = self._ruhe()
        if ruhe and self._ruhe_seit is None:
            self._ruhe_seit = time.time()
        elif not ruhe:
            self._ruhe_seit = None
        faellig = (n >= cfg.NACHREFERENZ_NACH_TRANSPORTEN and ruhe
                   and time.time() - self._ruhe_seit >= cfg.NACHREFERENZ_RUHE_S)
        if faellig or n >= cfg.NACHREFERENZ_SPAETESTENS:
            self.nachreferenzieren()
            self._ruhe_seit = None
            return True
        return False

    # --- Umlagerung ----------------------------------------------------------
    def _ruhe(self):
        """Stillstand: keine Aufträge, Greifer ruht (falls verbunden), kein Klärfall."""
        if self.lager.klaerfall or self.lager.transport:
            return False
        with self._lock:
            if self._anf:
                return False
        return self.stillstand() if self.stillstand else True

    def _auto_umlagern_pruefen(self):
        if not self.einst["auto_umlagern"] or not self._ruhe():
            self._stillstand_seit = None
            return
        jetzt = time.time()
        if self._stillstand_seit is None:
            self._stillstand_seit = jetzt
        if jetzt - self._stillstand_seit < self.einst["umlagern_nach_s"]:
            return
        if jetzt - self._letzte_planung < cfg.UMLAGERN_PLANUNG_S:
            return
        self._letzte_planung = jetzt
        plan, vorher, nachher = self.strategie.umlager_plan(self.lager.snapshot())
        if plan:
            self.log(f"Stillstand {self.einst['umlagern_nach_s']} s – Umlagerung nach ABC: "
                     f"Zonenabweichung {vorher} → {nachher} in {len(plan)} Zügen")
            self._starte_plan(plan, "auto")

    def _starte_plan(self, plan, art):
        self._plan, self._plan_art, self._plan_index = list(plan), art, 0
        self.log("Umlagerplan: " + ", ".join(f"{a}→{b}" for a, b in plan))
        self._wechsel("UMLAGERN")

    def _umlager_schritt(self):
        if self._plan_index >= len(self._plan):
            self.log("Umlagerung abgeschlossen")
            self._plan = []
            if self._plan_art == "auto" and self._stillstand_seit is not None:
                # weiter optimieren, solange Stillstand herrscht (ohne erneute Wartezeit)
                self._stillstand_seit = time.time() - self.einst["umlagern_nach_s"]
            self._wechsel("IDLE")
            return
        if self._plan_art == "auto" and self._plan_index > 0 and not self._ruhe():
            self.log("Umlagerung unterbrochen – Auftrag hat Vorrang")
            self._plan = []
            self._stillstand_seit = None
            self._wechsel("IDLE")
            return
        von, nach = self._plan[self._plan_index]
        if self.lager.zustand(von) == FREI or self.lager.zustand(nach) != FREI:
            self.log(f"Umlagerzug {von}→{nach} nicht mehr möglich – Plan beendet")
            self._plan = []
            self._wechsel("IDLE")
            return
        self._setze_status(True, "BUSY")
        inhalt = self.lager.belegung(von)
        t0 = time.time()
        try:
            dauer = self.transport(von, nach)
        except LagerFehler as e:
            raise AchsenFehler(str(e))
        self.kpi.spiel("Umlagerung", dauer, time.time() - t0, inhalt if inhalt in FARBEN else "", von, nach)
        self._plan_index += 1
        self._setze_status(False, "READY")

    def fahre_kalib_pos(self):
        """C++: moveCalibPos – ohne Referenzfahrt (nur falls eine Achse keine Referenz hat)."""
        self.referenz_falls_noetig()
        if self.kalib_pos == "CV":
            self.fahre_band(stop=False)
        else:
            idx = KALIB_POSITIONEN.index(self.kalib_pos) - 1
            self.fahre_platz(idx, idx)

    # --- Kalibrierung (ersetzt die Joystick-Bedienung durch Dashboard-Knöpfe)
    def _kalibrier_schritt(self):
        if self._hole("kalib_ende") is not None:
            self._quittung(ACK_CALIB_END, None)
            self.log("Kalibrierung beendet")
            self._wechsel("IDLE")
            return
        w = self._hole("kalib_waehle")
        if w is not None and w.get("pos") in KALIB_POSITIONEN:
            self.kalib_pos = w["pos"]
            self._wechsel("CALIB_HBW_NAV")
            return
        j = self._hole("kalib_jog")
        if j is not None:
            achse = self.achse_x if j.get("achse") == "x" else self.achse_y
            achse.fahre_rel(int(j.get("schritte", 0)))
            return
        if self._hole("kalib_speichern") is not None:
            x, y = self.achse_x.pos, self.achse_y.pos
            if self.kalib_pos == "CV":
                self.kalib.conv = {"x": x, "y": y}
            else:
                idx = KALIB_POSITIONEN.index(self.kalib_pos) - 1
                self.kalib.hbx[idx], self.kalib.hby[idx] = x, y
            self.kalib.speichern()
            self.log(f"Kalibrierung {self.kalib_pos} gespeichert: x={x} y={y}")
            self._wechsel("CALIB_HBW_NAV")   # wie Original: Position wird zur Kontrolle neu angefahren

    def joystick(self, jd):
        """Original-Joystick der Sensorstation (MQTT fl/ssc/joy) im Kalibriermodus."""
        if self.zustand != "CALIB_HBW_MOVE":
            return
        i = KALIB_POSITIONEN.index(self.kalib_pos)
        if jd.get("b2"):
            self.anfordern("kalib_ende", quelle="mqtt")
        elif jd.get("b1"):
            self.anfordern("kalib_speichern", quelle="mqtt")
        elif jd.get("aX2", 0) > 500:
            self.anfordern("kalib_waehle", quelle="mqtt", pos=KALIB_POSITIONEN[(i - 1) % 4])
        elif jd.get("aX2", 0) < -500:
            self.anfordern("kalib_waehle", quelle="mqtt", pos=KALIB_POSITIONEN[(i + 1) % 4])
        else:
            ax, ay = jd.get("aX1", 0), jd.get("aY1", 0)   # C++: moveJoystick
            if abs(ax) > abs(ay):
                self.anfordern("kalib_jog", quelle="mqtt", achse="y", schritte=(1 if ax > 0 else -1) * (abs(ax) // 50))
            elif abs(ay) > abs(ax):
                self.anfordern("kalib_jog", quelle="mqtt", achse="x", schritte=(1 if ay > 0 else -1) * (abs(ay) // 50))

    # --- Handbetrieb (nur im Dashboard, gibt es im Original nicht) ---------
    def _handbetrieb(self, h):
        aktion = h.get("aktion")
        self.log(f"Handbetrieb: {aktion}")
        self._setze_status(True, "BUSY")
        try:
            if aktion == "ausleger_aus":
                self.achse_z.ausfahren()
            elif aktion == "ausleger_ein":
                self.achse_z.einfahren()
            elif aktion == "band_rein":
                self.band.rein()
            elif aktion == "band_raus":
                self.band.raus()
            elif aktion in ("x", "y"):
                self.achse_z.einfahren()
                (self.achse_x if aktion == "x" else self.achse_y).fahre_rel(int(h.get("schritte", 0)))
            elif aktion == "platz":
                i, j = platz_koordinaten(h["platz"])
                self.fahre_platz(i, j)
            elif aktion == "band_pos":
                self.fahre_band()
        finally:
            self._setze_status(False, "READY")

    # =======================================================================
    # Zustand für das Dashboard
    # =======================================================================
    def zustandsbericht(self):
        try:
            eingaenge = {f"I{n}": self.hw.eingang(n) for n in range(1, 9)}
        except Exception:
            eingaenge = {}
        snap = self.lager.snapshot()
        t = self.lager.transport
        b = self.basis_bericht()
        b.update({
            "x": {"pos": self.achse_x.pos, "status": self.achse_x.status, "ende": cfg.ENDE_X},
            "y": {"pos": self.achse_y.pos, "status": self.achse_y.status, "ende": cfg.ENDE_Y},
            "z": {"eingefahren": eingaenge.get(f"I{cfg.TASTER_Z_EINGEFAHREN}", 0),
                  "ausgefahren": eingaenge.get(f"I{cfg.TASTER_Z_AUSGEFAHREN}", 0),
                  "status": self.achse_z.status},
            "eingaenge": eingaenge,
            "lager": self.lager.bestand(),
            "plaetze": self.lager.plaetze(),
            "transport": t,
            "klaerfall": self.lager.klaerfall,
            "behaelter": self.lager.behaelter_anzahl(),
            "strategie": self.strategie.bericht(snap),
            "einstellungen": self.einst.als_dict(),
            "plan": {"zuege": self._plan, "index": self._plan_index, "art": self._plan_art},
            "taetigkeit": self.taetigkeit,
            "transporte_seit_ref": self.transporte_seit_ref,
            "umlager_arten": UMLAGER_ARTEN,
            "werkstueck": self.werkstueck,
            "kalibrierung": self.kalib.als_dict(),
            "kalib_pos": self.kalib_pos,
            "max_je_farbe": cfg.MAX_JE_FARBE,
        })
        return b
