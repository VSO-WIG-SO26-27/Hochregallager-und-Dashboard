#!/usr/bin/env python3
"""
fischertechnik Lernfabrik 4.0 – Python-Steuerung mit Dashboard im Browser.

Stationen (Kürzel wie im Original-Code):
  hbw  Hochregallager                         TXT 192.168.0.12
  vgr  Sauggreifer mit Ein-/Ausgabestation    TXT 192.168.0.13
  mpo  Bearbeitungsstation mit Ofen           TXT 192.168.0.11 (+ Erweiterungs-TXT)
  sld  Sortierstrecke mit Farberkennung       TXT 192.168.0.14

Ohne Anlage (Simulation, alle vier Stationen):
    py start.py --sim                 (Mac/Linux: python3 start.py --sim)

An der Anlage (Rechner im Lernfabrik-WLAN):
    py start.py                       alle vier Stationen
    py start.py --stationen hbw,vgr   nur Hochregallager und Greifer
    py start.py --nur-hbw             nur das Hochregallager

Weitere Optionen:
    --ip / --ip-greifer / --ip-mpo / --ip-sld   abweichende IP-Adressen
    --mqtt                       HBW zusätzlich mit dem MQTT-Broker der Lernfabrik verbinden
                                 (nur sinnvoll mit --nur-hbw, wenn der Original-Greifer läuft)
    --port 8000                  Port des Dashboards
    --host 0.0.0.0               Dashboard auch für andere Geräte im WLAN erreichbar machen
    --zeitfaktor 5               Simulation schneller laufen lassen
    --kein-browser               Browser nicht automatisch öffnen

Fällt an der Anlage die Bearbeitungsstation oder die Sortierstrecke aus (keine
Verbindung), läuft der Rest trotzdem – Bestellungen gehen dann direkt zur Ausgabe.

Beenden: Strg+C im Terminal (alle Motoren werden ausgeschaltet).
"""

import argparse
import sys
import threading
import time
import webbrowser

from lernfabrik import konfiguration as cfg
from lernfabrik.dashboard import erstelle_server
from lernfabrik.greifer import Sauggreifer
from lernfabrik.hardware import HardwareFehler, TxtHardware
from lernfabrik.ofen import Bearbeitungsstation
from lernfabrik.simulation import SimWelt, erzeuge_sim
from lernfabrik.sortierung import Sortierstrecke
from lernfabrik.steuerung import Hochregallager

NAMEN = {"hbw": "Hochregallager", "vgr": "Greifer", "mpo": "Bearbeitungsstation", "sld": "Sortierstrecke"}


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


def txt_hardware(kuerzel, args):
    """Verbindung zum echten TXT einer Station mit der passenden Pin-Belegung."""
    if kuerzel == "hbw":
        return verbinde(NAMEN[kuerzel], args.ip)
    if kuerzel == "vgr":
        return verbinde(NAMEN[kuerzel], args.ip_greifer,
                        motoren=(cfg.VGR_MOTOR_X, cfg.VGR_MOTOR_Y, cfg.VGR_MOTOR_Z),
                        ausgaenge=(cfg.VGR_KOMPRESSOR, cfg.VGR_VENTIL), spannung=(cfg.DPS_FARBSENSOR,))
    if kuerzel == "mpo":
        return verbinde(NAMEN[kuerzel], args.ip_mpo, erweiterung=True,
                        motoren=(cfg.MPO_MOTOR_TISCH, cfg.MPO_MOTOR_SAEGE, cfg.MPO_MOTOR_BAND,
                                 cfg.MPO_MOTOR_SCHIEBER, cfg.MPO_MOTOR_GREIFER),
                        ausgaenge=(cfg.MPO_VENTIL_AUSWERFER, cfg.MPO_KOMPRESSOR, cfg.MPO_VENTIL_VAKUUM,
                                   cfg.MPO_VENTIL_SENKEN, cfg.MPO_VENTIL_TUER, cfg.MPO_OFENLICHT))
    if kuerzel == "sld":
        return verbinde(NAMEN[kuerzel], args.ip_sld, motoren=(cfg.SLD_MOTOR_BAND,),
                        ausgaenge=(*cfg.SLD_VENTIL.values(), cfg.SLD_KOMPRESSOR),
                        spannung=(cfg.SLD_FARBSENSOR,))
    raise ValueError(kuerzel)


