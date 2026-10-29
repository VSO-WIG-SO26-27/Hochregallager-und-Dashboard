"""
Lagerstrategie: dynamische ABC-Zuordnung nach Bestellhistorie.

Idee (wie in der Lagerlogistik üblich – "Schnelldreher nach vorn"):
  1. Wegzeit: Für jeden Lagerplatz wird die Fahrzeit vom Übergabeplatz am
     Förderband abgeschätzt. X und Y fahren gleichzeitig, also zählt die
     längere der beiden Strecken (Tschebyschow-Abstand in Encoder-Impulsen).
  2. Zonen: Die 9 Plätze werden nach Wegzeit sortiert und in drei Zonen
     zu je 3 Plätzen eingeteilt: A (schnell erreichbar), B, C (weit weg).
  3. ABC-Klassen: Die Farben werden nach der Anzahl ihrer Bestellungen in
     den letzten N Aufträgen sortiert: meistbestellte Farbe = A usw.
     Da je Farbe max. 3 Werkstücke eingelagert werden, passt jede Klasse
     genau in ihre Zone.
  4. Platzwahl:
       Einlagern     -> freier Platz in der Zone der Farbe (sonst nächstgelegener)
       Leerbehälter  -> den Behälter holen, dessen Platz danach am besten passt
       Auslagern     -> kürzester Weg (Gerät -> Platz -> Band)
  5. Umlagern: Eine Breitensuche plant bis zu cfg.UMLAGERN_MAX_ZUEGE
     Behälterbewegungen, die die Summe der Zonenabweichungen am stärksten
     senken. Als Puffer dienen freie Plätze und das Förderband.
"""

import math

from . import konfiguration as cfg
from .daten import BAND, FARBEN, FREI, LEER, PLAETZE, platz_koordinaten

KLASSEN = ("A", "B", "C")
FARB_REIHENFOLGE = ("WHITE", "RED", "BLUE")   # Gleichstand: diese Reihenfolge


