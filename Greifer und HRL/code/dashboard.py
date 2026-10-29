"""
Kleiner Webserver für das Dashboard (nur Python-Standardbibliothek).

  GET  /                -> dashboard.html
  GET  /api/status      -> Zustand aller Stationen + Warteschlange als JSON
  GET  /api/kennzahlen  -> Kennzahlen (Spielzeiten, Wartezeiten, Durchsatz, ...)
  POST /api/befehl      -> {"station": "hbw"|"vgr"|"lvs"|"alle", "befehl": "...", ...}
"""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HTML_DATEI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")


def erstelle_server(stationen, port, mqtt=None, lvs=None):
    """stationen: dict, z. B. {"hbw": Hochregallager, "vgr": Sauggreifer}
    lvs: Auftragsverwaltung (Warteschlange), falls der Greifer läuft"""
    hbw = stationen["hbw"]

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
                bericht = {name: s.zustandsbericht() for name, s in stationen.items()}
                bericht["lvs"] = None if lvs is None else lvs.zustandsbericht()
                bericht["mqtt"] = None if mqtt is None else mqtt.verbunden
                bericht["zeit"] = time.strftime("%H:%M:%S")
                self._antwort(200, bericht)
            elif self.path == "/api/kennzahlen":
                b = hbw.kpi.bericht(hbw.lager)
                b["abc"] = hbw.strategie.bericht(hbw.lager.snapshot())
                self._antwort(200, b)
            else:
                self._antwort(404, {"fehler": "nicht gefunden"})

        def do_POST(self):
            if self.path != "/api/befehl":
                self._antwort(404, {"fehler": "nicht gefunden"})
                return
            try:
                laenge = int(self.headers.get("Content-Length", 0))
                daten = json.loads(self.rfile.read(laenge) or b"{}")
                befehl = daten.pop("befehl")
                station = daten.pop("station", "hbw")
            except (ValueError, KeyError):
                self._antwort(400, {"ok": False, "meldung": "Ungültige Anfrage"})
                return
            if station == "alle":
                ergebnisse = [s.anfordern(befehl, quelle="dashboard", **dict(daten)) for s in stationen.values()]
                ok = all(e[0] for e in ergebnisse)
                meldung = " · ".join(e[1] for e in ergebnisse)
            elif station == "lvs" and lvs is not None:
                ok, meldung = lvs.anfordern(befehl, quelle="dashboard", **daten)
            elif station in stationen:
                ok, meldung = stationen[station].anfordern(befehl, quelle="dashboard", **daten)
            else:
                ok, meldung = False, f"Station '{station}' ist nicht aktiv"
            self._antwort(200, {"ok": ok, "meldung": meldung})

        def log_message(self, *args):   # keine Zeile pro Abfrage im Terminal
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
