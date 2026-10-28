"""
Hochregallager – Bewegungsabläufe und Ablaufsteuerung (Zustandsautomat).

  TxtHighBayWarehouse.cpp     ->  Bewegungsabläufe (referenzfahrt, hole_aus_regal, ...)
  TxtHighBayWarehouseRun.cpp  ->  Zustandsautomat (_schritt)

Ablauf "Einlagern" (im Original vom Sauggreifer/VGR angestoßen):
  1. behaelter_holen    -> leerer Behälter wird aus dem Regal aufs Band gestellt
  2. (Werkstück wird in den Behälter gelegt)
  3. einlagern(Farbe)   -> Behälter mit Werkstück zurück ins Regal, danach Referenzfahrt

Ablauf "Auslagern":
  1. auslagern(Farbe)   -> Behälter mit Werkstück dieser Farbe aufs Band
  2. (Werkstück wird entnommen)
  3. behaelter_zurueck  -> leerer Behälter zurück ins Regal, danach Referenzfahrt
"""

from . import konfiguration as cfg
from .achsen import EndschalterAchse, Foerderband, ReferenzAchse
from .daten import Kalibrierung, Lager, platz_koordinaten, platz_name
from .station import STATUS_CODES, Station  # noqa: F401  (STATUS_CODES für Abwärtskompatibilität)

# Quittungscodes (TxtHbwAckCode_t)
ACK_EXIT, ACK_FETCHED, ACK_STORED, ACK_CALIB_NAV, ACK_CALIB_END = 0, 1, 2, 3, 4

KALIB_POSITIONEN = ["CV", "A1", "B2", "C3"]   # TxtHbwCalibPos_t


