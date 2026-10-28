"""
Simulation der Werkstücke über alle Stationen hinweg.

Jede Station hat ihre eigene SimHardware (Motoren, Taster, Lichtschranken).
Damit ein Werkstück von einer Station zur nächsten "wandern" kann, teilen sich
alle Simulationen eine gemeinsame SimWelt. Sie merkt sich, wo gerade welches
Werkstück liegt (Eingabe, Greifer, Ofen, Band, Ablagen der Sortierstrecke ...).

Ein Werkstück wird einfach durch seine Farbe dargestellt: "WHITE", "RED", "BLUE"
oder "NONE" (Farbe nicht erkennbar).

Die Stationen selbst wissen nichts von der Simulation: Sie schalten nur
Motoren und Ventile und lesen Sensoren – genau wie an der echten Anlage.
Ausnahme: das Hochregallager meldet über hw.sim_ereignis(), welche Farbe es
aufs Band gestellt hat (die Behälter im Regal werden nicht physikalisch simuliert).
"""

import collections
import math
import threading
import time

from . import konfiguration as cfg
from .hardware import SimHardware

FARBEN = ("WHITE", "RED", "BLUE")


class SimWelt:
    """Wo liegt welches Werkstück? Gemeinsam für alle Stationen."""

    def __init__(self):
        self.lock = threading.RLock()
        self.eingabe = None          # Ein-/Ausgabestation: Werkstück in der Eingabe
        self.ausgabe = None          # ... in der Ausgabe
        self.falsch = []             # Ablage für Werkstücke mit unbekannter Farbe
        self.greifer = None          # hängt am Sauger des Greifers
        self.hbw_band = None         # steht im Behälter am Band des Hochregallagers
        self.mpo_ofen = None         # liegt auf dem Ofenschieber
        self.mpo_sauger = None       # hängt am internen Sauger der Bearbeitungsstation
        self.mpo_tisch = None        # liegt auf dem Drehtisch
        self.mpo_band = None         # {"farbe", "pos"} auf dem Band der Bearbeitungsstation (pos 0..1)
        self.sld_band = []           # [{"farbe", "pos"}] auf der Sortierstrecke (pos in Zähl-Flanken)
        self.sld_ablage = {f: [] for f in FARBEN}
        self.protokoll = collections.deque(maxlen=40)

    def log(self, text):
        self.protokoll.append(time.strftime("%H:%M:%S ") + "[SIM] " + text)

    def ereignis(self, name, **d):
        with self.lock:
            if name == "hbw_band":
                self.hbw_band = d.get("farbe")

    def bericht(self):
        with self.lock:
            return {
                "eingabe": self.eingabe, "ausgabe": self.ausgabe, "falsch": list(self.falsch),
                "greifer": self.greifer, "hbw_band": self.hbw_band,
                "mpo_ofen": self.mpo_ofen, "mpo_sauger": self.mpo_sauger, "mpo_tisch": self.mpo_tisch,
                "mpo_band": dict(self.mpo_band) if self.mpo_band else None,
                "sld_band": [dict(w) for w in self.sld_band],
                "sld_ablage": {f: list(v) for f, v in self.sld_ablage.items()},
                "protokoll": list(self.protokoll),
            }


# ===========================================================================
# Greifer (VGR) mit Ein-/Ausgabestation (DPS)
# ===========================================================================
SIM_FARBWERTE = {"WHITE": 600, "RED": 1200, "BLUE": 1800, "NONE": 100}

# Gespeicherter Greiferpunkt -> Ort in der SimWelt
VGR_ORTE = {"DIN": "eingabe", "DCS": "farbsensor", "DCS0": "farbsensor", "DNFC": "nfc", "WDC": "falsch",
            "DOUT": "ausgabe", "HBW": "hbw_band", "HBW1": "hbw_band", "MPO": "mpo_ofen",
            "SSD1": "WHITE", "SSD2": "RED", "SSD3": "BLUE"}


