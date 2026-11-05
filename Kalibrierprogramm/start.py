#!/usr/bin/env python3
"""
Kalibrierprogramm (RBG-Einlernen) – Grundhöhen des Regalbediengeräts (Hochregallager) an der
Anlage anfahren und speichern. Eigenständiges Werkzeug, unabhängig vom
Hauptprogramm: es benutzt eine eigene Kopie der Hardwareanbindung (Ordner rbg/)
und schreibt nur in positionen.json in diesem Ordner.

Modell – es gibt nur zwei Höhen je Platz:
    Grundhöhe  G  wird eingelernt: genau auf Lagerplatzhöhe, der ausgefahrene
                  Ausleger liegt am Behälter an, hebt ihn aber noch nicht.
    Hebehöhe   H  = G - HUB (40 Impulse höher; kleinere Y-Werte = höher).
Eingelernt wird je Fach (A1 … C3) und für die Bandübergabe (CV): X und G.

Abläufe (Aufnehmen immer G -> H, Abstellen immer H -> G):
    Auslagern  Fach -> Band   Fach: G anfahren, aus, hoch auf H, ein
                              Band: H anfahren, aus, runter auf G, Band raus, ein
    Einlagern  Band -> Fach   Band: G anfahren, aus, Band rein, hoch auf H, ein
                              Fach: H anfahren, aus, runter auf G, ein

Aufruf:
    python3 start.py              an der Anlage (HBW-TXT 192.168.0.12)
    python3 start.py --sim        ohne Anlage ausprobieren
    Optionen: --ip, --port 8010, --kein-browser, --zeitfaktor 3 (nur --sim)

Vorher das Hauptprogramm (Hauptprogramm/start.py) beenden – sonst steuern zwei Programme
gleichzeitig denselben TXT.
"""

import argparse
import datetime
import json
import os
import queue
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)

from rbg import konfiguration as cfg                       # noqa: E402
from rbg.achsen import AchsenFehler, EndschalterAchse, Foerderband, ReferenzAchse  # noqa: E402
from rbg.hardware import HardwareFehler, SimHardware, TxtHardware                  # noqa: E402

POSITIONEN_DATEI = os.path.join(HIER, "positionen.json")
HTML_DATEI = os.path.join(HIER, "einlernen.html")
FAECHER = [z + s for z in "ABC" for s in "123"]             # A1 A2 A3 B1 … C3
ALLE = FAECHER + ["CV"]
HUB = 40                     # Hebehöhe = Grundhöhe - HUB (fest vorgegeben)
TOLERANZ = 3                 # ab hier gilt "steht auf der Position"


