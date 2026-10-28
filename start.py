#!/usr/bin/env python3
"""
Hochregallager der fischertechnik Lernfabrik 4.0 – Python-Steuerung mit Test-Dashboard.

Start an der Anlage (Mac im Lernfabrik-WLAN):
    python3 start.py

Ohne Anlage (Simulation):
    python3 start.py --sim

Weitere Optionen:
    --ip 192.168.0.12     andere IP des HBW-TXT
    --mqtt                zusätzlich mit dem MQTT-Broker der Lernfabrik verbinden
    --port 8000           Port des Dashboards
    --kein-browser        Browser nicht automatisch öffnen

Beenden: Strg+C im Terminal (alle Motoren werden ausgeschaltet).
"""

import argparse
import sys
import threading
import time
import webbrowser

from hochregallager import konfiguration as cfg
from hochregallager.dashboard import erstelle_server
from hochregallager.hardware import HardwareFehler, SimHardware, TxtHardware
from hochregallager.steuerung import Hochregallager


def main():
    p = argparse.ArgumentParser(description="Hochregallager-Steuerung (Python)")
    p.add_argument("--sim", action="store_true", help="Simulation statt echter Anlage")
    p.add_argument("--ip", default=cfg.TXT_IP, help=f"IP des HBW-TXT (Standard {cfg.TXT_IP})")
    p.add_argument("--port", type=int, default=cfg.WEB_PORT, help="Port des Dashboards")
    p.add_argument("--mqtt", action="store_true", help="MQTT-Anbindung an die Lernfabrik aktivieren")
    p.add_argument("--zeitfaktor", type=float, default=1.0, help="Simulation schneller laufen lassen (z. B. 3)")
    p.add_argument("--kein-browser", action="store_true")
    args = p.parse_args()

    if args.sim:
        hw = SimHardware(args.zeitfaktor)
        print("Simulation gestartet (keine Anlage nötig)")
    else:
        print(f"Verbinde mit HBW-TXT unter {args.ip}:{cfg.TXT_PORT} ...")
        try:
            hw = TxtHardware(args.ip)
        except HardwareFehler as e:
            print(f"\nFEHLER: {e}")
            if e.__cause__:
                print(f"Ursache: {e.__cause__!r}")
            print("Tipp: Mit 'python3 start.py --sim' lässt sich alles ohne Anlage testen.")
            sys.exit(1)
        print("Verbunden.")

    hbw = Hochregallager(hw)

    mqtt = None
    if args.mqtt:
        from hochregallager.mqtt_bruecke import MqttBruecke
        try:
            mqtt = MqttBruecke(hbw)
        except RuntimeError as e:
            print(f"MQTT nicht verfügbar: {e}")

    server = erstelle_server(hbw, args.port, mqtt)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{args.port}"
    print(f"Dashboard: {url}")

    hbw.start()   # Zustandsautomat: INIT -> Referenzfahrt -> IDLE
    if not args.kein_browser:
        webbrowser.open(url)

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nBeende ...")
    finally:
        hbw.stop()
        if mqtt:
            mqtt.beenden()
        server.shutdown()
        hw.trennen()
        print("Motoren aus, Verbindung getrennt.")


if __name__ == "__main__":
    main()