class Strategie:
    def __init__(self, kalib, einst, historie):
        """kalib: Kalibrierung (Positionen), einst: Einstellungen,
        historie: Funktion(fenster) -> {Farbe: Anzahl Bestellungen}"""
        self.kalib = kalib
        self.einst = einst
        self.historie = historie

    # =======================================================================
    # Wege und Zonen
    # =======================================================================
    def koordinate(self, ort):
        if isinstance(ort, tuple):
            return ort
        if ort == BAND:
            return self.kalib.conv["x"], self.kalib.conv["y"]
        x, y = platz_koordinaten(ort)
        return self.kalib.hbx[x], self.kalib.hby[y]

    def weg(self, a, b):
        """Geschätzte Fahrzeit in Encoder-Impulsen (X und Y fahren gleichzeitig)."""
        (xa, ya), (xb, yb) = self.koordinate(a), self.koordinate(b)
        return max(abs(xa - xb), abs(ya - yb))

    def rangfolge(self):
        """Plätze sortiert nach Wegzeit vom Förderband (Gleichstand: kürzere Gesamtstrecke)."""
        def key(p):
            (xa, ya), (xb, yb) = self.koordinate(BAND), self.koordinate(p)
            return self.weg(BAND, p), math.hypot(xa - xb, ya - yb), p
        return sorted(PLAETZE, key=key)

    def zonen(self):
        # Zwischenspeicher – ändert sich nur, wenn neu kalibriert wurde
        schluessel = (tuple(self.kalib.hbx), tuple(self.kalib.hby), self.kalib.conv["x"], self.kalib.conv["y"])
        if getattr(self, "_zonen_schluessel", None) != schluessel:
            r = self.rangfolge()
            self._zonen = {"A": r[0:3], "B": r[3:6], "C": r[6:9]}
            self._zone_je_platz = {p: k for k, pl in self._zonen.items() for p in pl}
            self._zonen_schluessel = schluessel
        return self._zonen

    def zone(self, platz):
        self.zonen()
        return self._zone_je_platz.get(platz)

    # =======================================================================
    # ABC-Analyse
    # =======================================================================
    def abc(self):
        """Gibt (Klassen, Zähler, aktiv) zurück. Klassen: {Farbe: 'A'|'B'|'C'}."""
        zaehler = {f: 0 for f in FARBEN}
        zaehler.update(self.historie(self.einst["abc_fenster"]))
        reihe = sorted(FARBEN, key=lambda f: (-zaehler[f], FARB_REIHENFOLGE.index(f)))
        klassen = {f: KLASSEN[i] for i, f in enumerate(reihe)}
        aktiv = sum(zaehler.values()) >= self.einst["abc_min_bestellungen"]
        return klassen, zaehler, aktiv

    def klasse(self, farbe):
        klassen, _, aktiv = self.abc()
        return klassen.get(farbe) if aktiv else None

    def zonenabstand(self, platz, farbe, klassen=None):
        """0 = Werkstück steht in seiner Zone, 1 = eine Zone daneben, 2 = ganz falsch."""
        k = klassen.get(farbe) if klassen is not None else self.klasse(farbe)
        if k is None or platz == BAND:
            return 0
        return abs(KLASSEN.index(self.zone(platz)) - KLASSEN.index(k))

    # =======================================================================
    # Platzwahl
    # =======================================================================
    def einlager_ziel(self, snap, farbe):
        """Freier Platz für einen Behälter mit Werkstück dieser Farbe."""
        frei = [p for p in PLAETZE if snap[p] == FREI]
        if not frei:
            return None
        return min(frei, key=lambda p: (self.zonenabstand(p, farbe), self.weg(BAND, p), p))

    def leerbehaelter_quelle(self, snap, farbe, position):
        """Welchen leeren Behälter für ein Werkstück dieser Farbe holen?
        Der Behälter kommt später auf einen freien Platz zurück – deshalb wird
        bewertet, wie gut dieser Platz danach zur Farbe passt."""
        kandidaten = [p for p in PLAETZE if snap[p] == LEER]
        if not kandidaten:
            return None

        def bewertung(e):
            danach = dict(snap)
            danach[e] = FREI
            ziel = self.einlager_ziel(danach, farbe)
            return (self.zonenabstand(ziel, farbe), self.weg(position, e) + self.weg(e, BAND), e)
        return min(kandidaten, key=bewertung)

    def auslager_quelle(self, snap, farbe, position):
        """Werkstück dieser Farbe mit dem kürzesten Weg Gerät -> Platz -> Band.
        Bei gleichem Weg zuerst eines, das nicht in seiner Zone steht."""
        kandidaten = [p for p in PLAETZE if snap[p] == farbe]
        if not kandidaten:
            return None
        return min(kandidaten, key=lambda p: (self.weg(position, p) + self.weg(p, BAND),
                                              -self.zonenabstand(p, farbe), p))

    def leer_rueck_ziel(self, snap, folge=None):
        """Freier Platz für den leeren Behälter vom Band (kürzester Weg, ggf. weiter zu 'folge')."""
        frei = [p for p in PLAETZE if snap[p] == FREI]
        if not frei:
            return None
        return min(frei, key=lambda p: (self.weg(BAND, p) + (self.weg(p, folge) if folge else 0), p))

    # =======================================================================
    # Umlagerplanung
    # =======================================================================
    def kosten(self, snap, klassen):
        """Summe der Zonenabweichungen aller eingelagerten Werkstücke."""
        k = sum(self.zonenabstand(p, snap[p], klassen) for p in PLAETZE if snap[p] in FARBEN)
        if snap[BAND] in FARBEN:
            k += 99        # ein Werkstück darf nicht am Band stehen bleiben
        return k

    def umlager_plan(self, snap, max_zuege=None):
        """Breitensuche über Behälterbewegungen. Gibt (Züge, Kosten vorher, Kosten nachher)
        zurück; Züge = [(von, nach), ...]. Leere Liste = keine Verbesserung möglich.

        Regeln:
          * Steht am Band schon ein Behälter (leer nach einer Auslagerung), bleibt er dort.
          * Ist das Band frei, darf es als Puffer dienen. Am Ende darf dort höchstens
            ein LEERER Behälter stehen – er wird bei der nächsten Einlagerung benutzt
            bzw. bei der nächsten Auslagerung zurückgelagert.
          * Werkstücke, die schon in ihrer Zone stehen, werden nicht bewegt.
        """
        klassen, _, aktiv = self.abc()
        if not aktiv:
            return [], 0, 0
        max_zuege = max_zuege or cfg.UMLAGERN_MAX_ZUEGE
        orte = PLAETZE + [BAND]
        band_fest = snap[BAND] != FREI
        start = tuple(snap[o] for o in orte)
        kosten_start = self.kosten(snap, klassen)
        if kosten_start == 0:
            return [], 0, 0

        def als_dict(t):
            return dict(zip(orte, t))

        bester = (kosten_start, 0, [])
        ebene = [(start, [])]
        gesehen = {start}
        for tiefe in range(1, max_zuege + 1):
            naechste = []
            for zustand, weg in ebene:
                d = als_dict(zustand)
                freie = [o for o in orte if d[o] == FREI and not (o == BAND and band_fest)]
                for von in orte:
                    inhalt = d[von]
                    if inhalt == FREI or (von == BAND and band_fest):
                        continue
                    if inhalt in FARBEN and von != BAND and self.zonenabstand(von, inhalt, klassen) == 0:
                        continue
                    for nach in freie:
                        if nach == von:
                            continue
                        neu = dict(d)
                        neu[nach], neu[von] = inhalt, FREI
                        t = tuple(neu[o] for o in orte)
                        if t in gesehen:
                            continue
                        gesehen.add(t)
                        pfad = weg + [(von, nach)]
                        k = self.kosten(neu, klassen)
                        if k < bester[0]:
                            bester = (k, tiefe, pfad)
                        naechste.append((t, pfad))
            ebene = naechste
            if bester[0] == 0:
                break
        k, _, pfad = bester
        return (pfad, kosten_start, k) if k < kosten_start else ([], kosten_start, kosten_start)

    # =======================================================================
    def bericht(self, snap=None):
        klassen, zaehler, aktiv = self.abc()
        summe = sum(zaehler.values()) or 1
        z = self.zonen()
        b = {
            "zonen": z,
            "zone_je_platz": {p: k for k, pl in z.items() for p in pl},
            "wegzeit": {p: self.weg(BAND, p) for p in PLAETZE},
            "klassen": klassen,
            "bestellungen": zaehler,
            "anteil": {f: round(100 * zaehler[f] / summe) for f in FARBEN},
            "aktiv": aktiv,
            "fenster": self.einst["abc_fenster"],
            "min_bestellungen": self.einst["abc_min_bestellungen"],
        }
        if snap is not None:
            b["fehlplatziert"] = [p for p in PLAETZE if snap[p] in FARBEN and aktiv
                                  and self.zonenabstand(p, snap[p], klassen) > 0]
        return b