def jetzt():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ===========================================================================
# Positionsdatei
# ===========================================================================
class Positionen:
    """positionen.json – je Fach und für das Band: x, grundhoehe, Zeitpunkt des Einlernens."""

    def __init__(self, datei=POSITIONEN_DATEI):
        self.datei = datei
        self.lock = threading.Lock()
        self.hub = HUB
        self.daten = {name: self._standard(name) for name in ALLE}
        self.hinweis = None
        self.laden()

    @staticmethod
    def _standard(name):
        """Startwert aus dem bisherigen Raster (Spalte x Zeile) der Konfiguration."""
        k = cfg.STANDARD_KALIBRIERUNG
        if name == "CV":
            return {"x": int(k["conv"]["x"]), "grundhoehe": int(k["conv"]["y"]), "eingelernt": None}
        return {"x": int(k["hbx"][int(name[1]) - 1]), "grundhoehe": int(k["hby"]["ABC".index(name[0])]),
                "eingelernt": None}

    def laden(self):
        if not os.path.exists(self.datei):
            self.speichern()
            return
        try:
            with open(self.datei, encoding="utf-8") as f:
                d = json.load(f)
            alt = "grundhoehe" not in json.dumps(d)
            for name, p in d.get("positionen", {}).items():
                if name not in self.daten:
                    continue
                if alt:
                    # altes Format (x, y) aus der ersten Version: Werte waren durch den
                    # Zählfehler verfälscht -> als Startwert behalten, aber neu einlernen
                    self.daten[name] = {"x": int(p["x"]), "grundhoehe": int(p["y"]), "eingelernt": None}
                else:
                    self.daten[name] = {"x": int(p["x"]), "grundhoehe": int(p["grundhoehe"]),
                                        "eingelernt": p.get("eingelernt")}
            if alt:
                self.hinweis = ("Alte positionen.json übernommen: Werte bleiben als Startwert, "
                                "alle Plätze müssen neu eingelernt werden.")
                self.speichern()
        except (OSError, ValueError, KeyError, TypeError) as e:
            print(f"positionen.json unlesbar ({e}) – starte mit Standardwerten, Datei bleibt unverändert.")

    def speichern(self):
        inhalt = {
            "beschreibung": "Positionen des Regalbediengeräts (Encoder-Impulse ab Referenztaster), eingelernt "
                            "mit dem Kalibrierprogramm. A1 = Zeile A (oben), Spalte 1; CV = Förderband. "
                            "grundhoehe = Lagerplatzhöhe; Hebehöhe = grundhoehe - hub (kleiner = höher).",
            "aktualisiert": jetzt(),
            "hub": self.hub,
            "positionen": {n: self.daten[n] for n in ALLE},
        }
        tmp = self.datei + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(inhalt, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.datei)

    def setze(self, name, x, g):
        with self.lock:
            self.daten[name] = {"x": int(x), "grundhoehe": int(g), "eingelernt": jetzt()}
            self.speichern()

    def zuruecksetzen(self, name):
        with self.lock:
            self.daten[name] = self._standard(name)
            self.speichern()

    def grund(self, name):
        return self.daten[name]["grundhoehe"]

    def hebe(self, name):
        return self.daten[name]["grundhoehe"] - self.hub

    def als_dict(self):
        return {n: {**self.daten[n], "hebehoehe": self.hebe(n)} for n in ALLE}


