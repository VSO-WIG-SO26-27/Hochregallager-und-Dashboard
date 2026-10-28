"""
Lagerverwaltung und Kalibrierdaten.

  TxtHighBayWarehouseStorage.cpp    ->  Lager
  TxtHighBayWarehouseCalibData.cpp  ->  Kalibrierung

Beide Dateien werden im selben JSON-Format gespeichert wie auf dem Original-TXT
(Data/Config.HBW.Storage.json und Data/Calib.HBW.json). Man kann die Dateien
vom TXT also direkt in den Ordner daten/ kopieren und weiterverwenden.
"""

import json
import os
import threading

from . import konfiguration as cfg

DATEN_ORDNER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "daten")

TYPEN = ["NONE", "WHITE", "RED", "BLUE"]          # TxtWPType_t
ZUSTAENDE = ["RAW", "PROCESSED", "REJECTED"]       # TxtWPState_t


def platz_name(x, y):
    """Lagerplatz-Name wie im Dashboard / MQTT (C++ getStockMap):
    Buchstabe = Zeile (y: A oben .. C unten), Zahl = Spalte (x: 1..3)."""
    return "ABC"[y] + str(x + 1)


def platz_koordinaten(name):
    return int(name[1]) - 1, "ABC".index(name[0].upper())


# ===========================================================================
class Lager:
    """Belegung der 3x3 Regalplätze. Jeder Platz enthält immer einen Behälter;
    gespeichert wird, ob und welches Werkstück darin liegt (None = leerer Behälter)."""

    def __init__(self, dateiname="lager.json"):
        os.makedirs(DATEN_ORDNER, exist_ok=True)
        self.datei = os.path.join(DATEN_ORDNER, dateiname)
        self._lock = threading.RLock()
        self.wp = [[None] * 3 for _ in range(3)]     # wp[x][y]
        self.naechster_platz = None                  # C++: nextFetchPos
        self.beobachter = []                         # Funktionen, die bei Änderung aufgerufen werden
        if not self.laden():
            self.zuruecksetzen()

    # --- Datei -------------------------------------------------------------
    # Hinweis: Das Original speichert die Datei mit vertauschten Indizes
    # (Dateischlüssel "A2" = wp[0][1]), zeigt im Dashboard aber "A2" = wp[1][0].
    # Damit Dateien vom TXT kompatibel bleiben, wird das hier genauso gemacht.
    @staticmethod
    def _datei_schluessel(x, y):
        return "ABC"[x] + str(y + 1)

    def laden(self):
        if not os.path.exists(self.datei):
            return False
        try:
            with open(self.datei, encoding="utf-8") as f:
                speicher = json.load(f)["Storage"]
            with self._lock:
                for x in range(3):
                    for y in range(3):
                        w = speicher.get(self._datei_schluessel(x, y))
                        self.wp[x][y] = None if w is None else {
                            "id": str(w.get("tag_uid", "")),
                            "type": TYPEN[int(w.get("type", 0))],
                            "state": ZUSTAENDE[int(w.get("state", 0))],
                        }
            return True
        except (OSError, ValueError, KeyError, IndexError) as e:
            print(f"Lagerdatei {self.datei} unlesbar ({e}) – Lager wird zurückgesetzt")
            return False

    def speichern(self):
        with self._lock:
            speicher = {}
            for x in range(3):
                for y in range(3):
                    w = self.wp[x][y]
                    speicher[self._datei_schluessel(x, y)] = None if w is None else {
                        "tag_uid": w["id"],
                        "state": ZUSTAENDE.index(w["state"]),
                        "type": TYPEN.index(w["type"]),
                    }
        with open(self.datei, "w", encoding="utf-8") as f:
            json.dump({"Storage": speicher}, f, indent=1)

    def zuruecksetzen(self):
        """C++: resetStorageState – alle Plätze mit leerem Behälter."""
        with self._lock:
            self.wp = [[None] * 3 for _ in range(3)]
        self._geaendert()
        self.speichern()

    def _geaendert(self):
        for f in list(self.beobachter):
            try:
                f(self)
            except Exception as e:
                print("Beobachter-Fehler:", e)

    # --- Lagerlogik (1:1 aus TxtHighBayWarehouseStorage.cpp) --------------
    # Suchreihenfolge im Original: Spalte 1..3, innerhalb der Spalte von
    # unten (Zeile C) nach oben (Zeile A).
    def _suche(self, bedingung):
        for x in range(3):
            for y in (2, 1, 0):
                if bedingung(self.wp[x][y]):
                    return (x, y)
        return None

    def behaelter_holen(self):
        """C++: fetchContainer – Platz mit leerem Behälter suchen."""
        with self._lock:
            self.naechster_platz = self._suche(lambda w: w is None)
            if self.naechster_platz is None:
                return False
        self._geaendert()
        return True

    def einlagern(self, werkstueck):
        """C++: store – Werkstück kommt in den zuvor geholten Behälter."""
        with self._lock:
            if self.naechster_platz is None:
                return False
            x, y = self.naechster_platz
            self.wp[x][y] = dict(werkstueck)
        self._geaendert()
        return True

    def auslagern(self, typ):
        """C++: fetch – erstes Werkstück der Farbe suchen und austragen."""
        with self._lock:
            self.naechster_platz = None
            if typ == "NONE":
                return False
            self.naechster_platz = self._suche(lambda w: w is not None and w["type"] == typ)
            if self.naechster_platz is None:
                return False
            x, y = self.naechster_platz
            self.wp[x][y] = None
        self._geaendert()
        return True

    def behaelter_zurueck(self):
        """C++: storeContainer – leerer Behälter zurück auf seinen Platz."""
        with self._lock:
            if self.naechster_platz is None:
                return False
            x, y = self.naechster_platz
            self.wp[x][y] = None
        self._geaendert()
        return True

    def farbe_einlagerbar(self, typ):
        """C++: canColorBeStored – max. 3 Werkstücke pro Farbe."""
        with self._lock:
            anzahl = sum(1 for x in range(3) for y in range(3)
                         if self.wp[x][y] and self.wp[x][y]["type"] == typ)
        return anzahl < 3

    # --- Für Dashboard -----------------------------------------------------
    def bestand(self):
        with self._lock:
            return {platz_name(x, y): (dict(self.wp[x][y]) if self.wp[x][y] else None)
                    for y in range(3) for x in range(3)}

    def platz_setzen(self, name, typ):
        """Manuelle Korrektur, falls Software und Regal nicht übereinstimmen."""
        x, y = platz_koordinaten(name)
        with self._lock:
            self.wp[x][y] = None if typ in (None, "", "LEER") else {"id": "", "type": typ, "state": "RAW"}
        self._geaendert()
        self.speichern()

    def als_text(self):
        """C++: print() – Belegung als 3x3-Text."""
        z = {None: "_", "WHITE": "W", "RED": "R", "BLUE": "B", "NONE": "!"}
        with self._lock:
            return "\n".join("".join(z[self.wp[x][y]["type"] if self.wp[x][y] else None] for x in range(3))
                             for y in range(3))


