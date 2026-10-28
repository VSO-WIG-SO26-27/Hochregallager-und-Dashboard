"""
Kleiner Webserver für das Test-Dashboard (nur Python-Standardbibliothek).

  GET  /             -> dashboard.html
  GET  /api/status   -> aktueller Zustand als JSON
  POST /api/befehl   -> {"befehl": "...", ...} an die Ablaufsteuerung
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HTML_DATEI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")


def erstelle_server(hbw, port, mqtt=None):
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
                bericht = hbw.zustandsbericht()
                bericht["mqtt"] = None if mqtt is None else mqtt.verbunden
                self._antwort(200, bericht)
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
            except (ValueError, KeyError):
                self._antwort(400, {"ok": False, "meldung": "Ungültige Anfrage"})
                return
            ok, meldung = hbw.anfordern(befehl, quelle="dashboard", **daten)
            self._antwort(200, {"ok": ok, "meldung": meldung})

        def log_message(self, *args):   # keine Zeile pro Abfrage im Terminal
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