# ===========================================================================
# Regalbediengerät
# ===========================================================================
class RBG:
    def __init__(self, hw, modus):
        self.hw = hw
        self.modus = modus
        self.nothalt = threading.Event()
        self.x = ReferenzAchse("X", hw, cfg.MOTOR_X, cfg.REF_X, cfg.ENDE_X, self.nothalt)
        self.y = ReferenzAchse("Y", hw, cfg.MOTOR_Y, cfg.REF_Y, cfg.ENDE_Y, self.nothalt)
        self.z = EndschalterAchse("Z", hw, cfg.MOTOR_Z,
                                  (cfg.TASTER_Z_EINGEFAHREN, cfg.TASTER_Z_AUSGEFAHREN), self.nothalt)
        self.band = Foerderband(hw, cfg.MOTOR_BAND, self.nothalt)
        self.pos = Positionen()
        self.auftraege = queue.Queue()
        self.aktiv = None                 # Name des laufenden Befehls
        self.fehler = None
        self.log_eintraege = []
        self.seit_ref = 0                 # Fahrten seit der letzten Referenz
        self.auto_ref = True              # vor Anfahren/Tests neu referenzieren
        self.kontrolle = None             # Ergebnis der letzten Zählkontrolle
        self._fehler_parallel = None
        if self.pos.hinweis:
            self.log(self.pos.hinweis, "fehler")
        threading.Thread(target=self._arbeiter, daemon=True).start()

    # --- Protokoll ----------------------------------------------------------
    def log(self, text, art="info"):
        self.log_eintraege.append({"zeit": time.strftime("%H:%M:%S"), "text": text, "art": art})
        del self.log_eintraege[:-60]
        print(f"[{time.strftime('%H:%M:%S')}] {text}")

    # --- Zustand ------------------------------------------------------------
    def eingefahren(self):
        return self.z.taster_gedrueckt(0)

    def ausleger(self):
        if self.z.taster_gedrueckt(0):
            return "eingefahren"
        if self.z.taster_gedrueckt(1):
            return "ausgefahren"
        return "dazwischen"

    def referenziert(self):
        return self.x.status != "NOREF" and self.y.status != "NOREF"

    def _wo(self):
        """Steht das RBG auf einer gespeicherten Position? -> ("B2", "grund"/"hebe") oder None."""
        for n in ALLE:
            if abs(self.pos.daten[n]["x"] - self.x.pos) <= TOLERANZ:
                if abs(self.pos.grund(n) - self.y.pos) <= TOLERANZ:
                    return [n, "grund"]
                if abs(self.pos.hebe(n) - self.y.pos) <= TOLERANZ:
                    return [n, "hebe"]
        return None

    def bericht(self):
        return {
            "modus": self.modus,
            "x": self.x.pos, "y": self.y.pos, "ausleger": self.ausleger(),
            "ende_x": cfg.ENDE_X, "ende_y": cfg.ENDE_Y,
            "referenziert": self.referenziert(),
            "beschaeftigt": self.aktiv is not None, "aktiv": self.aktiv,
            "fehler": self.fehler, "hier": self._wo(),
            "lichtschranke_aussen": self.hw.eingang(cfg.LICHTSCHRANKE_AUSSEN),
            "lichtschranke_innen": self.hw.eingang(cfg.LICHTSCHRANKE_INNEN),
            "hub": self.pos.hub,
            "seit_ref": self.seit_ref, "auto_ref": self.auto_ref, "kontrolle": self.kontrolle,
            "positionen": self.pos.als_dict(),
            "datei": os.path.basename(self.pos.datei),
            "log": self.log_eintraege[-25:],
            "zeit": time.strftime("%H:%M:%S"),
        }

    # --- Befehle annehmen ---------------------------------------------------
    BEWEGUNG = {"ref", "kontrolle", "jog", "abs", "aus", "ein", "gehe",
                "auslagern", "einlagern", "alle_testen", "band_rein", "band_raus"}
    OHNE_REFERENZ = {"ref", "kontrolle", "aus", "ein", "band_rein", "band_raus"}

    def befehl(self, b, d):
        """Wird vom Webserver aufgerufen. Rückgabe (ok, meldung)."""
        if b == "stop":
            self.nothalt.set()
            self.hw.alles_aus()
            while not self.auftraege.empty():
                try:
                    self.auftraege.get_nowait()
                except queue.Empty:
                    break
            self.log("STOPP – alle Motoren aus", "fehler")
            if self.aktiv is None:
                self.nothalt.clear()
            return True, "Gestoppt"
        if b == "quittieren":
            self.fehler = None
            return True, "Quittiert"
        if b == "merke":
            name = self._name(d)
            if self.aktiv is not None:
                return False, "Erst warten, bis die Bewegung fertig ist"
            if not self.referenziert():
                return False, "Keine Referenz – Position wäre ungültig. Erst Referenzfahrt."
            self.pos.setze(name, self.x.pos, self.y.pos)
            self.log(f"{name} gespeichert: X {self.x.pos}, Grundhöhe {self.y.pos} "
                     f"(Hebehöhe {self.y.pos - self.pos.hub})", "ok")
            return True, f"{name} gespeichert"
        if b == "setze":
            name = self._name(d)
            x, g = int(d["x"]), int(d["grundhoehe"])
            if not (0 <= x < cfg.ENDE_X and self.pos.hub <= g < cfg.ENDE_Y):
                return False, f"Werte außerhalb X 0…{cfg.ENDE_X - 1} / Grundhöhe {self.pos.hub}…{cfg.ENDE_Y - 1}"
            self.pos.setze(name, x, g)
            self.log(f"{name} von Hand gesetzt: X {x}, Grundhöhe {g}", "ok")
            return True, f"{name} gesetzt"
        if b == "zuruecksetzen":
            name = self._name(d)
            self.pos.zuruecksetzen(name)
            self.log(f"{name} auf Startwert zurückgesetzt")
            return True, f"{name} zurückgesetzt"
        if b == "auto_ref":
            self.auto_ref = bool(d.get("wert"))
            self.log("Vor dem Anfahren referenzieren: " + ("an" if self.auto_ref else "aus"))
            return True, "Einstellung übernommen"
        if b in self.BEWEGUNG:
            if self.aktiv is not None or not self.auftraege.empty():
                return False, "RBG fährt noch – kurz warten"
            if b not in self.OHNE_REFERENZ and not self.referenziert():
                return False, "Keine Referenz – zuerst Referenzfahrt"
            if self.fehler:
                return False, "Erst Fehler quittieren"
            if b in ("gehe", "auslagern", "einlagern"):
                name = self._name(d)
                if b != "gehe" and name == "CV":
                    return False, "Ein-/Auslagern-Test ist für ein Fach – das Band ist immer dabei"
            self.auftraege.put((b, d))
            return True, "angenommen"
        return False, f"Unbekannter Befehl '{b}'"

    @staticmethod
    def _name(d):
        name = str(d.get("pos", "")).upper()
        if name not in ALLE:
            raise ValueError(f"Unbekannte Position '{name}'")
        return name

    # --- Ausführung ---------------------------------------------------------
    def _arbeiter(self):
        while True:
            b, d = self.auftraege.get()
            self.aktiv = b
            try:
                getattr(self, "_" + b)(d)
            except AchsenFehler as e:
                self.hw.alles_aus()
                if self.nothalt.is_set():
                    self.log(f"Abgebrochen: {e}", "fehler")
                else:
                    self.fehler = str(e)
                    self.log(f"Fehler: {e}", "fehler")
            except (ValueError, KeyError) as e:
                self.log(f"Ungültig: {e}", "fehler")
            except Exception as e:                       # noqa: BLE001 – nie den Thread verlieren
                self.hw.alles_aus()
                self.fehler = repr(e)
                self.log(f"Unerwarteter Fehler: {e!r}", "fehler")
            finally:
                self.aktiv = None
                self.nothalt.clear()

    def _parallel(self, f1, f2):
        """X und Y gleichzeitig fahren; Fehler der zweiten Achse weiterreichen."""
        def sicher():
            try:
                f2()
            except AchsenFehler as e:
                self._fehler_parallel = e
        t = threading.Thread(target=sicher)
        t.start()
        f1()
        t.join()
        if self._fehler_parallel:
            e, self._fehler_parallel = self._fehler_parallel, None
            raise e

    def _ein_wenn_noetig(self):
        if not self.eingefahren():
            self.z.einfahren()

    def _fahre_xy(self, x, y):
        self._ein_wenn_noetig()                          # nie mit ausgefahrenem Ausleger quer fahren
        self.seit_ref += 1
        self._parallel(lambda: self.y.fahre_abs(y), lambda: self.x.fahre_abs(x))

    def _ref(self, d):
        self.log("Referenzfahrt …")
        self.z.einfahren()
        self._parallel(self.y.referenzfahrt, self.x.referenzfahrt)
        self.seit_ref = 0
        self.log("Referenzfahrt fertig – X 0, Y 0", "ok")

    def _ref_wenn_noetig(self):
        if self.auto_ref and self.seit_ref > 0:
            self._ref({})

    def _kontrolle(self, d):
        """Zählkontrolle: zählen, wie viele Impulse es von der aktuellen Position
        bis zum Referenztaster wirklich sind. Weicht das von der angezeigten
        Position ab, hat die Achse unterwegs Impulse verloren. Danach ist die
        Achse neu referenziert und fährt zurück an dieselbe Stelle."""
        self._ein_wenn_noetig()
        ziel_x, ziel_y = self.x.pos, self.y.pos
        ergebnis = {}
        for achse in (self.y, self.x):
            angezeigt = achse.pos
            achse._start_pruefen()
            achse._v_fahrt = None                    # wie die Referenzfahrt: volle Leistung
            self.hw.zaehler_reset(achse.motor)
            self.hw.motor_strecke(achse.motor, 0)
            achse._links()
            start = time.time()
            while not achse.taster_gedrueckt():
                if achse._pruefe_stop():
                    raise AchsenFehler(f"{achse.name}: Kontrolle abgebrochen")
                if time.time() - start > cfg.TIMEOUT_REFERENZ_S:
                    achse._aus()
                    raise AchsenFehler(f"{achse.name}: Referenztaster nicht erreicht")
                time.sleep(cfg.ZYKLUS_S)
            achse._aus()
            gezaehlt = achse._zaehler_ruhig()
            self.hw.zaehler_reset(achse.motor)
            achse.pos, achse._richtung, achse._gelesen, achse.status = 0, 0, None, "READY"
            ergebnis[achse.name.lower()] = angezeigt - gezaehlt
        self.kontrolle = {"x": ergebnis["x"], "y": ergebnis["y"], "fahrten": self.seit_ref,
                          "zeit": time.strftime("%H:%M:%S")}
        self.log(f"Zählkontrolle nach {self.seit_ref} Fahrten: X {ergebnis['x']:+d}, Y {ergebnis['y']:+d} "
                 "Impulse (0 = alles korrekt gezählt). Jetzt neu referenziert.",
                 "ok" if max(abs(ergebnis["x"]), abs(ergebnis["y"])) <= 5 else "fehler")
        self.seit_ref = 0
        self._parallel(lambda: self.y.fahre_abs(ziel_y), lambda: self.x.fahre_abs(ziel_x))

    def _jog(self, d):
        achse = self.x if d["achse"] == "x" else self.y
        n = int(d["schritte"])
        if achse is self.x and not self.eingefahren():
            raise ValueError("X nur mit eingefahrenem Ausleger bewegen")
        if achse is self.y and not self.eingefahren() and abs(n) > 2 * self.pos.hub:
            raise ValueError(f"Ausleger ausgefahren – Y nur in Schritten bis {2 * self.pos.hub}")
        self.seit_ref += 1
        achse.fahre_rel(n)

    def _abs(self, d):
        x, y = int(d.get("x", self.x.pos)), int(d.get("y", self.y.pos))
        if not self.eingefahren() and (x != self.x.pos or abs(y - self.y.pos) > 2 * self.pos.hub):
            raise ValueError("Ausleger ausgefahren – erst einfahren")
        self.seit_ref += 1
        if x != self.x.pos:
            self.x.fahre_abs(x)
        self.y.fahre_abs(y)

    def _aus(self, d):
        self.z.ausfahren()

    def _ein(self, d):
        self.z.einfahren()

    def _band_rein(self, d):
        self.band.rein()

    def _band_raus(self, d):
        self.band.raus()

    def _gehe(self, d):
        name = self._name(d)
        hebe = d.get("hoehe") == "hebe"
        y = self.pos.hebe(name) if hebe else self.pos.grund(name)
        self._ref_wenn_noetig()
        self.log(f"Fahre zu {name}, {'Hebehöhe' if hebe else 'Grundhöhe'} (X {self.pos.daten[name]['x']}, Y {y})")
        self._fahre_xy(self.pos.daten[name]["x"], y)

    # --- Abläufe: Aufnehmen G -> H, Abstellen H -> G ------------------------
    def _aufnehmen(self, name):
        x, g, h = self.pos.daten[name]["x"], self.pos.grund(name), self.pos.hebe(name)
        self._fahre_xy(x, g)                             # Grundhöhe anfahren
        self.z.ausfahren()
        if name == "CV":
            self.band.rein()                             # Behälter ans Ende des Auslegers ziehen
        self.y.fahre_abs(h)                              # auf Hebehöhe: Behälter angehoben
        self.z.einfahren()

    def _abstellen(self, name):
        x, g, h = self.pos.daten[name]["x"], self.pos.grund(name), self.pos.hebe(name)
        self._fahre_xy(x, h)                             # mit Behälter auf Hebehöhe anfahren
        self.z.ausfahren()
        self.y.fahre_abs(g)                              # auf Grundhöhe absenken: Behälter steht
        if name == "CV":
            self.band.raus()
        self.z.einfahren()

    def _auslagern(self, d):
        name = self._name(d)
        self._ref_wenn_noetig()
        self.log(f"Auslagern-Test {name} → Band (Band muss leer sein)")
        self._aufnehmen(name)
        self._abstellen("CV")
        self.log(f"Auslagern-Test {name} fertig – Behälter steht auf dem Band", "ok")

    def _einlagern(self, d):
        name = self._name(d)
        self._ref_wenn_noetig()
        self.log(f"Einlagern-Test Band → {name} (Behälter muss auf dem Band stehen, Fach leer)")
        self._aufnehmen("CV")
        self._abstellen(name)
        self.log(f"Einlagern-Test {name} fertig – Behälter steht im Fach", "ok")

    def _alle_testen(self, d):
        for name in FAECHER:
            self._ref_wenn_noetig()
            self.log(f"Test {name}: auslagern und wieder einlagern …")
            self._aufnehmen(name)
            self._abstellen("CV")
            time.sleep(0.5)
            self._aufnehmen("CV")
            self._abstellen(name)
        self.log("Alle 9 Fächer aus- und wieder eingelagert", "ok")


