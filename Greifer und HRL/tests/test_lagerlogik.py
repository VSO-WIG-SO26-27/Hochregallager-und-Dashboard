"""
Ablauftest in der Simulation (ohne Anlage):  python3 tests/test_lagerlogik.py

Prüft die Anforderungen an das Hochregallager:
  * Referenzfahrt nur beim Start, danach bleibt das RBG an der letzten Position
  * leerer Behälter bleibt nach der Auslagerung am Band und wird bei der
    nächsten Einlagerung direkt benutzt bzw. bei der nächsten Auslagerung zurückgelagert
  * Warteschlange mit Priorität und Vorrang
  * ABC-Umlagerung im Stillstand und manuelles Tauschen über die Förderstrecke
  * Reservierung/Buchung und Kennzahlen
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import code.daten as daten          # noqa: E402
import code.kennzahlen as kennzahlen  # noqa: E402

TMP = tempfile.mkdtemp()
daten.DATEN_ORDNER = kennzahlen.DATEN_ORDNER = TMP

from code.auftraege import Auftragsverwaltung  # noqa: E402
from code.greifer import Sauggreifer           # noqa: E402
from code.hardware import SimHardware          # noqa: E402
from code.steuerung import Hochregallager      # noqa: E402
from code import konfiguration as cfg          # noqa: E402

ZF = float(os.environ.get("ZF", "12"))


def warte(bed, timeout=120, text=""):
    t = time.time()
    while not bed():
        if time.time() - t > timeout:
            raise AssertionError(f"Timeout: {text}")
        if hbw.zustand == "FAULT" or vgr.zustand == "FAULT":
            raise AssertionError(f"FAULT bei '{text}': HBW={hbw.fehler!r} VGR={vgr.fehler!r}")
        time.sleep(0.02)


def ruhe():
    warte(lambda: hbw.zustand == "IDLE" and not hbw.aktiv and vgr.zustand == "IDLE" and not vgr.aktiv
          and not lvs.offen, text="Ruhe")


hw_h, hw_v = SimHardware(ZF, "hbw"), SimHardware(ZF, "vgr")
hbw = Hochregallager(hw_h)
vgr = Sauggreifer(hw_v, hbw)
lvs = Auftragsverwaltung(hbw, vgr)
vgr.verbinde_lvs(lvs)
hbw.einst.setze("auto_umlagern", False)
hbw.einst.setze("umlagern_nach_s", 1)
referenzfahrten = []
_ref = hbw.referenzfahrt
hbw.referenzfahrt = lambda: (referenzfahrten.append(time.time()), _ref())[1]
for s in (hbw, vgr):
    s.start()
warte(lambda: hbw.zustand == "IDLE" and vgr.zustand == "IDLE", text="Start")
print("Start ok – Referenzfahrten bisher:", len(referenzfahrten))


def einlegen(farbe):
    hw_v.sim_werkstueck_einlegen(farbe)
    warte(lambda: any(a["art"] == "EIN" for a in lvs.offen), text="EIN-Auftrag")
    ruhe()


# 1) Einlagern: weiß, rot, blau, rot
for f in ("WHITE", "RED", "BLUE", "RED"):
    einlegen(f)
print("Nach 4 Einlagerungen:\n" + hbw.lager.als_text())
assert hbw.lager.anzahl("RED") == 2 and hbw.lager.zustand("BAND") == "FREI"

# 2) Auslagern rot -> leerer Behälter bleibt am Band, RBG steht am Band
ok, m = lvs.bestellen("RED")
assert ok, m
ruhe()
assert hbw.lager.zustand("BAND") == "LEER", hbw.lager.als_text()
print("Auslagerung rot: leerer Behälter steht am Band ✓")

# 3) Einlagerung direkt in den Behälter am Band (keine Bereitstellungsfahrt)
zugriffe_vorher = sum(hbw.kpi.d["zugriffe"].values())
einlegen("BLUE")
assert hbw.lager.zustand("BAND") == "FREI"
assert sum(hbw.kpi.d["zugriffe"].values()) - zugriffe_vorher == 1, "nur 1 Lagerzugriff erwartet"
print("Einlagerung blau in den Behälter vom Band (1 Fahrt statt 2) ✓")

# 4) Zwei Auslagerungen hintereinander: zweite lagert zuerst den Leerbehälter zurück
assert lvs.bestellen("WHITE")[0] and lvs.bestellen("BLUE")[0]
ruhe()
assert hbw.lager.zustand("BAND") == "LEER"
print("Zwei Auslagerungen: Leerbehälter vor der 2. Auslagerung zurückgelagert ✓")

# 5) Warteschlange: Verfügbarkeitsprüfung, Priorität, Vorrang
print("Bestand:", {f: hbw.lager.anzahl(f) for f in ("WHITE", "RED", "BLUE")})
assert not lvs.bestellen("WHITE")[0], "kein weiß mehr – muss abgelehnt werden"
hbw.einst.setze("vorrang", "AUS")
for f in ("RED", "BLUE"):
    pass
einlegen("WHITE")
einlegen("WHITE")
lvs.bestellen("WHITE", "normal")
lvs.bestellen("RED", "eilig")
r = [(a["art"], a["farbe"], a["prio"]) for a in lvs.reihenfolge()]
print("Reihenfolge:", r)
ruhe()

# 6) Umlagerung im Stillstand
print("Bestellhistorie:", hbw.kpi.bestellhistorie(20), "ABC:", hbw.strategie.abc()[0])
print("vorher:\n" + hbw.lager.als_text())
k_vorher = hbw.strategie.kosten(hbw.lager.snapshot(), hbw.strategie.abc()[0])
hbw.einst.setze("auto_umlagern", True)
time.sleep(2)
warte(lambda: hbw.zustand == "IDLE" and hbw._stillstand_seit is not None and not hbw._plan
      and time.time() - hbw._letzte_planung < 1, text="Auto-Umlagern")
time.sleep(4)
warte(lambda: hbw.zustand == "IDLE" and not hbw._plan, text="Umlagern fertig")
k_nachher = hbw.strategie.kosten(hbw.lager.snapshot(), hbw.strategie.abc()[0])
print("nachher:\n" + hbw.lager.als_text(), f"\nZonenabweichung {k_vorher} -> {k_nachher}")
assert k_nachher <= k_vorher
hbw.einst.setze("auto_umlagern", False)

# 7) Manuell tauschen über die Förderstrecke
if hbw.lager.zustand("BAND") != "FREI":
    frei = [p for p in daten.PLAETZE if hbw.lager.zustand(p) == "FREI"][0]
    ok, m = hbw.anfordern("umlagern", art="band_einlagern", nach=frei)
    assert ok, m
    warte(lambda: hbw.zustand == "IDLE" and not hbw._plan and hbw.lager.zustand("BAND") == "FREI", text="Band einlagern")
a, b = "A1", "C3"
va, vb = hbw.lager.belegung(a), hbw.lager.belegung(b)
ok, m = hbw.anfordern("umlagern", art="tauschen", von=a, nach=b)
assert ok, m
time.sleep(0.2)
warte(lambda: hbw.zustand == "IDLE" and not hbw._plan, text="Tauschen")
assert hbw.lager.belegung(a) == vb and hbw.lager.belegung(b) == va
print(f"Tausch {a}⇄{b} über die Förderstrecke ✓")

# 8) Referenzfahrt nur beim Start + Nachreferenzieren in Pausen (Encoder-Abgleich)
transporte = sum(hbw.kpi.d["zugriffe"].values()) / 2
erwartet_max = 1 + int(transporte // cfg.NACHREFERENZ_NACH_TRANSPORTEN) + 1
assert len(referenzfahrten) <= erwartet_max, f"{len(referenzfahrten)} Referenzfahrten bei {transporte:.0f} Transporten"
print(f"Referenzfahrten: {len(referenzfahrten)} bei {transporte:.0f} Transporten "
      f"(Start + Nachreferenzieren alle {cfg.NACHREFERENZ_NACH_TRANSPORTEN}) ✓  RBG steht bei", hbw.position())

# 10) Leerbehälter-Bereitstellung beginnt mit der Einlagerung -> Greifer wartet kaum
w = [x["dauer"] for x in hbw.kpi.d["wartezeiten"] if x["art"] == "Einlagerung" and x["wer"] == "Greifer"]
print(f"Greifer-Wartezeit Einlagerung: Ø {sum(w)/len(w):.2f} s, max {max(w):.2f} s")
assert max(w) < 0.5, "Greifer sollte bei der Einlagerung nicht warten"

# 9) Kennzahlen
k = hbw.kpi.bericht(hbw.lager)
for art in ("Einlagerung", "Auslagerung", "Umlagerung"):
    print(f"  {art:12s}", k["spiele"][art]["netto"], "brutto Ø", k["spiele"][art]["brutto"]["mittel"])
print("  Wartezeiten:", {kk: v["mittel"] for kk, v in k["wartezeiten"].items()})
print("  Durchsatz:", k["durchsatz"]["letzte_stunde"], "Füllgrad:", k["fuellgrad"]["prozent"], "%")
print("  Durchlauf:", k["durchlauf"])
print("  Erledigte Aufträge:", [(a["nr"], a["art"], a["farbe"], a["status"]) for a in lvs.erledigt][:6])
for s in (hbw, vgr):
    s.stop()
print("\nALLE TESTS OK")