# ===========================================================================
class Kalibrierung:
    """Encoder-Positionen der Regalspalten/-zeilen und der Bandübergabe."""

    def __init__(self, dateiname="kalibrierung.json"):
        os.makedirs(DATEN_ORDNER, exist_ok=True)
        self.datei = os.path.join(DATEN_ORDNER, dateiname)
        s = cfg.STANDARD_KALIBRIERUNG
        self.hbx = list(s["hbx"])
        self.hby = list(s["hby"])
        self.conv = dict(s["conv"])
        if not self.laden():
            self.speichern()

    def laden(self):
        if not os.path.exists(self.datei):
            return False
        try:
            with open(self.datei, encoding="utf-8") as f:
                h = json.load(f)["HBW"]
            self.hbx = [int(h["hbx"][k]) for k in ("1", "2", "3")]
            self.hby = [int(h["hby"][k]) for k in ("1", "2", "3")]
            self.conv = {"x": int(h["conv"]["x"]), "y": int(h["conv"]["y"])}
            return True
        except (OSError, ValueError, KeyError) as e:
            print(f"Kalibrierdatei {self.datei} unlesbar ({e}) – Standardwerte werden benutzt")
            return False

    def speichern(self):
        daten = {"HBW": {
            "hbx": {str(i + 1): v for i, v in enumerate(self.hbx)},
            "hby": {str(i + 1): v for i, v in enumerate(self.hby)},
            "conv": dict(self.conv),
        }}
        with open(self.datei, "w", encoding="utf-8") as f:
            json.dump(daten, f, indent=1)

    def als_dict(self):
        return {"hbx": list(self.hbx), "hby": list(self.hby), "conv": dict(self.conv)}


