"""
Testprogramm NUR für die Bearbeitungsstation (Ofen) – bewegt nichts.

Zeigt live an:
  * ob die Lichtschranke am Ofen (Erweiterungs-TXT, Eingang I5) frei oder belegt ist
  * alle Eingänge von Master (I1..I8) und Erweiterung (E1..E8)
  * ob die Verbindung zum TXT noch steht (Alter der letzten Daten)

Aufruf (im Ordner "Alle Elemente"):
    py ofen_test.py                  Master 192.168.0.11 mit Erweiterung
    py ofen_test.py --ip 192.168.0.11
    py ofen_test.py --ohne-erweiterung   nur den Master testen (E-Eingänge fehlen dann)

Test: Werkstück auf den Ofenschieber legen und wieder wegnehmen.
      E5 muss zwischen FREI und BELEGT wechseln. Beenden mit Strg+C.
"""

import argparse
import sys
import time

from lernfabrik import konfiguration as cfg
from lernfabrik.hardware import HardwareFehler, TxtHardware

LS = cfg.MPO_LICHTSCHRANKE_OFEN - cfg.ERW      # 5


def main():
    p = argparse.ArgumentParser(description="Lichtschranke am Ofen testen")
    p.add_argument("--ip", default=cfg.MPO_IP)
    p.add_argument("--ohne-erweiterung", action="store_true")
    args = p.parse_args()
    mit_erw = not args.ohne_erweiterung

    print(f"Verbinde mit Ofen-TXT {args.ip} ({'mit' if mit_erw else 'ohne'} Erweiterung) ...")
    try:
        # motoren=() / ausgaenge=() -> es wird garantiert nichts bewegt oder geschaltet
        hw = TxtHardware(args.ip, motoren=(), ausgaenge=(), erweiterung=mit_erw)
    except HardwareFehler as e:
        print(f"FEHLER: {e}")
        if e.__cause__:
            print(f"Ursache: {e.__cause__!r}")
        sys.exit(1)
    txt = hw.txt
    print("Verbunden. Auf dem TXT-Display sollte jetzt 'Online' stehen.")
    print("Werkstück auf den Ofenschieber legen / wegnehmen.  Strg+C = Ende\n")

    letzter = None
    wechsel = 0
    start = time.time()
    try:
        while True:
            alter = time.time() - getattr(txt, "_update_timer", time.time())
            gestoppt = getattr(txt, "_txt_stop_event", None)
            gestoppt = gestoppt is not None and gestoppt.is_set()
            if gestoppt or alter > 2.0:
                print(f"\n\n!!! VERBINDUNG VERLOREN nach {time.time() - start:.1f} s "
                      f"(letzte Daten vor {alter:.1f} s).")
                print("    Die Zeile von ftrobopy direkt darüber nennt den Grund (z. B. 'Network error ...').")
                print(f"    Bis dahin gab es {wechsel} Wechsel an der Lichtschranke.")
                break

            roh = list(txt._current_input)          # direkt aus ftrobopy, ohne Umweg
            master = roh[0:8]
            erw = roh[8:16] if mit_erw else None

            zeile = "I: " + " ".join(f"{n + 1}:{v}" for n, v in enumerate(master))
            if erw is not None:
                ls = erw[LS - 1]
                if letzter is not None and ls != letzter:
                    wechsel += 1
                    print(f"\n  -> Lichtschranke wechselt auf {'BELEGT' if ls == 0 else 'FREI'} "
                          f"(Wechsel Nr. {wechsel})")
                letzter = ls
                zeile += "  |  E: " + " ".join(f"{n + 1}:{v}" for n, v in enumerate(erw))
                zeile += f"  |  Lichtschranke E{LS}: {'BELEGT' if ls == 0 else 'frei  '}"
            zeile += f"  |  Daten {alter * 1000:4.0f} ms alt"
            print("\r" + zeile, end="", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print(f"\n\nBeendet. Wechsel an der Lichtschranke: {wechsel}")
    finally:
        hw.trennen()


if __name__ == "__main__":
    main()
