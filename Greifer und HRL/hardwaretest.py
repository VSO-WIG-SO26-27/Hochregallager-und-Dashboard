#!/usr/bin/env python3
"""
Hardwaretest – VOR dem ersten Start von start.py ausführen.

Prüft Schritt für Schritt, ob Taster, Lichtschranken, Encoder, Motoren (und
beim Greifer: Sauger und Farbsensor) so angeschlossen sind wie in
hochregallager/konfiguration.py angenommen. Jeder Motor bewegt sich nur kurz
bzw. bis zum Taster.

    python3 hardwaretest.py                    Hochregallager (TXT 192.168.0.12)
    python3 hardwaretest.py --station greifer  Greifer + Ein-/Ausgabe (TXT 192.168.0.13)
    python3 hardwaretest.py --sim              Simulation
"""

import argparse
import time

from code import konfiguration as cfg
from code.achsen import AchsenFehler, EndschalterAchse, Foerderband, ReferenzAchse
from code.hardware import SimHardware, TxtHardware


def eingaenge_live(hw, zaehler, dauer=15, spannung=None, zaehler_eingang=None):
    print(f"Eingänge {dauer} s lang live (Taster von Hand drücken, Lichtschranke unterbrechen) – Strg+C bricht ab")
    ende = time.time() + dauer
    try:
        while time.time() < ende:
            zeile = "  ".join(f"I{n}:{hw.eingang(n)}" for n in range(1, 9))
            zeile += "  " + " ".join(f"C{n}:{hw.zaehler(n):5d}" for n in zaehler)
            if spannung:
                zeile += f"  Farbsensor:{hw.spannung(spannung):5d} mV"
            if zaehler_eingang:
                zeile += f"  C{zaehler_eingang}-Eingang:{hw.zaehler_eingang(zaehler_eingang)}"
            print("\r" + zeile + "   ", end="", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    print()


def kurz_fahren(achse, schritte=100):
    """Fährt die Achse ein Stück vom Referenztaster weg und wieder zurück."""
    print(f"{achse.name}: Referenzfahrt …")
    achse.referenzfahrt()
    print(f"{achse.name}: fahre {schritte} Impulse vom Taster weg …")
    achse.fahre_abs(schritte)
    print(f"{achse.name}: Position laut Encoder: {achse.pos}  (sollte ca. {schritte} sein)")
    achse.fahre_abs(0)
    print(f"{achse.name}: zurück, Taster gedrückt: {achse.taster_gedrueckt()}")


def menue_hbw(hw):
    x = ReferenzAchse("X", hw, cfg.MOTOR_X, cfg.REF_X, cfg.ENDE_X)
    y = ReferenzAchse("Y", hw, cfg.MOTOR_Y, cfg.REF_Y, cfg.ENDE_Y)
    z = EndschalterAchse("Z", hw, cfg.MOTOR_Z, (cfg.TASTER_Z_EINGEFAHREN, cfg.TASTER_Z_AUSGEFAHREN))
    band = Foerderband(hw, cfg.MOTOR_BAND)
    print("\nHochregallager – Belegung laut Konfiguration:")
    print("  I1 Lichtschranke außen, I4 Lichtschranke innen, I5 Referenz X, I6 Ausleger eingefahren,")
    print("  I7 Ausleger ausgefahren, I8 Referenz Y | M1 Band, M2 X (C2), M3 Ausleger, M4 Y (C4)")
    print("Empfohlene Reihenfolge: 1, 2, 4, 5, 6, 7.\n")
    return {
        "1": ("Eingänge und Zähler live anzeigen", lambda: eingaenge_live(hw, (2, 4))),
        "2": ("Ausleger einfahren (M3 bis I6)", z.einfahren),
        "3": ("Ausleger ausfahren (M3 bis I7) – nur wenn vor dem Regal/Band Platz ist!", z.ausfahren),
        "4": ("Achse X: Referenz + 100 Impulse hin und zurück (M2, I5, C2)", lambda: kurz_fahren(x)),
        "5": ("Achse Y: Referenz + 100 Impulse hin und zurück (M4, I8, C4)", lambda: kurz_fahren(y)),
        "6": ("Förderband rein (M1, 1,0 s)", band.rein),
        "7": ("Förderband raus (M1, 1,2 s)", band.raus),
    }


def menue_greifer(hw):
    x = ReferenzAchse("Drehung", hw, cfg.VGR_MOTOR_X, cfg.VGR_REF_X, cfg.VGR_ENDE_X)
    y = ReferenzAchse("Höhe", hw, cfg.VGR_MOTOR_Y, cfg.VGR_REF_Y, cfg.VGR_ENDE_Y)
    z = ReferenzAchse("Arm", hw, cfg.VGR_MOTOR_Z, cfg.VGR_REF_Z, cfg.VGR_ENDE_Z)

    def alle_referenz():
        # Reihenfolge wie im Original: erst Höhe (Arm hoch), dann Drehung und Arm
        y.referenzfahrt()
        x.referenzfahrt()
        z.referenzfahrt()
        print("Alle drei Achsen am Referenztaster.")

    def sauger():
        print("Kompressor an (O7) …")
        hw.ausgang(cfg.VGR_KOMPRESSOR, 512)
        time.sleep(cfg.VGR_ANSAUGZEIT_S)
        print("Ventil an (O8) – jetzt sollte der Sauger ansaugen (3 s) …")
        hw.ausgang(cfg.VGR_VENTIL, 512)
        time.sleep(3)
        hw.ausgang(cfg.VGR_VENTIL, 0)
        hw.ausgang(cfg.VGR_KOMPRESSOR, 0)
        print("Ventil und Kompressor aus.")

    print("\nGreifer – Belegung laut Konfiguration:")
    print("  I1 Ref. Drehung, I2 Ref. Höhe, I3 Ref. Arm, I7 Lichtschranke Eingabe (0 = belegt),")
    print("  I8 Farbsensor (analog), C4 Lichtschranke Ausgabe (1 = frei)")
    print("  M1 Drehung (C1), M2 Höhe (C2), M3 Arm (C3), O7 Kompressor, O8 Ventil")
    print("Empfohlene Reihenfolge: 1, 2, 3, 4, 5, 6, 7. Höhe zuerst, damit der Arm oben ist!\n")
    return {
        "1": ("Eingänge, Zähler, Farbsensor und Ausgabe live anzeigen",
              lambda: eingaenge_live(hw, (1, 2, 3), cfg.DPS_FARBSENSOR, cfg.DPS_LICHTSCHRANKE_AUSGABE)),
        "2": ("Höhe: Referenz + 100 Impulse hin und zurück (M2, I2, C2)", lambda: kurz_fahren(y)),
        "3": ("Drehung: Referenz + 100 Impulse hin und zurück (M1, I1, C1)", lambda: kurz_fahren(x)),
        "4": ("Arm: Referenz + 100 Impulse hin und zurück (M3, I3, C3)", lambda: kurz_fahren(z)),
        "5": ("Alle Achsen zur Referenz (Höhe, Drehung, Arm)", alle_referenz),
        "6": ("Sauger testen (Kompressor O7, Ventil O8)", sauger),
        "7": ("Farbsensor: Werkstück unter den Sensor halten – live anzeigen",
              lambda: eingaenge_live(hw, (), cfg.DPS_FARBSENSOR)),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--station", choices=("hbw", "greifer"), default="hbw")
    p.add_argument("--sim", action="store_true")
    p.add_argument("--ip", default=None, help="IP des TXT (Standard je nach Station)")
    args = p.parse_args()

    if args.station == "hbw":
        hw = SimHardware(profil="hbw") if args.sim else TxtHardware(args.ip or cfg.TXT_IP)
        menue = menue_hbw(hw)
    else:
        hw = SimHardware(profil="vgr") if args.sim else TxtHardware(
            args.ip or cfg.VGR_IP, motoren=(cfg.VGR_MOTOR_X, cfg.VGR_MOTOR_Y, cfg.VGR_MOTOR_Z),
            ausgaenge=(cfg.VGR_KOMPRESSOR, cfg.VGR_VENTIL), spannung=(cfg.DPS_FARBSENSOR,))
        menue = menue_greifer(hw)

    print("Bei falscher Fahrtrichtung sofort Strg+C!\n")
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
