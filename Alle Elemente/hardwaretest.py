#!/usr/bin/env python3
"""
Hardwaretest – VOR dem ersten Start von start.py ausführen.

Prüft Schritt für Schritt, ob Taster, Lichtschranken, Encoder, Motoren (und
beim Greifer: Sauger und Farbsensor) so angeschlossen sind wie in
lernfabrik/konfiguration.py angenommen. Jeder Motor bewegt sich nur kurz
bzw. bis zum Taster.

    py hardwaretest.py                       Hochregallager (TXT 192.168.0.12)
    py hardwaretest.py --station greifer     Greifer + Ein-/Ausgabe (TXT 192.168.0.13)
    py hardwaretest.py --station ofen        Bearbeitungsstation mit Ofen (TXT 192.168.0.11 + Erweiterung)
    py hardwaretest.py --station sortierung  Sortierstrecke (TXT 192.168.0.14)
    py hardwaretest.py --sim                 Simulation (mit --station kombinierbar)
"""

import argparse
import time

from lernfabrik import konfiguration as cfg
from lernfabrik.achsen import AchsenFehler, EndschalterAchse, Foerderband, ReferenzAchse
from lernfabrik.hardware import TxtHardware
from lernfabrik.simulation import erzeuge_sim


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


def eingaenge_live_mpo(hw, dauer=15):
    print(f"Eingänge Master (I) und Erweiterung (E) {dauer} s lang live – Strg+C bricht ab")
    ende = time.time() + dauer
    try:
        while time.time() < ende:
            zeile = "  ".join(f"I{n}:{hw.eingang(n)}" for n in range(1, 6))
            zeile += "  |  " + "  ".join(f"E{n}:{hw.eingang(cfg.ERW + n)}" for n in (1, 2, 3, 5))
            print("\r" + zeile + "   ", end="", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    print()


def ventil_kurz(hw, nr, sekunden=1.0, kompressor=None):
    """Ventil (und Kompressor) kurz einschalten."""
    if kompressor:
        hw.ausgang(kompressor, 512)
        time.sleep(0.5)
    hw.ausgang(nr, 512)
    time.sleep(sekunden)
    hw.ausgang(nr, 0)
    if kompressor:
        hw.ausgang(kompressor, 0)


def motor_kurz(hw, nr, v, sekunden):
    hw.motor(nr, v)
    time.sleep(sekunden)
    hw.motor(nr, 0)


def menue_ofen(hw):
    tisch = EndschalterAchse("Drehtisch", hw, cfg.MPO_MOTOR_TISCH, cfg.MPO_TISCH_TASTER)
    sauger = EndschalterAchse("Sauger", hw, cfg.MPO_MOTOR_GREIFER, cfg.MPO_GREIFER_TASTER)
    schieber = EndschalterAchse("Ofenschieber", hw, cfg.MPO_MOTOR_SCHIEBER, cfg.MPO_SCHIEBER_TASTER)
    print("\nBearbeitungsstation – Belegung laut Konfiguration (E = Erweiterungs-TXT):")
    print("  I1/I2/I3 Drehtisch Sauger/Säge/Band, I4 Lichtschranke Bandende, I5 Sauger am Drehtisch")
    print("  E1 Schieber im Ofen, E2 Schieber außen, E3 Sauger am Ofen, E5 Lichtschranke Ofen")
    print("  M1 Drehtisch, M2 Säge, M3 Band, O7 Auswerfer, O8 Kompressor")
    print("  E-M1 Ofenschieber, E-M2 Sauger, E-O5 Vakuum, E-O6 Senken, E-O7 Ofentür, E-O8 Licht")
    print("WICHTIG: Ob E1/E2 beim Schieber stimmen, ist unsicher (C++-Code und Pinliste widersprechen sich).")
    print("Test 1 zuerst: Schieber von Hand verschieben und schauen, welcher Taster schaltet!")
    print("Empfohlene Reihenfolge: 1, 2, 3, 4, 5, 6 ...\n")

    def tuer_auf_und_schieber(rein):
        hw.ausgang(cfg.MPO_KOMPRESSOR, 512)
        hw.ausgang(cfg.MPO_VENTIL_TUER, 512)
        time.sleep(1.0)
        schieber.fahre_zu(1 if rein else 0)
        hw.ausgang(cfg.MPO_KOMPRESSOR, 0)

    return {
        "1": ("Eingänge Master + Erweiterung live anzeigen", lambda: eingaenge_live_mpo(hw)),
        "2": ("Ofentür öffnen (E-O7 + Kompressor, bleibt offen)", lambda: (hw.ausgang(cfg.MPO_KOMPRESSOR, 512),
              hw.ausgang(cfg.MPO_VENTIL_TUER, 512), time.sleep(1), hw.ausgang(cfg.MPO_KOMPRESSOR, 0))),
        "3": ("Ofenschieber nach außen (E-M1 bis E2) – Tür wird vorher geöffnet", lambda: tuer_auf_und_schieber(False)),
        "4": ("Ofenschieber in den Ofen (E-M1 bis E1) – Tür wird vorher geöffnet", lambda: tuer_auf_und_schieber(True)),
        "5": ("Sauger zum Drehtisch (E-M2 bis I5)", lambda: sauger.fahre_zu(0)),
        "6": ("Sauger zum Ofen (E-M2 bis E3)", lambda: sauger.fahre_zu(1)),
        "7": ("Drehtisch zum Sauger (M1 bis I1)", lambda: tisch.fahre_zu(0)),
        "8": ("Drehtisch zur Säge (M1 bis I2) – vorher 7!", lambda: tisch.fahre_zu(1)),
        "9": ("Drehtisch zum Band (M1 bis I3) – vorher 7 oder 8!", lambda: tisch.fahre_zu(2)),
        "10": ("Säge 1 s laufen lassen (M2)", lambda: motor_kurz(hw, cfg.MPO_MOTOR_SAEGE, -512, 1.0)),
        "11": ("Band 2 s laufen lassen (M3, Richtung Sortierstrecke)", lambda: motor_kurz(hw, cfg.MPO_MOTOR_BAND, -512, 2.0)),
        "12": ("Auswerfer (O7) mit Kompressor (O8)", lambda: ventil_kurz(hw, cfg.MPO_VENTIL_AUSWERFER, 0.2, cfg.MPO_KOMPRESSOR)),
        "13": ("Sauger senken (E-O6) mit Kompressor", lambda: ventil_kurz(hw, cfg.MPO_VENTIL_SENKEN, 1.5, cfg.MPO_KOMPRESSOR)),
        "14": ("Ofenlicht 2 s (E-O8)", lambda: ventil_kurz(hw, cfg.MPO_OFENLICHT, 2.0)),
    }


def menue_sortierung(hw):
    print("\nSortierstrecke – Belegung laut Konfiguration:")
    print("  I1 Lichtschranke Start, I2 Farbsensor (analog), I3 Lichtschranke Auswurf,")
    print("  I6/I7/I8 Lichtschranken Ablage weiß/rot/blau, C1 Impulszähler")
    print("  M1 Band, O5/O6/O7 Auswerfer weiß/rot/blau, O8 Kompressor")
    print("Empfohlene Reihenfolge: 1, 2, 3, 4.\n")

    def zaehler_test():
        hw.zaehler_reset(cfg.SLD_ZAEHLER)
        hw.motor(cfg.SLD_MOTOR_BAND, -512)
        time.sleep(3.0)
        hw.motor(cfg.SLD_MOTOR_BAND, 0)
        n = hw.zaehler(cfg.SLD_ZAEHLER)
        print(f"Zähler C1 nach 3 s Bandlauf: {n} Impulse = {n * cfg.SLD_FLANKEN_PRO_IMPULS} Flanken "
              f"(Faktor SLD_FLANKEN_PRO_IMPULS = {cfg.SLD_FLANKEN_PRO_IMPULS})")
        print("Zum Vergleich: Im Original wird Blau nach 26 Flanken ausgeworfen. Liegt ein blaues Werkstück")
        print("dann nicht vor dem blauen Auswerfer, den Faktor in konfiguration.py anpassen.")

    def farbe_live():
        print("Werkstück unter den Farbsensor legen – Messwerte 15 s live (Strg+C bricht ab)")
        ende = time.time() + 15
        try:
            while time.time() < ende:
                print(f"\r  Farbsensor I2: {hw.spannung(cfg.SLD_FARBSENSOR):5d} mV   ", end="", flush=True)
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        print()

    return {
        "1": ("Eingänge und Zähler live anzeigen", lambda: eingaenge_live(hw, (cfg.SLD_ZAEHLER,), 15, cfg.SLD_FARBSENSOR)),
        "2": ("Band 3 s laufen lassen und Zähler C1 prüfen (M1)", zaehler_test),
        "3": ("Farbsensor live (I2)", farbe_live),
        "4": ("Auswerfer weiß (O5) mit Kompressor (O8)", lambda: ventil_kurz(hw, cfg.SLD_VENTIL["WHITE"], 0.5, cfg.SLD_KOMPRESSOR)),
        "5": ("Auswerfer rot (O6) mit Kompressor (O8)", lambda: ventil_kurz(hw, cfg.SLD_VENTIL["RED"], 0.5, cfg.SLD_KOMPRESSOR)),
        "6": ("Auswerfer blau (O7) mit Kompressor (O8)", lambda: ventil_kurz(hw, cfg.SLD_VENTIL["BLUE"], 0.5, cfg.SLD_KOMPRESSOR)),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--station", choices=("hbw", "greifer", "ofen", "sortierung"), default="hbw")
    p.add_argument("--sim", action="store_true")
    p.add_argument("--ip", default=None, help="IP des TXT (Standard je nach Station)")
    args = p.parse_args()

    if args.station == "hbw":
        hw = erzeuge_sim("hbw") if args.sim else TxtHardware(args.ip or cfg.TXT_IP)
        menue = menue_hbw(hw)
    elif args.station == "greifer":
        hw = erzeuge_sim("vgr") if args.sim else TxtHardware(
            args.ip or cfg.VGR_IP, motoren=(cfg.VGR_MOTOR_X, cfg.VGR_MOTOR_Y, cfg.VGR_MOTOR_Z),
            ausgaenge=(cfg.VGR_KOMPRESSOR, cfg.VGR_VENTIL), spannung=(cfg.DPS_FARBSENSOR,))
        menue = menue_greifer(hw)
    elif args.station == "ofen":
        hw = erzeuge_sim("mpo") if args.sim else TxtHardware(
            args.ip or cfg.MPO_IP, erweiterung=True,
            motoren=(cfg.MPO_MOTOR_TISCH, cfg.MPO_MOTOR_SAEGE, cfg.MPO_MOTOR_BAND,
                     cfg.MPO_MOTOR_SCHIEBER, cfg.MPO_MOTOR_GREIFER),
            ausgaenge=(cfg.MPO_VENTIL_AUSWERFER, cfg.MPO_KOMPRESSOR, cfg.MPO_VENTIL_VAKUUM,
                       cfg.MPO_VENTIL_SENKEN, cfg.MPO_VENTIL_TUER, cfg.MPO_OFENLICHT))
        menue = menue_ofen(hw)
    else:
        hw = erzeuge_sim("sld") if args.sim else TxtHardware(
            args.ip or cfg.SLD_IP, motoren=(cfg.SLD_MOTOR_BAND,),
            ausgaenge=(*cfg.SLD_VENTIL.values(), cfg.SLD_KOMPRESSOR), spannung=(cfg.SLD_FARBSENSOR,))
        menue = menue_sortierung(hw)

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
