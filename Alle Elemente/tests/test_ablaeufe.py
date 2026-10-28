"""
Automatische Tests der Abläufe – laufen komplett in der Simulation, ohne Anlage.

Starten (im Repo-Ordner, nicht im Ordner tests/):
    py -m pytest            (Mac/Linux: python3 -m pytest)

Einmalig vorher installieren:
    py -m pip install pytest

Ein kompletter Bestelldurchlauf dauert in der Simulation ca. 30 Sekunden,
weil die Wartezeiten der Stationen (Ofen, Säge, Sauger ...) echte Sekunden sind.
"""

import time

import pytest

from lernfabrik import daten
from lernfabrik import konfiguration as cfg
from lernfabrik.daten import farbe_aus_messwert
from lernfabrik.greifer import Sauggreifer
from lernfabrik.ofen import Bearbeitungsstation
from lernfabrik.simulation import SimWelt, erzeuge_sim
from lernfabrik.sortierung import Sortierstrecke
from lernfabrik.steuerung import Hochregallager

ZEITFAKTOR = 10


@pytest.fixture(autouse=True)
def eigener_datenordner(tmp_path, monkeypatch):
    """Tests schreiben Lager und Kalibrierung in einen Temp-Ordner, nicht nach daten/."""
    monkeypatch.setattr(daten, "DATEN_ORDNER", str(tmp_path))
    # Wartezeiten verkürzen, damit die Tests schneller sind
    monkeypatch.setattr(cfg, "VGR_ANSAUGZEIT_S", 0.3)
    monkeypatch.setattr(cfg, "MPO_BRENNEN_TAKT_S", 0.02)
    monkeypatch.setattr(cfg, "MPO_SAEGEN_S", 0.2)
    monkeypatch.setattr(cfg, "MPO_BAND_NACHLAUF_S", 0.5)


def warte_bis(bedingung, timeout=60, text=""):
    ende = time.time() + timeout
    while time.time() < ende:
        if bedingung():
            return
        time.sleep(0.05)
    raise AssertionError(f"Zeitüberschreitung: {text}")


@pytest.fixture
def fabrik():
    """Alle vier Stationen in der Simulation, gestartet und im Zustand IDLE."""
    welt = SimWelt()
    hw = {k: erzeuge_sim(k, ZEITFAKTOR, welt) for k in ("hbw", "vgr", "mpo", "sld")}
    st = {"hbw": Hochregallager(hw["hbw"])}
    st["vgr"] = Sauggreifer(hw["vgr"], st["hbw"])
    st["mpo"] = Bearbeitungsstation(hw["mpo"])
    st["sld"] = Sortierstrecke(hw["sld"])
    st["vgr"].verbinde_bearbeitung(st["mpo"], st["sld"])
    for s in st.values():
        s.start()
    warte_bis(lambda: all(s.zustand == "IDLE" for s in st.values()), 30, "Start aller Stationen")
    yield welt, hw, st
    for s in st.values():
        s.stop()
    for h in hw.values():
        h.trennen()


def einlagern(welt, hw, st, farbe):
    hw["vgr"].sim_werkstueck_einlegen(farbe)
    warte_bis(lambda: st["hbw"].hat_farbe(farbe) and st["hbw"].zustand == "IDLE"
              and st["vgr"].zustand == "IDLE", 60, f"Einlagern {farbe}")


def test_farbregel():
    assert farbe_aus_messwert(600, 850, 1550) == "WHITE"
    assert farbe_aus_messwert(850, 850, 1550) == "RED"
    assert farbe_aus_messwert(1600, 850, 1550) == "BLUE"
    assert farbe_aus_messwert(100, 850, 1550) == "NONE"
    assert farbe_aus_messwert(2100, 850, 1550) == "NONE"


def test_einlagern_und_bestellen_ueber_alle_stationen(fabrik):
    welt, hw, st = fabrik
    einlagern(welt, hw, st, "RED")
    ok, meldung = st["vgr"].anfordern("bestellen", farbe="RED")
    assert ok, meldung
    warte_bis(lambda: welt.ausgabe == "RED" and st["vgr"].bestellung["state"] == "WAITING_FOR_ORDER",
              120, "Bestellung RED bis zur Ausgabe")
    assert st["mpo"].zyklen == 1
    assert st["sld"].sortiert["RED"] == 1
    assert not st["hbw"].hat_farbe("RED")
    assert all(s.zustand != "FAULT" for s in st.values())


