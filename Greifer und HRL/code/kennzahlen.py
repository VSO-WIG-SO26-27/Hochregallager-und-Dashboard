"""
Kennzahlen (KPI) des Hochregallagers – Grundlage für den Vorher/Nachher-Vergleich.

Erfasst werden:
  Spiele          jede Lagerbewegung des Regalbediengeräts (RBG)
                    Einlagerung   = Leerbehälter bereitstellen + Behälter einlagern
                    Auslagerung   = ggf. Leerbehälter zurück + Werkstück aufs Band
                    Umlagerung    = eine Behälterbewegung im Stillstand / von Hand
                  netto  = reine Fahrzeit des RBG (ohne Warten auf den Greifer)
                  brutto = vom Auftrag ans RBG bis zur Buchung (inkl. Warten)
  Wartezeiten     an der Übergabe Förderband: wer musste auf wen warten?
  Durchlaufzeit   Auftrag gestartet -> Werkstück eingelagert bzw. in der Ausgabe
  Bestellungen    Grundlage der ABC-Analyse
  Zugriffe        Ein-/Auslagerungen je Lagerplatz
  Betriebszeit    Laufzeit des Programms (Basis für Durchsatz und Auslastung)

Speicherung: daten/kennzahlen.json (Rohdaten) und daten/spiele.csv (für Excel).
"""

import csv
import json
import os
import threading
import time

from .daten import DATEN_ORDNER, FARBEN, PLAETZE

MAX_EINTRAEGE = 2000
SPIEL_ARTEN = ("Einlagerung", "Auslagerung", "Umlagerung")


def _statistik(werte):
    if not werte:
        return {"anzahl": 0, "mittel": None, "min": None, "max": None}
    return {"anzahl": len(werte), "mittel": round(sum(werte) / len(werte), 1),
            "min": round(min(werte), 1), "max": round(max(werte), 1)}