# ===========================================================================
# Sauggreifer (VGR) – TxtVacuumGripperRobotCalibData
# ===========================================================================
class KalibrierungGreifer:
    """Benannte 3D-Positionen des Greifers (x = Drehung, y = Höhe, z = Arm).
    Gespeichert im Format von Data/Calib.VGR.json des Original-TXT."""

    def __init__(self, dateiname="kalibrierung_greifer.json"):
        os.makedirs(DATEN_ORDNER, exist_ok=True)
        self.datei = os.path.join(DATEN_ORDNER, dateiname)
        self.pos = {k: list(v) for k, v in cfg.VGR_STANDARD_POSITIONEN.items()}
        if not self.laden():
            self.speichern()

    def laden(self):
        if not os.path.exists(self.datei):
            return False
        try:
            with open(self.datei, encoding="utf-8") as f:
                liste = json.load(f)["VGR"]["pos3list"]
            for eintrag in liste:
                for name, p in eintrag.items():
                    self.pos[name] = [int(p["x"]), int(p["y"]), int(p["z"])]
            return True
        except (OSError, ValueError, KeyError, TypeError) as e:
            print(f"Greifer-Kalibrierdatei {self.datei} unlesbar ({e}) – Standardwerte werden benutzt")
            return False

    def speichern(self):
        liste = [{name: {"x": p[0], "y": p[1], "z": p[2]}} for name, p in self.pos.items()]
        with open(self.datei, "w", encoding="utf-8") as f:
            json.dump({"VGR": {"pos3list": liste}}, f, indent=1)

    def setze(self, name, xyz):
        self.pos[name] = [int(v) for v in xyz]

    def kopiere(self, quelle, ziel, achse):
        """C++: copyPos3X / copyPos3Z"""
        i = "xyz".index(achse)
        self.pos[ziel][i] = self.pos[quelle][i]

    def als_dict(self):
        return {k: list(v) for k, v in self.pos.items()}


class KalibrierungFarbsensor:
    """Schwellwerte des Farbsensors (C++: TxtDeliveryPickupStationCalibData,
    Datei Data/Calib.DPS.json)."""

    def __init__(self, dateiname="kalibrierung_farbsensor.json"):
        os.makedirs(DATEN_ORDNER, exist_ok=True)
        self.datei = os.path.join(DATEN_ORDNER, dateiname)
        self.th1, self.th2 = cfg.FARBE_TH1, cfg.FARBE_TH2
        if not self.laden():
            self.speichern()

    def laden(self):
        if not os.path.exists(self.datei):
            return False
        try:
            with open(self.datei, encoding="utf-8") as f:
                c = json.load(f)["VGR"]["colorsens"]
            self.th1, self.th2 = int(c["th1"]), int(c["th2"])
            return True
        except (OSError, ValueError, KeyError) as e:
            print(f"Farbsensor-Datei {self.datei} unlesbar ({e}) – Standardwerte werden benutzt")
            return False

    def speichern(self):
        with open(self.datei, "w", encoding="utf-8") as f:
            json.dump({"VGR": {"colorsens": {"th1": self.th1, "th2": self.th2}}}, f, indent=1)

    def farbe(self, mv):
        """C++: TxtDeliveryPickupStation::getLastColor"""
        if 200 <= mv < self.th1:
            return "WHITE"
        if self.th1 <= mv < self.th2:
            return "RED"
        if self.th2 <= mv < 2000:
            return "BLUE"
        return "NONE"