def main():
    p = argparse.ArgumentParser(description="Lernfabrik-Steuerung (Python)")
    p.add_argument("--sim", action="store_true", help="Simulation statt echter Anlage")
    p.add_argument("--stationen", default="hbw,vgr,mpo,sld",
                   help="welche Stationen laufen sollen, z. B. hbw,vgr (Standard: alle)")
    p.add_argument("--nur-hbw", action="store_true", help="nur das Hochregallager steuern")
    p.add_argument("--ip", default=cfg.TXT_IP, help=f"IP des Hochregallager-TXT (Standard {cfg.TXT_IP})")
    p.add_argument("--ip-greifer", default=cfg.VGR_IP, help=f"IP des Greifer-TXT (Standard {cfg.VGR_IP})")
    p.add_argument("--ip-mpo", default=cfg.MPO_IP, help=f"IP der Bearbeitungsstation (Standard {cfg.MPO_IP})")
    p.add_argument("--ip-sld", default=cfg.SLD_IP, help=f"IP der Sortierstrecke (Standard {cfg.SLD_IP})")
    p.add_argument("--port", type=int, default=cfg.WEB_PORT, help="Port des Dashboards")
    p.add_argument("--host", default="127.0.0.1", help="0.0.0.0 = Dashboard im ganzen WLAN erreichbar")
    p.add_argument("--mqtt", action="store_true", help="MQTT-Anbindung des HBW an die Lernfabrik aktivieren")
    p.add_argument("--zeitfaktor", type=float, default=1.0, help="Simulation schneller laufen lassen (z. B. 3)")
    p.add_argument("--kein-browser", action="store_true")
    args = p.parse_args()

    gewuenscht = ["hbw"] if args.nur_hbw else [s.strip() for s in args.stationen.split(",") if s.strip()]
    unbekannt = [s for s in gewuenscht if s not in NAMEN]
    if unbekannt or "hbw" not in gewuenscht:
        print(f"--stationen: erlaubt sind {', '.join(NAMEN)} (hbw muss dabei sein)")
        sys.exit(2)
    if ("mpo" in gewuenscht or "sld" in gewuenscht) and "vgr" not in gewuenscht:
        print("Hinweis: Bearbeitungsstation/Sortierstrecke laufen ohne Greifer nur im Handbetrieb.")

    welt = None
    hw = {}
    if args.sim:
        welt = SimWelt()
        for k in gewuenscht:
            hw[k] = erzeuge_sim(k, args.zeitfaktor, welt)
        print(f"Simulation gestartet ({', '.join(NAMEN[k] for k in gewuenscht)}) – keine Anlage nötig")
    else:
        for k in gewuenscht:
            h = txt_hardware(k, args)
            if h is None and k in ("hbw", "vgr"):
                for andere in hw.values():
                    andere.trennen()
                if k == "vgr":
                    print("Tipp: Nur das Hochregallager starten mit 'py start.py --nur-hbw'.")
                print("Tipp: Mit 'py start.py --sim' lässt sich alles ohne Anlage testen.")
                sys.exit(1)
            if h is None:
                print(f"-> {NAMEN[k]} wird weggelassen. Bestellungen gehen direkt zur Ausgabe.")
                continue
            hw[k] = h

    stationen = {"hbw": Hochregallager(hw["hbw"])}
    if "vgr" in hw:
        stationen["vgr"] = Sauggreifer(hw["vgr"], stationen["hbw"])
    if "mpo" in hw:
        stationen["mpo"] = Bearbeitungsstation(hw["mpo"])
    if "sld" in hw:
        stationen["sld"] = Sortierstrecke(hw["sld"])
    if all(k in stationen for k in ("vgr", "mpo", "sld")):
        stationen["vgr"].verbinde_bearbeitung(stationen["mpo"], stationen["sld"])

    mqtt = None
    if args.mqtt:
        from lernfabrik.mqtt_bruecke import MqttBruecke
        try:
            mqtt = MqttBruecke(stationen["hbw"])
        except RuntimeError as e:
            print(f"MQTT nicht verfügbar: {e}")

    server = erstelle_server(stationen, args.port, mqtt, welt=welt, host=args.host)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{args.port}"
    print(f"Dashboard: {url}" + (f"  (im WLAN: http://<IP dieses Rechners>:{args.port})" if args.host == "0.0.0.0" else ""))

    for s in stationen.values():
        s.start()   # Zustandsautomat: INIT -> Referenzfahrt/Grundstellung -> IDLE
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
        for h in hw.values():
            h.trennen()
        print("Motoren aus, Verbindungen getrennt.")


if __name__ == "__main__":
    main()