class Kennzahlen:
    def __init__(self, dateiname="kennzahlen.json"):
        os.makedirs(DATEN_ORDNER, exist_ok=True)
        self.datei = os.path.join(DATEN_ORDNER, dateiname)
        self.csv_datei = os.path.join(DATEN_ORDNER, "spiele.csv")
        self._lock = threading.RLock()
        self._leer()
        self._laden()
        self._t_tick = time.time()
        self._t_gespeichert = 0

    def _leer(self):
        self.d = {"seit": time.strftime("%Y-%m-%d %H:%M"), "betriebszeit_s": 0.0,
                  "spiele": [], "wartezeiten": [], "durchlauf": [], "bestellungen": [],
                  "zugriffe": {p: 0 for p in PLAETZE}}

    def _laden(self):
        try:
            with open(self.datei, encoding="utf-8") as f:
                gelesen = json.load(f)
            for k in self.d:
                if k in gelesen:
                    self.d[k] = gelesen[k]
            for p in PLAETZE:
                self.d["zugriffe"].setdefault(p, 0)
        except (OSError, ValueError):
            pass

    def speichern(self, sofort=True):
        with self._lock:
            self._tick()
            if not sofort and time.time() - self._t_gespeichert < 30:
                return
            for k in ("spiele", "wartezeiten", "durchlauf", "bestellungen"):
                del self.d[k][:-MAX_EINTRAEGE]
            tmp = self.datei + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.d, f)
            os.replace(tmp, self.datei)
            self._t_gespeichert = time.time()

    def _tick(self):
        jetzt = time.time()
        self.d["betriebszeit_s"] += jetzt - self._t_tick
        self._t_tick = jetzt

    # --- Erfassen ----------------------------------------------------------
    def spiel(self, art, netto, brutto, farbe="", von="", nach=""):
        e = {"t": time.time(), "art": art, "netto": round(netto, 2), "brutto": round(brutto, 2),
             "farbe": farbe or "", "von": von, "nach": nach}
        with self._lock:
            self.d["spiele"].append(e)
        try:
            neu = not os.path.exists(self.csv_datei)
            with open(self.csv_datei, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f, delimiter=";")
                if neu:
                    w.writerow(["Zeit", "Art", "Farbe", "von", "nach", "Spielzeit netto [s]", "brutto [s]"])
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e["t"])), art, farbe, von, nach,
                            f"{netto:.2f}".replace(".", ","), f"{brutto:.2f}".replace(".", ",")])
        except OSError:
            pass
        self.speichern()

    def wartezeit(self, art, wer, dauer):
        """art: 'Einlagerung'|'Auslagerung', wer: 'Greifer' (wartet auf HRL) oder 'HRL' (wartet auf Greifer)."""
        with self._lock:
            self.d["wartezeiten"].append({"t": time.time(), "art": art, "wer": wer, "dauer": round(dauer, 2)})
        self.speichern(sofort=False)

    def durchlauf(self, art, dauer):
        with self._lock:
            self.d["durchlauf"].append({"t": time.time(), "art": art, "dauer": round(dauer, 2)})
        self.speichern(sofort=False)

    def bestellung(self, farbe):
        with self._lock:
            self.d["bestellungen"].append({"t": time.time(), "farbe": farbe})
        self.speichern()

    def zugriff(self, platz):
        if platz in PLAETZE:
            with self._lock:
                self.d["zugriffe"][platz] = self.d["zugriffe"].get(platz, 0) + 1

    def bestellhistorie(self, fenster):
        """Anzahl Bestellungen je Farbe in den letzten 'fenster' Bestellungen."""
        with self._lock:
            letzte = self.d["bestellungen"][-int(fenster):] if fenster else []
        z = {f: 0 for f in FARBEN}
        for b in letzte:
            if b["farbe"] in z:
                z[b["farbe"]] += 1
        return z

    def zuruecksetzen(self):
        with self._lock:
            self._leer()
            self._t_tick = time.time()
            try:
                os.remove(self.csv_datei)
            except OSError:
                pass
        self.speichern()

    # --- Auswertung --------------------------------------------------------
    def bericht(self, lager=None):
        with self._lock:
            self._tick()
            d = json.loads(json.dumps(self.d))   # Kopie
        jetzt = time.time()
        bz_h = d["betriebszeit_s"] / 3600

        spiele = {}
        for art in SPIEL_ARTEN:
            liste = [s for s in d["spiele"] if s["art"] == art]
            spiele[art] = {"netto": _statistik([s["netto"] for s in liste]),
                           "brutto": _statistik([s["brutto"] for s in liste]),
                           "verlauf": [s["netto"] for s in liste[-25:]],
                           "letzte": liste[-1] if liste else None}

        warte = {}
        for art in ("Einlagerung", "Auslagerung"):
            for wer in ("Greifer", "HRL"):
                w = [x["dauer"] for x in d["wartezeiten"] if x["art"] == art and x["wer"] == wer]
                warte[f"{art}|{wer}"] = {**_statistik(w), "summe": round(sum(w), 1)}
        alle_w = {wer: [x["dauer"] for x in d["wartezeiten"] if x["wer"] == wer] for wer in ("Greifer", "HRL")}

        durchlauf = {art: _statistik([x["dauer"] for x in d["durchlauf"] if x["art"] == art])
                     for art in ("Einlagerung", "Auslagerung")}

        ein_aus = [s for s in d["spiele"] if s["art"] in ("Einlagerung", "Auslagerung")]
        letzte_h = [s for s in ein_aus if s["t"] >= jetzt - 3600]
        stunden = []
        for i in range(8, 0, -1):          # Durchsatz je Stunde, letzte 8 Stunden
            a, b = jetzt - i * 3600, jetzt - (i - 1) * 3600
            stunden.append({"bis": time.strftime("%H:%M", time.localtime(b)),
                            "ein": sum(1 for s in ein_aus if a <= s["t"] < b and s["art"] == "Einlagerung"),
                            "aus": sum(1 for s in ein_aus if a <= s["t"] < b and s["art"] == "Auslagerung")})
        netto_summe = sum(s["netto"] for s in d["spiele"])

        b = {
            "seit": d["seit"],
            "betriebszeit_h": round(bz_h, 2),
            "spiele": spiele,
            "wartezeiten": warte,
            "wartezeit_gesamt": {wer: {**_statistik(w), "summe": round(sum(w), 1)} for wer, w in alle_w.items()},
            "durchlauf": durchlauf,
            "durchsatz": {
                "letzte_stunde": len(letzte_h),
                "letzte_stunde_ein": sum(1 for s in letzte_h if s["art"] == "Einlagerung"),
                "letzte_stunde_aus": sum(1 for s in letzte_h if s["art"] == "Auslagerung"),
                "mittel_pro_h": round(len(ein_aus) / bz_h, 1) if bz_h > 0.05 else None,
                "gesamt": len(ein_aus),
                "stunden": stunden,
            },
            "auslastung_prozent": round(100 * netto_summe / d["betriebszeit_s"], 1) if d["betriebszeit_s"] > 60 else None,
            "umlagerungen": sum(1 for s in d["spiele"] if s["art"] == "Umlagerung"),
            "zugriffe": d["zugriffe"],
            "bestellungen_gesamt": len(d["bestellungen"]),
        }
        if lager is not None:
            snap = lager.snapshot()
            belegt = sum(1 for p in PLAETZE if snap[p] in FARBEN)
            leer = sum(1 for p in PLAETZE if snap[p] == "LEER")
            b["fuellgrad"] = {
                "prozent": round(100 * belegt / len(PLAETZE)),
                "belegt": belegt, "leer": leer, "frei": len(PLAETZE) - belegt - leer,
                "band": snap["BAND"],
                "je_farbe": {f: sum(1 for p in PLAETZE if snap[p] == f) for f in FARBEN},
                "plaetze": len(PLAETZE),
            }
        return b