class SimVGR(SimHardware):
    def __init__(self, zeitfaktor=1.0, welt=None):
        super().__init__(zeitfaktor, "vgr", welt or SimWelt())
        self.sim_positionen = {k: list(v) for k, v in cfg.VGR_STANDARD_POSITIONEN.items()}
        self.sim_farbe = "NONE"      # Werkstück, das man für die Farbkalibrierung unter den Sensor hält

    def _ort(self):
        """Nächstgelegener gespeicherter Punkt (oder None, wenn keiner in der Nähe ist)."""
        p = (self.pos[cfg.VGR_MOTOR_X], self.pos[cfg.VGR_MOTOR_Y], self.pos[cfg.VGR_MOTOR_Z])
        bester, abstand = None, 120.0
        for name, ort in VGR_ORTE.items():
            q = self.sim_positionen.get(name)
            if q is None:
                continue
            a = math.dist(p, q)
            if a < abstand:
                bester, abstand = ort, a
        return bester

    def _ausgang_geaendert(self, nr, alt, neu):
        if nr != cfg.VGR_VENTIL:
            return
        w = self.welt
        ort = self._ort()
        with w.lock:
            if neu and w.greifer is None:                    # ansaugen
                if ort == "eingabe" and w.eingabe:
                    w.greifer, w.eingabe = w.eingabe, None
                elif ort == "hbw_band" and w.hbw_band:
                    w.greifer, w.hbw_band = w.hbw_band, None
                elif ort in FARBEN and w.sld_ablage[ort]:
                    w.greifer = w.sld_ablage[ort].pop(0)
                if w.greifer:
                    w.log(f"Greifer saugt {w.greifer} an ({'Ablage ' + ort if ort in FARBEN else ort})")
            elif not neu and w.greifer is not None:          # loslassen
                wp, w.greifer = w.greifer, None
                if ort == "ausgabe":
                    w.ausgabe = wp
                elif ort == "falsch":
                    w.falsch.append(wp)
                elif ort == "hbw_band":
                    w.hbw_band = wp
                elif ort == "mpo_ofen":
                    w.mpo_ofen = wp
                elif ort == "eingabe":
                    w.eingabe = wp
                else:
                    w.log(f"Werkstück {wp} ist heruntergefallen (losgelassen bei {ort or 'unbekannter Position'})")
                    return
                w.log(f"Greifer legt {wp} ab ({ort})")

    def _welt_eingang(self, nr):
        if nr == cfg.DPS_LICHTSCHRANKE_EINGABE:
            return 0 if self.welt.eingabe else 1
        return None

    def zaehler_eingang(self, nr):
        if nr == cfg.DPS_LICHTSCHRANKE_AUSGABE:
            return 0 if self.welt.ausgabe else 1
        return super().zaehler_eingang(nr)

    def _welt_spannung(self, nr):
        wp = self.welt.greifer
        if wp is not None:
            return SIM_FARBWERTE[wp] if self._ort() == "farbsensor" else SIM_FARBWERTE["NONE"]
        return SIM_FARBWERTE.get(self.sim_farbe, 100)

    # --- Dashboard-Knöpfe --------------------------------------------------
    def sim_werkstueck_einlegen(self, farbe):
        with self.welt.lock:
            self.welt.eingabe = farbe
            self.welt.log(f"{farbe} in die Eingabe gelegt")
        self.sim_farbe = farbe

    def sim_ausgabe(self, belegt):
        with self.welt.lock:
            self.welt.ausgabe = "WHITE" if belegt else None
            self.welt.log("Ausgabe " + ("belegt" if belegt else "geleert"))