def test_bestellweg_direkt(fabrik):
    welt, hw, st = fabrik
    einlagern(welt, hw, st, "WHITE")
    assert st["vgr"].anfordern("bestellweg", weg="direkt")[0]
    assert st["vgr"].anfordern("bestellen", farbe="WHITE")[0]
    warte_bis(lambda: welt.ausgabe == "WHITE" and st["vgr"].zustand == "IDLE", 60, "direkte Bestellung")
    assert st["mpo"].zyklen == 0


def test_zweite_bestellung_waehrend_der_ersten_wird_abgelehnt(fabrik):
    welt, hw, st = fabrik
    einlagern(welt, hw, st, "BLUE")
    einlagern(welt, hw, st, "BLUE")
    assert st["vgr"].anfordern("bestellen", farbe="BLUE")[0]
    warte_bis(lambda: st["vgr"].zustand == "IDLE" and st["vgr"].bestellung["state"] == "IN_PROCESS",
              60, "Greifer frei, Bestellung unterwegs")
    ok, meldung = st["vgr"].anfordern("bestellen", farbe="BLUE")
    assert not ok and "läuft bereits" in meldung


def test_unbekannte_farbe_kommt_in_die_falsch_ablage(fabrik):
    welt, hw, st = fabrik
    hw["vgr"].sim_werkstueck_einlegen("NONE")
    warte_bis(lambda: welt.falsch == ["NONE"] and st["vgr"].zustand == "IDLE", 60, "Ablage falsche Farbe")
    assert not st["hbw"].hat_farbe("NONE")


def test_ausgabe_belegt_greifer_wartet(fabrik):
    welt, hw, st = fabrik
    einlagern(welt, hw, st, "WHITE")
    st["vgr"].anfordern("bestellweg", weg="direkt")
    hw["vgr"].sim_ausgabe(True)
    st["vgr"].anfordern("bestellen", farbe="WHITE")
    warte_bis(lambda: st["vgr"].zustand == "MOVE_PICKUP_WAIT", 60, "Greifer wartet an der Ausgabe")
    time.sleep(1.0)
    assert st["vgr"].zustand == "MOVE_PICKUP_WAIT"
    hw["vgr"].sim_ausgabe(False)
    warte_bis(lambda: welt.ausgabe == "WHITE" and st["vgr"].zustand == "IDLE", 30, "Ablage nach Freigabe")


def test_sortierstrecke_alleine():
    welt = SimWelt()
    hw = erzeuge_sim("sld", ZEITFAKTOR, welt)
    sld = Sortierstrecke(hw)
    sld.start()
    try:
        warte_bis(lambda: sld.zustand == "IDLE", 10, "Start")
        for farbe in ("WHITE", "RED", "BLUE"):
            hw.sim_band_einlegen(farbe)
            warte_bis(lambda: welt.sld_ablage[farbe] == [farbe] and sld.zustand == "IDLE", 20, f"Sortieren {farbe}")
        hw.sim_band_einlegen("NONE")
        warte_bis(lambda: sld.zustand == "FAULT", 20, "Fehler bei unbekannter Farbe")
        assert "Farbe nicht erkannt" in sld.fehler
    finally:
        sld.stop()
        hw.trennen()


def test_bearbeitungsstation_ohne_werkstueck_meldet_fehler(monkeypatch):
    monkeypatch.setattr(cfg, "MPO_WARTE_OFEN_S", 0.5)
    hw = erzeuge_sim("mpo", ZEITFAKTOR)
    mpo = Bearbeitungsstation(hw)
    mpo.start()
    try:
        warte_bis(lambda: mpo.zustand == "IDLE", 15, "Grundstellung")
        ok, meldung = mpo.anfordern("produzieren")          # vom Dashboard: wird abgelehnt
        assert not ok and "Kein Werkstück" in meldung
        mpo.anfordern("produzieren", quelle="vgr")          # vom Greifer: Fehler nach Wartezeit
        warte_bis(lambda: mpo.zustand == "FAULT", 10, "Fehlerzustand")
        assert hw.ausgang_wert(cfg.MPO_KOMPRESSOR) == 0
        mpo.anfordern("quittieren")
        warte_bis(lambda: mpo.zustand == "IDLE", 15, "Grundstellung nach Quittieren")
    finally:
        mpo.stop()
        hw.trennen()


def test_not_halt_stoppt_alles(fabrik):
    welt, hw, st = fabrik
    hw["mpo"].sim_ofen_einlegen("RED")
    st["mpo"].anfordern("produzieren")
    warte_bis(lambda: st["mpo"].zustand == "BURN", 10, "Bearbeitung läuft")
    for s in st.values():
        s.anfordern("not_halt")
    warte_bis(lambda: st["mpo"].zustand == "FAULT", 10, "Not-Halt -> FAULT")
    assert all(v == 0 for v in hw["mpo"]._v.values())
