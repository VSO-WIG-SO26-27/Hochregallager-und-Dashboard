"""
Kleiner Webserver für das Dashboard (nur Python-Standardbibliothek).

  GET  /             -> dashboard.html
  GET  /api/status   -> Zustand aller Stationen als JSON
  POST /api/befehl   -> {"station": "hbw"|"vgr"|"mpo"|"sld"|"alle", "befehl": "...", ...}
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HTML_DATEI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")


def erstelle_server(stationen, port, mqtt=None, welt=None, host="127.0.0.1"):
    """stationen: dict, z. B. {"hbw": Hochregallager, "vgr": Sauggreifer, "mpo": ..., "sld": ...}
    welt: SimWelt der Simulation (zeigt im Dashboard, wo die Werkstücke liegen)
    host: "0.0.0.0", damit auch andere Geräte im WLAN das Dashboard öffnen können"""

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
                bericht["mqtt"] = None if mqtt is None else mqtt.verbunden
                bericht["sim_welt"] = None if welt is None else welt.bericht()
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
                station = daten.pop("station", "hbw")
            except (ValueError, KeyError):
                self._antwort(400, {"ok": False, "meldung": "Ungültige Anfrage"})
                return
            if station == "alle":
                ergebnisse = [s.anfordern(befehl, quelle="dashboard", **dict(daten)) for s in stationen.values()]
                ok = all(e[0] for e in ergebnisse)
                meldung = " · ".join(e[1] for e in ergebnisse)
            elif station in stationen:
                ok, meldung = stationen[station].anfordern(befehl, quelle="dashboard", **daten)
            else:
                ok, meldung = False, f"Station '{station}' ist nicht aktiv"
            self._antwort(200, {"ok": ok, "meldung": meldung})

        def log_message(self, *args):   # keine Zeile pro Abfrage im Terminal
            pass

    return ThreadingHTTPServer((host, port), Handler)
