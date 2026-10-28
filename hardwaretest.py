#!/usr/bin/env python3
"""
Hardwaretest für das Hochregallager – VOR dem ersten Start von start.py ausführen.

Prüft Schritt für Schritt, ob Taster, Lichtschranken, Encoder und Motoren
so angeschlossen sind wie in hochregallager/konfiguration.py angenommen.
Jeder Motor bewegt sich nur kurz bzw. bis zum Taster.

    python3 hardwaretest.py          (an der Anlage)
    python3 hardwaretest.py --sim    (Simulation)
"""

import argparse
import time

from hochregallager import konfiguration as cfg
from hochregallager.achsen import AchsenFehler, EndschalterAchse, Foerderband, ReferenzAchse
from hochregallager.hardware import SimHardware, TxtHardware

NAMEN = {cfg.LICHTSCHRANKE_AUSSEN: "Lichtschranke außen", cfg.LICHTSCHRANKE_INNEN: "Lichtschranke innen",
         cfg.REF_X: "Referenz X", cfg.TASTER_Z_EINGEFAHREN: "Ausleger eingefahren",
         cfg.TASTER_Z_AUSGEFAHREN: "Ausleger ausgefahren", cfg.REF_Y: "Referenz Y"}


def eingaenge_live(hw, dauer=15):
    print(f"Eingänge {dauer} s lang live (Taster von Hand drücken, Lichtschranke unterbrechen) – Strg+C bricht ab")
    ende = time.time() + dauer
    try:
        while time.time() < ende:
            zeile = "  ".join(f"I{n}:{hw.eingang(n)}" for n in range(1, 9))
            print("\r" + zeile + f"   C2:{hw.zaehler(2):5d} C4:{hw.zaehler(4):5d}", end="", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    print()


def kurz_fahren(hw, achse, schritte=100):
    """Fährt die Achse ein Stück vom Referenztaster weg und wieder zurück."""
    print(f"{achse.name}: Referenzfahrt …")
    achse.referenzfahrt()
    print(f"{achse.name}: fahre {schritte} Impulse vom Taster weg …")
    achse.fahre_abs(schritte)
    print(f"{achse.name}: Position laut Encoder: {achse.pos}  (sollte ca. {schritte} sein)")
    achse.fahre_abs(0)
    print(f"{achse.name}: zurück, Taster gedrückt: {achse.taster_gedrueckt()}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sim", action="store_true")
    p.add_argument("--ip", default=cfg.TXT_IP)
    args = p.parse_args()

    hw = SimHardware() if args.sim else TxtHardware(args.ip)
    x = ReferenzAchse("X", hw, cfg.MOTOR_X, cfg.REF_X, cfg.ENDE_X)
    y = ReferenzAchse("Y", hw, cfg.MOTOR_Y, cfg.REF_Y, cfg.ENDE_Y)
    z = EndschalterAchse("Z", hw, cfg.MOTOR_Z, (cfg.TASTER_Z_EINGEFAHREN, cfg.TASTER_Z_AUSGEFAHREN))
    band = Foerderband(hw, cfg.MOTOR_BAND)

    menue = {
        "1": ("Eingänge und Zähler live anzeigen", lambda: eingaenge_live(hw)),
        "2": ("Ausleger einfahren (M3 bis I6)", z.einfahren),
        "3": ("Ausleger ausfahren (M3 bis I7)  – nur wenn vor dem Regal/Band Platz ist!", z.ausfahren),
        "4": ("Achse X: Referenz + 100 Impulse hin und zurück (M2, I5, C2)", lambda: kurz_fahren(hw, x)),
        "5": ("Achse Y: Referenz + 100 Impulse hin und zurück (M4, I8, C4)", lambda: kurz_fahren(hw, y)),
        "6": ("Förderband rein (M1, 1,0 s)", band.rein),
        "7": ("Förderband raus (M1, 1,2 s)", band.raus),
    }
    print("\nPin-Belegung laut Konfiguration:")
    for n, name in sorted(NAMEN.items()):
        print(f"  I{n}: {name}")
    print(f"  M{cfg.MOTOR_BAND}: Band  M{cfg.MOTOR_X}: X  M{cfg.MOTOR_Z}: Ausleger  M{cfg.MOTOR_Y}: Y\n")
    print("Empfohlene Reihenfolge: 1, 2, 4, 5, 6, 7. Bei falscher Richtung sofort Strg+C!\n")
    try:
        while True:
            for k, (text, _) in menue.items():
                print(f"  {k}) {text}")
            wahl = input("Auswahl (q = Ende): ").strip().lower()
            if wahl == "q":
                break
            if wahl in menue:
                try:
                    menue[wahl][1]()
                    print("OK\n")
                except AchsenFehler as e:
                    hw.alles_aus()
                    print(f"FEHLER: {e}\n")
    except KeyboardInterrupt:
        print("\nAbbruch")
    finally:
        hw.trennen()
        print("Motoren aus, Verbindung getrennt.")


if __name__ == "__main__":
    main()