# ===========================================================================
# Webserver
# ===========================================================================
def erstelle_server(rbg, port):
    class Handler(BaseHTTPRequestHandler):
        def _antwort(self, code, daten, typ="application/json; charset=utf-8"):
            body = daten if isinstance(daten, bytes) else json.dumps(daten).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", typ)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                with open(HTML_DATEI, "rb") as f:
                    self._antwort(200, f.read(), "text/html; charset=utf-8")
            elif self.path == "/api/status":
                self._antwort(200, rbg.bericht())
            elif self.path == "/positionen.json":
                with open(POSITIONEN_DATEI, "rb") as f:
                    self._antwort(200, f.read())
            else:
                self._antwort(404, {"fehler": "nicht gefunden"})

        def do_POST(self):
            if self.path != "/api/befehl":
                self._antwort(404, {"fehler": "nicht gefunden"})
                return
            try:
                laenge = int(self.headers.get("Content-Length", 0))
                d = json.loads(self.rfile.read(laenge) or b"{}")
                ok, meldung = rbg.befehl(d.pop("befehl"), d)
            except (ValueError, KeyError, TypeError) as e:
                ok, meldung = False, str(e)
            self._antwort(200, {"ok": ok, "meldung": meldung})

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ip", default=cfg.TXT_IP, help="IP des Hochregallager-TXT (Standard %(default)s)")
    p.add_argument("--sim", action="store_true", help="ohne Anlage (Simulation)")
    p.add_argument("--zeitfaktor", type=float, default=1.0, help="nur --sim: schneller laufen lassen")
    p.add_argument("--port", type=int, default=8010)
    p.add_argument("--kein-browser", action="store_true")
    args = p.parse_args()

    if args.sim:
        hw, modus = SimHardware(zeitfaktor=args.zeitfaktor, profil="hbw"), "Simulation"
    else:
        print(f"Verbinde mit dem Hochregallager-TXT unter {args.ip}:{cfg.TXT_PORT} …")
        try:
            hw, modus = TxtHardware(args.ip), f"Anlage {args.ip}"
        except HardwareFehler as e:
            sys.exit(f"FEHLER: {e}")
    rbg = RBG(hw, modus)
    rbg.log(f"Verbunden ({modus}). Positionen: {POSITIONEN_DATEI}")
    rbg.befehl("ref", {})

    server = erstelle_server(rbg, args.port)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"\nEinlern-Dashboard: {url}\nBeenden mit Strg+C\n")
    if not args.kein_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        rbg.nothalt.set()
        hw.trennen()
        print("Motoren aus, Verbindung getrennt.")


if __name__ == "__main__":
    main()