class Hochregallager(Station):
    kuerzel = "HBW"
    # Welche Befehle in welchem Zustand im Dashboard erlaubt sind
    ERLAUBT = {
        "IDLE": {"behaelter_holen", "auslagern", "kalibrieren", "lager_reset", "referenzfahrt",
                 "hand", "platz_setzen"},
        "STORE_WP": {"einlagern"},
        "FETCH_WP_WAIT": {"behaelter_zurueck"},
        "FAULT": {"quittieren", "platz_setzen"},
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
        self.kalib_pos = "CV"
        self.werkstueck = None        # C++: reqVGRwp

    def set_geschwindigkeit(self, v):
        for a in (self.achse_x, self.achse_y, self.achse_z, self.band):
            a.geschwindigkeit = v

    def stop(self):
        super().stop()
        self._quittung(ACK_EXIT, None)

    # =======================================================================
    # Bewegungsabläufe (TxtHighBayWarehouse.cpp)
    # =======================================================================
    def referenzfahrt(self):
        """C++: moveRef – Ausleger einfahren, dann X und Y gleichzeitig zum Referenztaster."""
        self._setze_status(True, "BUSY")
        self.achse_z.einfahren()
        self._parallel(self.achse_x.referenzfahrt, self.achse_y.referenzfahrt)
        self._setze_status(False, "READY")

    def _fahre_xy(self, x, y):
        self._parallel(lambda: self.achse_x.fahre_abs(x), lambda: self.achse_y.fahre_abs(y))

    def fahre_band(self, stop=False):
        """C++: moveConv – zur Übergabeposition am Förderband."""
        if not stop:
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
        self.achse_y.fahre_abs(y - cfg.Y_DELTA)   # anheben
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
        self.achse_y.fahre_abs(y - cfg.Y_DELTA)
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

    # --- Lageraufträge (C++: store / storeContainer / fetch / fetchContainer)
    def _auftrag(self, lager_aktion, bewegung, speichern, referenz):
        self._setze_status(True, "BUSY")
        if not lager_aktion():
            self._setze_status(False, "ERROR")
            return False
        i, j = self.lager.naechster_platz
        self.log(f"  Lagerplatz {platz_name(i, j)}")
        bewegung(i, j)
        self._setze_status(False, "READY")
        if speichern:
            self.lager.speichern()
        if referenz:
            self.referenzfahrt()
        return True

    def einlagern(self, wp):
        def bew(i, j):
            self.hole_vom_band(stop=True)
            self.bringe_ins_regal(i, j)
            self.hw.sim_ereignis("hbw_band", farbe=None)            # nur Simulation
        return self._auftrag(lambda: self.lager.einlagern(wp), bew, True, True)

    def behaelter_zurueck(self):
        def bew(i, j):
            self.hole_vom_band(stop=True)
            self.bringe_ins_regal(i, j)
        return self._auftrag(self.lager.behaelter_zurueck, bew, True, True)

    def auslagern(self, typ):
        def bew(i, j):
            self.hole_aus_regal(i, j)
            self.bringe_aufs_band(stop=True)
            self.hw.sim_ereignis("hbw_band", farbe=typ)             # nur Simulation
        return self._auftrag(lambda: self.lager.auslagern(typ), bew, True, False)

    def behaelter_holen(self):
        def bew(i, j):
            self.hole_aus_regal(i, j)
            self.bringe_aufs_band(stop=True)
        return self._auftrag(self.lager.behaelter_holen, bew, False, False)

    def fahre_kalib_pos(self):
        """C++: moveCalibPos"""
        self.referenzfahrt()
        if self.kalib_pos == "CV":
            self.fahre_band(stop=False)
        else:
            idx = KALIB_POSITIONEN.index(self.kalib_pos) - 1
            self.fahre_platz(idx, idx)

    # =======================================================================
    # Anforderungen
    # =======================================================================
    def _pruefe_dashboard(self, befehl, daten):
        if befehl in ("einlagern", "auslagern"):
            typ = daten.get("farbe")
            if typ not in ("WHITE", "RED", "BLUE"):
                return False, "Bitte Farbe wählen"
            if befehl == "einlagern" and not self.lager.farbe_einlagerbar(typ):
                return False, f"Es sind bereits 3 Werkstücke {typ} eingelagert"
            if befehl == "auslagern" and not self.hat_farbe(typ):
                return False, f"Kein Werkstück {typ} im Lager"
            daten["werkstueck"] = {"id": daten.get("id", ""), "type": typ, "state": "RAW"}
        if befehl == "behaelter_holen" and not self.hat_leeren_behaelter():
            return False, "Kein leerer Behälter im Lager"
        if befehl == "platz_setzen":
            self.lager.platz_setzen(daten["platz"], daten.get("farbe"))
            self.log(f"Lagerplatz {daten['platz']} manuell auf {daten.get('farbe') or 'leer'} gesetzt")
            return None, "Lagerplatz geändert"
        return True, ""

    def _angefordert(self, befehl, daten):
        if daten.get("werkstueck") is not None:
            self.werkstueck = daten["werkstueck"]

    def hat_farbe(self, typ):
        return any(w and w["type"] == typ for w in self.lager.bestand().values())

    def hat_leeren_behaelter(self):
        return any(w is None for w in self.lager.bestand().values())

    # =======================================================================
    # Zustandsautomat (TxtHighBayWarehouseRun.cpp)
    # =======================================================================
    def _bei_eintritt(self, zustand):
        if zustand == "IDLE":
            self.set_geschwindigkeit(cfg.GESCHWINDIGKEIT)
            self.referenzfahrt()
            self._setze_status(False, "READY")
            self.lager._geaendert()          # C++: publishStorage()
        elif zustand == "CALIB_HBW":
            self.referenzfahrt()

    def _schritt(self):
        z = self.zustand

        if z == "FAULT":
            self._fault_schritt()

        elif z == "INIT":
            self.referenzfahrt()
            self._wechsel("IDLE")

        elif z == "IDLE":
            if self._hole("behaelter_holen") is not None:
                self._wechsel("FETCH_CONTAINER")
            elif self._hole("auslagern") is not None:
                self._wechsel("FETCH_WP")
            elif self._hole("kalibrieren") is not None:
                self._wechsel("CALIB_HBW")
            elif self._hole("lager_reset") is not None:
                self.lager.zuruecksetzen()
                self.log("Lager zurückgesetzt (alle Behälter leer)")
            elif self._hole("referenzfahrt") is not None:
                self.referenzfahrt()
            else:
                hand = self._hole("hand")
                if hand is not None:
                    self._handbetrieb(hand)

        elif z == "FETCH_CONTAINER":
            if self.behaelter_holen():
                self._quittung(ACK_FETCHED, self.werkstueck)
                self.log("Leerer Behälter steht am Band – Werkstück einlegen und einlagern")
                self._wechsel("STORE_WP")
            else:
                self.fehler = "Kein leerer Behälter gefunden"
                self._wechsel("FAULT")

        elif z == "STORE_WP":
            if self._hole("einlagern") is not None:
                if self.werkstueck and self.einlagern(self.werkstueck):
                    self.log(f"Werkstück {self.werkstueck['type']} eingelagert")
                    self._wechsel("IDLE")
                else:
                    self.fehler = "Einlagern nicht möglich"
                    self._wechsel("FAULT")

        elif z == "FETCH_WP":
            if self.werkstueck and self.auslagern(self.werkstueck["type"]):
                self._quittung(ACK_FETCHED, self.werkstueck)
                self.log("Werkstück steht am Band – entnehmen und Behälter zurücklagern")
                self._wechsel("FETCH_WP_WAIT")
            else:
                self.fehler = "Kein passendes Werkstück im Lager"
                self._wechsel("FAULT")

        elif z == "FETCH_WP_WAIT":
            if self._hole("behaelter_zurueck") is not None:
                self._wechsel("STORE_CONTAINER")

        elif z == "STORE_CONTAINER":
            if self.behaelter_zurueck():
                self.log("Leerer Behälter zurückgelagert")
                self._wechsel("IDLE")
            else:
                self.fehler = "Behälter konnte nicht zurückgelagert werden"
                self._wechsel("FAULT")

        elif z == "CALIB_HBW":
            self._setze_status(False, "CALIB")
            self.kalib_pos = "CV"
            self._wechsel("CALIB_HBW_NAV")

        elif z == "CALIB_HBW_NAV":
            # Im Original wird hier sofort die gewählte Position angefahren
            self.fahre_kalib_pos()
            self._setze_status(False, "CALIB")
            self._quittung(ACK_CALIB_NAV, None)
            self.log(f"Kalibrierposition {self.kalib_pos} angefahren – mit Pfeilen justieren und speichern")
            self._wechsel("CALIB_HBW_MOVE")

        elif z == "CALIB_HBW_MOVE":
            self._kalibrier_schritt()

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
        np = self.lager.naechster_platz
        b = self.basis_bericht()
        b.update({
            "x": {"pos": self.achse_x.pos, "status": self.achse_x.status, "ende": cfg.ENDE_X},
            "y": {"pos": self.achse_y.pos, "status": self.achse_y.status, "ende": cfg.ENDE_Y},
            "z": {"eingefahren": eingaenge.get(f"I{cfg.TASTER_Z_EINGEFAHREN}", 0),
                  "ausgefahren": eingaenge.get(f"I{cfg.TASTER_Z_AUSGEFAHREN}", 0),
                  "status": self.achse_z.status},
            "eingaenge": eingaenge,
            "lager": self.lager.bestand(),
            "naechster_platz": platz_name(*np) if np else None,
            "werkstueck": self.werkstueck,
            "kalibrierung": self.kalib.als_dict(),
            "kalib_pos": self.kalib_pos,
        })
        return b