# ===========================================================================
# Bearbeitungsstation mit Ofen (MPO)
# ===========================================================================
class SimMPO(SimHardware):
    BAND_ZEIT_S = 2.0            # so lange braucht ein Werkstück bis zum Bandende

    def __init__(self, zeitfaktor=1.0, welt=None):
        super().__init__(zeitfaktor, "mpo", welt or SimWelt())
        self.schalter_pos[cfg.MPO_MOTOR_SCHIEBER] = 0.0      # Schieber steht draußen

    @property
    def tisch(self):
        return self.schalter_pos[cfg.MPO_MOTOR_TISCH]

    @property
    def greifer(self):
        return self.schalter_pos[cfg.MPO_MOTOR_GREIFER]      # 0 = am Drehtisch, 1 = am Ofen

    @property
    def schieber(self):
        return self.schalter_pos[cfg.MPO_MOTOR_SCHIEBER]     # 0 = draußen, 1 = im Ofen

    def _welt_eingang(self, nr):
        w = self.welt
        if nr == cfg.MPO_LICHTSCHRANKE_OFEN:
            return 0 if (w.mpo_ofen and self.schieber < 0.1) else 1
        if nr == cfg.MPO_LICHTSCHRANKE_BANDENDE:
            return 0 if (w.mpo_band and 0.8 <= w.mpo_band["pos"] < 1.0) else 1
        return None

    def _ausgang_geaendert(self, nr, alt, neu):
        w = self.welt
        with w.lock:
            if nr == cfg.MPO_VENTIL_VAKUUM and neu:
                if self.greifer >= 0.95 and w.mpo_ofen and self.schieber < 0.1:
                    w.mpo_sauger, w.mpo_ofen = w.mpo_ofen, None
                    w.log(f"MPO-Sauger nimmt {w.mpo_sauger} vom Ofenschieber")
            elif nr == cfg.MPO_VENTIL_VAKUUM and not neu and w.mpo_sauger:
                wp, w.mpo_sauger = w.mpo_sauger, None
                if self.greifer <= 0.05 and abs(self.tisch) < 0.1:
                    w.mpo_tisch = wp
                    w.log(f"MPO-Sauger legt {wp} auf den Drehtisch")
                elif self.greifer >= 0.95:
                    w.mpo_ofen = wp
                else:
                    w.log(f"Werkstück {wp} ist in der Bearbeitungsstation heruntergefallen")
            elif nr == cfg.MPO_VENTIL_AUSWERFER and neu:
                if w.mpo_tisch and abs(self.tisch - 2) < 0.1:
                    w.mpo_band = {"farbe": w.mpo_tisch, "pos": 0.0}
                    w.log(f"{w.mpo_tisch} aufs Band der Bearbeitungsstation ausgeworfen")
                    w.mpo_tisch = None

    def _welt_schritt(self, dt):
        w = self.welt
        with w.lock:
            if w.mpo_band and self._v[cfg.MPO_MOTOR_BAND]:
                w.mpo_band["pos"] += dt / self.BAND_ZEIT_S
                if w.mpo_band["pos"] >= 1.0:
                    w.sld_band.append({"farbe": w.mpo_band["farbe"], "pos": 0.0})
                    w.log(f"{w.mpo_band['farbe']} an die Sortierstrecke übergeben")
                    w.mpo_band = None

    def sim_ofen_einlegen(self, farbe):
        with self.welt.lock:
            self.welt.mpo_ofen = farbe
            self.welt.log(f"{farbe} von Hand auf den Ofenschieber gelegt")


# ===========================================================================
# Sortierstrecke (SLD)
# ===========================================================================
SLD_FARBWERTE = {"WHITE": 600, "RED": 1250, "BLUE": 1600, "NONE": 2100}
SLD_HINTERGRUND = 2100          # Messwert ohne Werkstück (über 2000 = keine Farbe)


