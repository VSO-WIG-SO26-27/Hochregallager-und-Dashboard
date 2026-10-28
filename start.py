#!/usr/bin/env python3
"""
fischertechnik Lernfabrik 4.0 – Python-Steuerung für Hochregallager (HBW) und
Sauggreifer mit Ein-/Ausgabestation (VGR/DPS), mit Dashboard im Browser.

Start an der Anlage (Mac im Lernfabrik-WLAN):
    python3 start.py              Hochregallager + Greifer
    python3 start.py --nur-hbw    nur das Hochregallager

Ohne Anlage (Simulation):
    python3 start.py --sim

Weitere Optionen:
    --ip 192.168.0.12            IP des Hochregallager-TXT
    --ip-greifer 192.168.0.13    IP des Greifer-TXT
    --mqtt                       HBW zusätzlich mit dem MQTT-Broker der Lernfabrik verbinden
                                 (nur sinnvoll mit --nur-hbw, wenn der Original-Greifer läuft)
    --port 8000                  Port des Dashboards
    --kein-browser               Browser nicht automatisch öffnen

Beenden: Strg+C im Terminal (alle Motoren werden ausgeschaltet).
"""

import argparse
import sys
import threading
import time
import webbrowser

from code import konfiguration as cfg
from code.dashboard import erstelle_server
from code.greifer import Sauggreifer
from code.hardware import HardwareFehler, SimHardware, TxtHardware
from code.steuerung import Hochregallager


def verbinde(name, ip, **kw):
    print(f"Verbinde mit {name}-TXT unter {ip}:{cfg.TXT_PORT} ...")
    try:
        hw = TxtHardware(ip, **kw)
    except HardwareFehler as e:
        print(f"\nFEHLER ({name}): {e}")
        if e.__cause__:
            print(f"Ursache: {e.__cause__!r}")
        return None
    print(f"{name} verbunden.")
    return hw


def main():
    p = argparse.ArgumentParser(description="Lernfabrik-Steuerung (Python)")
    p.add_argument("--sim", action="store_true", help="Simulation statt echter Anlage")
    p.add_argument("--ip", default=cfg.TXT_IP, help=f"IP des Hochregallager-TXT (Standard {cfg.TXT_IP})")
    p.add_argument("--ip-greifer", default=cfg.VGR_IP, help=f"IP des Greifer-TXT (Standard {cfg.VGR_IP})")
    p.add_argument("--nur-hbw", action="store_true", help="nur das Hochregallager steuern")
    p.add_argument("--port", type=int, default=cfg.WEB_PORT, help="Port des Dashboards")
    p.add_argument("--mqtt", action="store_true", help="MQTT-Anbindung des HBW an die Lernfabrik aktivieren")
    p.add_argument("--zeitfaktor", type=float, default=1.0, help="Simulation schneller laufen lassen (z. B. 3)")
    p.add_argument("--kein-browser", action="store_true")
    args = p.parse_args()

    hardware = []
    if args.sim:
        hw_hbw = SimHardware(args.zeitfaktor, "hbw")
        hw_vgr = None if args.nur_hbw else SimHardware(args.zeitfaktor, "vgr")
        print("Simulation gestartet (keine Anlage nötig)")
    else:
        hw_hbw = verbinde("Hochregallager", args.ip)
        hw_vgr = None
        if hw_hbw and not args.nur_hbw:
            hw_vgr = verbinde("Greifer", args.ip_greifer, motoren=(cfg.VGR_MOTOR_X, cfg.VGR_MOTOR_Y, cfg.VGR_MOTOR_Z),
                              ausgaenge=(cfg.VGR_KOMPRESSOR, cfg.VGR_VENTIL), spannung=(cfg.DPS_FARBSENSOR,))
            if hw_vgr is None:
                hw_hbw.trennen()
                hw_hbw = None
                print("Tipp: Nur das Hochregallager starten mit 'python3 start.py --nur-hbw'.")
        if hw_hbw is None:
            print("Tipp: Mit 'python3 start.py --sim' lässt sich alles ohne Anlage testen.")
            sys.exit(1)
    hardware = [h for h in (hw_hbw, hw_vgr) if h]

    stationen = {"hbw": Hochregallager(hw_hbw)}
    if hw_vgr:
        stationen["vgr"] = Sauggreifer(hw_vgr, stationen["hbw"])

    mqtt = None
    if args.mqtt:
        from code.mqtt_bruecke import MqttBruecke
        try:
            mqtt = MqttBruecke(stationen["hbw"])
        except RuntimeError as e:
            print(f"MQTT nicht verfügbar: {e}")

    server = erstelle_server(stationen, args.port, mqtt)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{args.port}"
    print(f"Dashboard: {url}")

    for s in stationen.values():
        s.start()   # Zustandsautomat: INIT -> Referenzfahrt -> IDLE
    if not args.kein_browser:
        webbrowser.open(url)

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nBeende ...")
    finally:
        for s in stationen.values():
            s.stop()
        if mqtt:
            mqtt.beenden()
        server.shutdown()
        for h in hardware:
            h.trennen()
        print("Motoren aus, Verbindungen getrennt.")


if __name__ == "__main__":
    main()
