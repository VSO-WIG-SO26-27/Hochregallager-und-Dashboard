"""
Lagerverwaltung (LVS), Einstellungen und Kalibrierdaten.

  TxtHighBayWarehouseStorage.cpp    ->  Lager (erweitert: 3 Platzzustände, Förderband,
                                        Reservierung/Buchung, Klärfall)
  TxtHighBayWarehouseCalibData.cpp  ->  Kalibrierung

Lager- und Kalibrierdatei sind mit dem Original-TXT kompatibel
(Data/Config.HBW.Storage.json und Data/Calib.HBW.json): Der Teil "Storage"
hat das Originalformat, zusätzliche Angaben stehen in eigenen Schlüsseln.
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


PLAETZE = [platz_name(x, y) for y in range(3) for x in range(3)]     # A1, A2, A3, B1, ... C3
BAND = "BAND"                                                        # Übergabeplatz am Förderband

# Platzzustände (wie in einem Lagerverwaltungssystem)
BELEGT = "BELEGT"     # Behälter mit Werkstück
LEER = "LEER"         # leerer Behälter
FREI = "FREI"         # kein Behälter auf dem Platz (Behälter unterwegs bzw. am Band)

FARBEN = ("WHITE", "RED", "BLUE")


def _wp_aus_datei(w):
    return None if w is None else {
        "id": str(w.get("tag_uid", "")),
        "type": TYPEN[int(w.get("type", 0))],
        "state": ZUSTAENDE[int(w.get("state", 0))],
    }


def _wp_in_datei(w):
    return None if w is None else {
        "tag_uid": w.get("id", ""),
        "state": ZUSTAENDE.index(w.get("state", "RAW")),
        "type": TYPEN.index(w.get("type", "NONE")),
    }


class LagerFehler(Exception):
    """Buchung nicht möglich (z. B. Zielplatz nicht frei)."""


# ===========================================================================
class Lager:
    """Lagerverwaltung (LVS) für 3x3 Regalplätze plus den Übergabeplatz am Förderband.

    Jeder Regalplatz hat einen von drei Zuständen:
      BELEGT  Behälter mit Werkstück
      LEER    leerer Behälter
      FREI    kein Behälter – er ist gerade unterwegs oder steht am Förderband

    Bewegungen laufen immer in zwei Schritten (wie in einem echten LVS):
      1. beginne()      Transport anlegen, Quelle und Ziel RESERVIEREN, Datei speichern
      2. abschliessen() Fahrt ist quittiert -> endgültige BUCHUNG, Reservierung löschen
    Bricht eine Fahrt ab (Fehler, Not-Halt, Programmende), bleibt der Transport
    stehen. Beim nächsten Start bzw. nach dem Fehler entsteht daraus ein KLÄRFALL:
    Die Belegung muss am Regal geprüft und im Dashboard bestätigt werden.
    """

    def __init__(self, dateiname="lager.json"):
        os.makedirs(DATEN_ORDNER, exist_ok=True)
        self.datei = os.path.join(DATEN_ORDNER, dateiname)
        self._lock = threading.RLock()
        self.behaelter = [[True] * 3 for _ in range(3)]   # behaelter[x][y]: steht ein Behälter auf dem Platz?
        self.wp = [[None] * 3 for _ in range(3)]          # wp[x][y]: Werkstück im Behälter (oder None)
        self.band = None                                  # None = kein Behälter am Band, sonst {"wp": ...}
        self.transport = None                             # laufender (noch nicht gebuchter) Transport
        self.klaerfall = ""                               # Text, wenn die Belegung geprüft werden muss
        self.beobachter = []                              # f(lager) bei jeder Änderung
        if not self.laden():
            self.zuruecksetzen()

    # --- Datei -------------------------------------------------------------
    # Hinweis: Das Original speichert die Datei mit vertauschten Indizes
    # (Dateischlüssel "A2" = wp[0][1]), zeigt im Dashboard aber "A2" = wp[1][0].
    # Damit Dateien vom TXT kompatibel bleiben, wird das hier genauso gemacht.
    # "Storage" ist das Originalformat; "Behaelter", "Band" und "Transport" sind neu
    # (das Original-Programm ignoriert sie).
    @staticmethod
    def _datei_schluessel(x, y):
        return "ABC"[x] + str(y + 1)

    def laden(self):
        if not os.path.exists(self.datei):
            return False
        try:
            with open(self.datei, encoding="utf-8") as f:
                daten = json.load(f)
            speicher = daten["Storage"]
            behaelter = daten.get("Behaelter", {})
            with self._lock:
                for x in range(3):
                    for y in range(3):
                        k = self._datei_schluessel(x, y)
                        self.wp[x][y] = _wp_aus_datei(speicher.get(k))
                        self.behaelter[x][y] = bool(behaelter.get(k, True))
                        if not self.behaelter[x][y]:
                            self.wp[x][y] = None
                band = daten.get("Band")
                self.band = None if band is None else {"wp": _wp_aus_datei(band.get("wp"))}
                self.transport = daten.get("Transport")
                if self.transport:
                    t = self.transport
                    self.klaerfall = (f"Der Transport {t['von']} → {t['nach']} wurde nicht abgeschlossen "
                                      "(Programmende oder Fehler während der Fahrt). Bitte Quelle, Ziel und "
                                      "Förderband am Regal prüfen, die Belegung ggf. korrigieren und bestätigen.")
            return True
        except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
            print(f"Lagerdatei {self.datei} unlesbar ({e}) – Lager wird zurückgesetzt")
            return False

    def speichern(self):
        with self._lock:
            speicher, behaelter = {}, {}
            for x in range(3):
                for y in range(3):
                    k = self._datei_schluessel(x, y)
                    speicher[k] = _wp_in_datei(self.wp[x][y])
                    behaelter[k] = self.behaelter[x][y]
            daten = {"Storage": speicher, "Behaelter": behaelter,
                     "Band": None if self.band is None else {"wp": _wp_in_datei(self.band["wp"])},
                     "Transport": self.transport}
        tmp = self.datei + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(daten, f, indent=1)
        os.replace(tmp, self.datei)          # atomar – Datei ist nie halb geschrieben

    def zuruecksetzen(self):
        """Alle 9 Plätze mit leerem Behälter, Förderband leer."""
        with self._lock:
            self.behaelter = [[True] * 3 for _ in range(3)]
            self.wp = [[None] * 3 for _ in range(3)]
            self.band = None
            self.transport = None
            self.klaerfall = ""
        self.speichern()
        self._geaendert()

    def _geaendert(self):
        for f in list(self.beobachter):
            try:
                f(self)
            except Exception as e:
                print("Beobachter-Fehler:", e)

    # --- Abfragen ----------------------------------------------------------
    def zustand(self, name):
        if name == BAND:
            with self._lock:
                if self.band is None:
                    return FREI
                return BELEGT if self.band["wp"] else LEER
        x, y = platz_koordinaten(name)
        with self._lock:
            if not self.behaelter[x][y]:
                return FREI
            return BELEGT if self.wp[x][y] else LEER

    def werkstueck(self, name):
        with self._lock:
            if name == BAND:
                return dict(self.band["wp"]) if self.band and self.band["wp"] else None
            x, y = platz_koordinaten(name)
            return dict(self.wp[x][y]) if self.wp[x][y] else None

    def belegung(self, name):
        """Kurzform für die Lagerstrategie: 'FREI', 'LEER' oder die Farbe des Werkstücks."""
        z = self.zustand(name)
        return self.werkstueck(name)["type"] if z == BELEGT else z

    def snapshot(self):
        """Belegung aller Plätze inkl. Band als dict (für Lagerstrategie und Dashboard)."""
        with self._lock:
            return {p: self.belegung(p) for p in PLAETZE + [BAND]}

    def reservierung(self, name):
        t = self.transport
        if not t:
            return ""
        if name == t["von"]:
            return "Quelle"
        if name == t["nach"]:
            return "Ziel"
        return ""

    def plaetze(self):
        """Ausführliche Belegung für das Dashboard."""
        with self._lock:
            return {p: {"zustand": self.zustand(p), "wp": self.werkstueck(p), "reserviert": self.reservierung(p)}
                    for p in PLAETZE + [BAND]}

    def bestand(self):
        """Kompatibel zum Original (MQTT f/i/stock): Platz -> Werkstück oder None."""
        with self._lock:
            return {p: self.werkstueck(p) for p in PLAETZE}

    def anzahl(self, typ, mit_band=True):
        with self._lock:
            n = sum(1 for p in PLAETZE if self.belegung(p) == typ)
            if mit_band and self.belegung(BAND) == typ:
                n += 1
            return n

    def farbe_einlagerbar(self, typ):
        """C++: canColorBeStored – max. 3 Werkstücke pro Farbe (inkl. Werkstück am Band)."""
        return self.anzahl(typ) < cfg.MAX_JE_FARBE

    def hat_leeren_behaelter(self):
        """Leerer Behälter im Regal oder schon am Band bereitgestellt?"""
        return self.zustand(BAND) == LEER or any(self.zustand(p) == LEER for p in PLAETZE)

    def hat_farbe(self, typ):
        return self.anzahl(typ, mit_band=False) > 0

    # --- Transporte: reservieren -> buchen ----------------------------------
    def beginne(self, von, nach):
        """Transport anlegen und Quelle/Ziel reservieren (wird sofort gespeichert)."""
        with self._lock:
            if self.transport:
                raise LagerFehler(f"Es läuft bereits ein Transport {self.transport['von']} → {self.transport['nach']}")
            if self.klaerfall:
                raise LagerFehler("Klärfall offen – zuerst Belegung prüfen und bestätigen")
            if von == nach:
                raise LagerFehler("Quelle und Ziel sind gleich")
            if self.zustand(von) == FREI:
                raise LagerFehler(f"Auf {von} steht kein Behälter")
            if self.zustand(nach) != FREI:
                raise LagerFehler(f"Ziel {nach} ist nicht frei ({self.zustand(nach)})")
            wp = self.werkstueck(von)
            self.transport = {"von": von, "nach": nach, "wp": wp,
                              "art": "Auslagern" if nach == BAND else ("Einlagern" if von == BAND else "Umlagern")}
        self.speichern()
        self._geaendert()

    def abschliessen(self):
        """Fahrt quittiert: Behälter (mit Inhalt) von der Quelle auf das Ziel buchen."""
        with self._lock:
            t = self.transport
            if not t:
                raise LagerFehler("Kein Transport offen")
            wp = None
            if t["von"] == BAND:
                wp = self.band["wp"]
                self.band = None
            else:
                x, y = platz_koordinaten(t["von"])
                wp = self.wp[x][y]
                self.wp[x][y], self.behaelter[x][y] = None, False
            if t["nach"] == BAND:
                self.band = {"wp": wp}
            else:
                x, y = platz_koordinaten(t["nach"])
                self.wp[x][y], self.behaelter[x][y] = wp, True
            self.transport = None
        self.speichern()
        self._geaendert()
        return t

    def klaerfall_melden(self, grund=""):
        """Nach einem Fehler während einer Fahrt aufrufen."""
        with self._lock:
            if self.transport and not self.klaerfall:
                t = self.transport
                self.klaerfall = (f"Fahrt {t['von']} → {t['nach']} abgebrochen{': ' + grund if grund else ''}. "
                                  "Bitte Quelle, Ziel und Förderband am Regal prüfen, die Belegung ggf. "
                                  "korrigieren und bestätigen.")
        self._geaendert()

    def klaerfall_bestaetigen(self):
        """Bediener hat die Belegung geprüft – offener Transport wird verworfen."""
        with self._lock:
            self.transport = None
            self.klaerfall = ""
        self.speichern()
        self._geaendert()

    # --- Buchungen am Förderband (Übergabe mit dem Greifer) -----------------
    def band_ablage(self, werkstueck):
        """Greifer hat ein Werkstück in den leeren Behälter am Band gelegt."""
        with self._lock:
            if self.zustand(BAND) != LEER:
                raise LagerFehler(f"Am Band steht kein leerer Behälter ({self.zustand(BAND)})")
            self.band["wp"] = dict(werkstueck)
        self.speichern()
        self._geaendert()

    def band_entnahme(self):
        """Greifer hat das Werkstück aus dem Behälter am Band genommen – Behälter bleibt leer stehen."""
        with self._lock:
            if self.zustand(BAND) != BELEGT:
                raise LagerFehler("Am Band steht kein Behälter mit Werkstück")
            wp = self.band["wp"]
            self.band["wp"] = None
        self.speichern()
        self._geaendert()
        return wp

    # --- Manuelle Korrektur (Inventur) -------------------------------------
    @staticmethod
    def _inhalt(wert):
        if wert in FARBEN:
            return {"id": "", "type": wert, "state": "RAW"}
        return None

    def platz_setzen(self, name, wert):
        """wert: 'FREI' (kein Behälter), 'LEER' oder eine Farbe."""
        x, y = platz_koordinaten(name)
        with self._lock:
            self.behaelter[x][y] = wert != FREI
            self.wp[x][y] = self._inhalt(wert)
        self.speichern()
        self._geaendert()

    def band_setzen(self, wert):
        """wert: 'FREI' (kein Behälter am Band), 'LEER' oder eine Farbe."""
        with self._lock:
            self.band = None if wert == FREI else {"wp": self._inhalt(wert)}
        self.speichern()
        self._geaendert()

    def behaelter_anzahl(self):
        with self._lock:
            return sum(1 for p in PLAETZE if self.zustand(p) != FREI) + (0 if self.band is None else 1)

    def als_text(self):
        z = {FREI: ".", LEER: "_", "WHITE": "W", "RED": "R", "BLUE": "B", "NONE": "!"}
        s = self.snapshot()
        return "\n".join("".join(z[s["ABC"[y] + str(x + 1)]] for x in range(3)) for y in range(3)) + \
            f"\nBand: {z[s[BAND]]}"


# ===========================================================================
class Einstellungen:
    """Betriebsparameter, die im Dashboard geändert werden können (daten/einstellungen.json)."""

    STANDARD = {
        "auto_annahme": True,         # Werkstück in der Eingabe automatisch annehmen
        "auto_umlagern": True,        # bei Stillstand selbstständig umlagern (ABC)
        "umlagern_nach_s": 20,        # so lange muss die Anlage stillstehen
        "vorrang": "EIN",             # "EIN" = Einlagerung vor Auslagerung, "AUS" = umgekehrt
        "abc_fenster": 20,            # ABC-Analyse über die letzten N Bestellungen
        "abc_min_bestellungen": 3,    # erst ab so vielen Bestellungen umlagern
    }

    def __init__(self, dateiname="einstellungen.json"):
        os.makedirs(DATEN_ORDNER, exist_ok=True)
        self.datei = os.path.join(DATEN_ORDNER, dateiname)
        self._lock = threading.Lock()
        self.werte = dict(self.STANDARD)
        try:
            with open(self.datei, encoding="utf-8") as f:
                gelesen = json.load(f)
            self.werte.update({k: v for k, v in gelesen.items() if k in self.STANDARD})
        except (OSError, ValueError):
            self.speichern()

    def __getitem__(self, name):
        return self.werte[name]

    def setze(self, name, wert):
        if name not in self.STANDARD:
            raise KeyError(name)
        typ = type(self.STANDARD[name])
        if typ is bool:
            wert = wert in (True, 1, "1", "true", "an", "on")
        elif typ is int:
            wert = int(wert)
        elif name == "vorrang" and wert not in ("EIN", "AUS"):
            raise ValueError("vorrang muss EIN oder AUS sein")
        with self._lock:
            self.werte[name] = wert
        self.speichern()
        return wert

    def speichern(self):
        with open(self.datei, "w", encoding="utf-8") as f:
            json.dump(self.werte, f, indent=1)

    def als_dict(self):
        return dict(self.werte)


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