class SimSLD(SimHardware):
    FLANKEN_PRO_S = 10.0        # Bandgeschwindigkeit in Zähl-Flanken pro Sekunde
    START = (0.0, 1.5)          # Bereich der Lichtschranke I1
    FARBSENSOR = (3.0, 5.0)     # Bereich unter dem Farbsensor
    AUSWURF = (8.0, 9.5)        # Lichtschranke I3
    BANDENDE = 40.0

    def __init__(self, zeitfaktor=1.0, welt=None):
        super().__init__(zeitfaktor, "sld", welt or SimWelt())
        self.ventil_pos = {f: self.AUSWURF[0] + cfg.SLD_ZAEHLER_AUSWURF[f] for f in FARBEN}

    def _welt_schritt(self, dt):
        with self.welt.lock:
            self._auswerfer_pruefen()
        if not self._v[cfg.SLD_MOTOR_BAND]:
            return
        d = self.FLANKEN_PRO_S * dt
        self._zaehler[cfg.SLD_ZAEHLER] += d / cfg.SLD_FLANKEN_PRO_IMPULS
        w = self.welt
        with w.lock:
            for wp in list(w.sld_band):
                wp["pos"] += d
                if wp["pos"] > self.BANDENDE:
                    w.sld_band.remove(wp)
                    w.log(f"{wp['farbe']} ist am Ende der Sortierstrecke heruntergefallen")

    def _bereich(self, von_bis):
        return [wp for wp in self.welt.sld_band if von_bis[0] <= wp["pos"] < von_bis[1]]

    def _welt_eingang(self, nr):
        w = self.welt
        with w.lock:
            if nr == cfg.SLD_LICHTSCHRANKE_START:
                return 0 if self._bereich(self.START) else 1
            if nr == cfg.SLD_LICHTSCHRANKE_AUSWURF:
                return 0 if self._bereich(self.AUSWURF) else 1
            for f, e in cfg.SLD_LICHTSCHRANKE_ABLAGE.items():
                if nr == e:
                    return 0 if w.sld_ablage[f] else 1
        return None

    def _welt_spannung(self, nr):
        with self.welt.lock:
            werte = [SLD_FARBWERTE.get(wp["farbe"], SLD_HINTERGRUND) for wp in self._bereich(self.FARBSENSOR)]
        return min(werte) if werte else SLD_HINTERGRUND

    def _toleranz(self):
        """Wie weit darf ein Werkstück vom Auswerfer entfernt sein? Bei schnellerer
        Simulation (Zeitfaktor) fährt das Band zwischen zwei Abfragen der Station
        weiter – daher wächst der Bereich mit (etwa 30 ms Reaktionszeit)."""
        return 2.5 + 0.03 * self.FLANKEN_PRO_S * self.zf

    def _auswerfer_pruefen(self):
        """Solange ein Auswerfer-Ventil offen ist, wird ein Werkstück davor in die Ablage geschoben."""
        w = self.welt
        for f, ventil in cfg.SLD_VENTIL.items():
            if not self._ausgaenge[ventil]:
                continue
            for wp in list(w.sld_band):
                if abs(wp["pos"] - self.ventil_pos[f]) <= self._toleranz():
                    w.sld_band.remove(wp)
                    w.sld_ablage[f].append(wp["farbe"])
                    w.log(f"{wp['farbe']} in Ablage {f} ausgeworfen")

    def _ausgang_geaendert(self, nr, alt, neu):
        if neu:
            with self.welt.lock:
                self._auswerfer_pruefen()

    def sim_band_einlegen(self, farbe):
        with self.welt.lock:
            self.welt.sld_band.append({"farbe": farbe, "pos": 0.0})
            self.welt.log(f"{farbe} von Hand auf die Sortierstrecke gelegt")

    def sim_ablagen_leeren(self):
        with self.welt.lock:
            for v in self.welt.sld_ablage.values():
                v.clear()
            self.welt.sld_band.clear()
            self.welt.log("Sortierstrecke geleert (Band und Ablagen)")


def erzeuge_sim(profil, zeitfaktor=1.0, welt=None):
    """Passende Simulation für eine Station anlegen."""
    klassen = {"vgr": SimVGR, "mpo": SimMPO, "sld": SimSLD}
    if profil in klassen:
        return klassen[profil](zeitfaktor, welt)
    return SimHardware(zeitfaktor, profil, welt)
