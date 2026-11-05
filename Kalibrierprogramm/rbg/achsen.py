"""
Achsen und Förderband – Python-Übersetzung von

  TxtAxis.cpp / TxtAxis1RefSwitch.cpp  ->  ReferenzAchse   (Achsen X und Y)
  TxtAxisNSwitch.cpp                   ->  EndschalterAchse (Ausleger Z)
  TxtConveyorBelt.cpp                  ->  Foerderband

Unterschied zum Original: Bei Fehlern (Timeout, Endlage überschritten) ruft
der C++-Code exit(1) auf und beendet das ganze Programm. Hier wird stattdessen
eine AchsenFehler-Exception geworfen; die Ablaufsteuerung geht dann in den
Zustand FAULT und kann über "Quittieren" wieder gestartet werden.
"""

import time

from . import konfiguration as cfg


class AchsenFehler(Exception):
    pass


# Stillstandserkennung des Zählers (siehe ReferenzAchse._zaehler_ruhig)
ZAEHLER_RUHE_TAKT_S = 0.03      # so oft den Zähler lesen
ZAEHLER_RUHE_LESUNGEN = 3       # so viele gleiche Werte hintereinander = Achse steht
ZAEHLER_RUHE_MAX_S = 1.0        # länger nie warten

# Feinanfahrt (siehe ReferenzAchse.fahre_abs). Bei voller Leistung läuft der Motor nach
# dem Stopp noch ein ganzes Stück nach – ein Tippschritt von 5 Impulsen wurde so zu ~20.
# Deshalb: die letzten FEIN_STRECKE Impulse und alle kurzen Fahrten langsam.
FEIN_STRECKE = 60               # so viele Impulse vor dem Ziel auf langsam umschalten
FEIN_GESCHWINDIGKEIT = 320      # langsame Startleistung (0..512); die Anlaufhilfe erhöht sie bei Bedarf
ZIEL_TOLERANZ = 1               # so nah muss die Achse am Ziel stehen
# Anlaufhilfe: Zählt der Encoder so lange keinen Impuls, Leistung um ANLAUF_SCHRITT erhöhen.
# Die Leistung, mit der die Achse angelaufen ist, wird je Richtung gemerkt (Y hoch/runter
# brauchen wegen der Last unterschiedlich viel).
ANLAUF_PRUEF_S = 0.4
ANLAUF_SCHRITT = 80
STILLSTAND_MAX_S = 1.5          # auch mit voller Leistung so lange kein Impuls -> Fehler
KORREKTUREN_MAX = 3             # so oft nach dem Feinanfahren nochmals nachstellen


class _Achse:
    """Gemeinsame Basis (C++: TxtAxis)."""

    def __init__(self, name, hw, motor, nothalt=None):
        self.name = name
        self.hw = hw
        self.motor = motor
        self.nothalt = nothalt              # threading.Event, gemeinsam für alle Achsen
        self.geschwindigkeit = cfg.GESCHWINDIGKEIT
        self.status = "NOREF"
        self._stop_angefordert = False

    def stop(self):
        """Laufende Bewegung abbrechen (C++: stopReq = true)."""
        self._stop_angefordert = True

    def _aus(self):
        self.hw.motor(self.motor, 0)

    def _nothalt_aktiv(self):
        return self.nothalt is not None and self.nothalt.is_set()

    def _start_pruefen(self):
        """Vor jeder Bewegung: bei aktivem Not-Halt gar nicht erst losfahren."""
        self._stop_angefordert = False
        if self._nothalt_aktiv():
            self._aus()
            raise AchsenFehler(f"{self.name}: Not-Halt aktiv")

    def _pruefe_stop(self):
        if self._stop_angefordert or self._nothalt_aktiv():
            self._stop_angefordert = False
            self._aus()
            return True
        return False


# ===========================================================================
class ReferenzAchse(_Achse):
    """Achse mit Encoder und einem Referenztaster (C++: TxtAxis1RefSwitch).

    Position 0 = am Referenztaster. Positive Positionen = vom Taster weg.
    """

    def __init__(self, name, hw, motor, ref_taster, pos_ende, nothalt=None):
        super().__init__(name, hw, motor, nothalt)
        self.ref_taster = ref_taster
        self.pos_ende = pos_ende
        self.pos = 0
        self._richtung = 0          # +1 / -1 der letzten Fahrt (für den Nachlauf)
        self._gelesen = None        # zuletzt gelesener Zählerstand dieser Fahrt
        self.nachlauf = {-1: None, +1: None}  # gelernter Nachlauf der Feinfahrt je Richtung (Impulse)
        self.fein_v = {-1: FEIN_GESCHWINDIGKEIT, +1: FEIN_GESCHWINDIGKEIT}   # gemerkte Anlaufleistung

    # --- Hilfsfunktionen ---------------------------------------------------
    def _zaehler_ruhig(self):
        """Zählerstand erst lesen, wenn die Achse wirklich steht.

        Nach dem Abschalten läuft der Motor noch kurz nach, und der Zählerstand
        kommt per WLAN verspätet an. Früher wurde nach festen 50 ms gelesen und
        beim nächsten Befehl der Zähler zurückgesetzt – die restlichen Impulse
        gingen verloren. Bei vielen kleinen Fahrten (Einlernen!) summierte sich
        das auf viele Impulse. Jetzt: lesen, bis sich der Wert mehrmals nicht
        mehr ändert (höchstens ZAEHLER_RUHE_MAX_S)."""
        start = time.time()
        letzter, gleich = self.hw.zaehler(self.motor), 0
        while time.time() - start < ZAEHLER_RUHE_MAX_S:
            time.sleep(ZAEHLER_RUHE_TAKT_S)
            wert = self.hw.zaehler(self.motor)
            if wert == letzter:
                gleich += 1
                if gleich >= ZAEHLER_RUHE_LESUNGEN:
                    break
            else:
                letzter, gleich = wert, 0
        return letzter

    def _nachlauf_nachtragen(self):
        """Impulse, die nach dem letzten Lesen noch dazugekommen sind, der
        letzten Fahrtrichtung zuschlagen, bevor der Zähler zurückgesetzt wird."""
        if self._richtung and self._gelesen is not None:
            rest = self.hw.zaehler(self.motor) - self._gelesen
            if rest > 0:
                self.pos = max(self.pos + self._richtung * rest, 0)
        self._richtung, self._gelesen = 0, None

    def taster_gedrueckt(self):
        return self.hw.eingang(self.ref_taster) == 1

    def _links(self):
        # C++ TxtAxis::setMotorLeft – nie gegen den gedrückten Taster fahren
        if self.taster_gedrueckt():
            self._aus()
        else:
            self.hw.motor(self.motor, +self._v())

    def _v(self):
        """Leistung der laufenden Fahrt (Feinanfahrt langsamer)."""
        return getattr(self, "_v_fahrt", None) or self.geschwindigkeit

    def _rechts(self):
        # C++ TxtAxis1RefSwitch::setMotorRight – nie über die Endlage hinaus
        if self.pos >= self.pos_ende:
            self._aus()
        else:
            self.hw.motor(self.motor, -self._v())

    # --- Referenzfahrt (C++: moveRef) --------------------------------------
    def referenzfahrt(self):
        self._start_pruefen()
        self._richtung, self._gelesen = 0, None
        self._v_fahrt = None                          # Referenzfahrt immer mit voller Leistung
        self.hw.zaehler_reset(self.motor)
        self.hw.motor_strecke(self.motor, 0)          # keine Streckenbegrenzung
        self._links()
        self.status = "MOVING_REF"
        start = time.time()
        while True:
            if self.taster_gedrueckt():
                self._aus()
                break
            if self._pruefe_stop():
                self.status = "NOREF"
                raise AchsenFehler(f"{self.name}: Referenzfahrt abgebrochen")
            if time.time() - start > cfg.TIMEOUT_REFERENZ_S:
                self._aus()
                self.status = "NOREF"
                raise AchsenFehler(f"{self.name}: Referenztaster I{self.ref_taster} nach "
                                   f"{cfg.TIMEOUT_REFERENZ_S:.0f} s nicht erreicht")
            time.sleep(cfg.ZYKLUS_S)
        self._zaehler_ruhig()                         # Nachlauf abwarten: 0 = Stillstandsposition
        self.hw.zaehler_reset(self.motor)
        self.pos = 0
        self._richtung, self._gelesen = 0, None
        self.status = "READY"

    # --- Absolut fahren (C++: moveAbs) -------------------------------------
    def fahre_abs(self, ziel):
        ziel = int(ziel)
        if ziel < 0:
            raise AchsenFehler(f"{self.name}: Zielposition {ziel} < 0")
        if ziel >= self.pos_ende:
            # C++: moveRight bricht mit AXIS_ERROR ab, wenn posa + steps >= posEnd
            raise AchsenFehler(f"{self.name}: Zielposition {ziel} >= Endlage {self.pos_ende}")
        # 1. Grobfahrt mit voller Leistung bis FEIN_STRECKE vor das Ziel
        delta = ziel - self.pos
        if abs(delta) > 2 * FEIN_STRECKE:
            schritte = abs(delta) - FEIN_STRECKE
            self._fahre(schritte, nach_links=delta < 0, v=self.geschwindigkeit)
        # 2. Feinanfahrt langsam, danach höchstens KORREKTUREN_MAX-mal nachstellen
        for _ in range(1 + KORREKTUREN_MAX):
            delta = ziel - self.pos
            if abs(delta) <= ZIEL_TOLERANZ:
                break
            if delta < 0 and self.pos == 0 and self.taster_gedrueckt():
                break                                   # steht schon am Referenztaster
            richtung = -1 if delta < 0 else +1
            # Vorhalt: um den gelernten Nachlauf früher abschalten
            befehl = max(1, abs(delta) - int(round(self.nachlauf[richtung] or 0)))
            vorher = self.pos
            self._fahre(befehl, nach_links=delta < 0, v=min(self.fein_v[richtung], self.geschwindigkeit))
            if self.pos != 0:                           # nicht am Referenztaster abgebrochen
                ueber = abs(self.pos - vorher) - befehl
                alt = self.nachlauf[richtung]
                neu = max(0, ueber)
                self.nachlauf[richtung] = neu if alt is None else 0.6 * alt + 0.4 * neu
        return True

    def fahre_rel(self, delta):
        """Relativ fahren (C++: moveRel) – z. B. für die Kalibrierung."""
        if self.status != "READY":
            raise AchsenFehler(f"{self.name}: nicht bereit (Status {self.status})")
        return self.fahre_abs(max(0, min(self.pos_ende - 1, self.pos + int(delta))))

    def _fahre(self, schritte, nach_links, v=None):
        """C++: moveLeft / moveRight. v = Leistung dieser Fahrt (Standard: volle)."""
        self._v_fahrt = v
        if self.status == "NOREF":
            raise AchsenFehler(f"{self.name}: keine Referenz – zuerst Referenzfahrt ausführen")
        if self.status != "READY":
            raise AchsenFehler(f"{self.name}: nicht bereit (Status {self.status})")

        self._start_pruefen()
        self._nachlauf_nachtragen()
        start_pos = self.pos
        self.hw.zaehler_reset(self.motor)
        self.hw.motor_strecke(self.motor, schritte)   # TXT hält nach 'schritte' Impulsen an
        if nach_links:
            self._links()
            self.status = "MOVING_LEFT"
        else:
            self._rechts()
            self.status = "MOVING_RIGHT"

        start = time.time()
        abgebrochen = False
        richtung = -1 if nach_links else +1
        letzter_wert, letzte_bewegung = 0, start
        while not self.hw.motor_fertig(self.motor):
            # --- Anlaufhilfe: bewegt sich die Achse nicht, mehr Leistung geben
            jetzt = time.time()
            wert = self.hw.zaehler(self.motor)
            if wert != letzter_wert:
                letzter_wert, letzte_bewegung = wert, jetzt
            elif self._v() < self.geschwindigkeit and jetzt - letzte_bewegung > ANLAUF_PRUEF_S:
                self._v_fahrt = min(self.geschwindigkeit, self._v() + ANLAUF_SCHRITT)
                if self._v_fahrt > self.fein_v[richtung]:
                    self.fein_v[richtung] = self._v_fahrt          # fürs nächste Mal merken
                (self._links if nach_links else self._rechts)()
                letzte_bewegung = jetzt
            elif jetzt - letzte_bewegung > STILLSTAND_MAX_S:
                self._aus()
                self.status = "NOREF"
                raise AchsenFehler(f"{self.name}: Motor läuft auch mit voller Leistung nicht an – "
                                   "blockiert, Kabel lose oder Encoder zählt nicht?")
            if nach_links and self.taster_gedrueckt():
                # Unterwegs Referenztaster erreicht -> Position ist sicher 0
                self._aus()
                self._zaehler_ruhig()
                self.hw.zaehler_reset(self.motor)
                self.pos = 0
                self._richtung, self._gelesen = 0, None
                self.status = "READY"
                return
            if self._pruefe_stop():
                abgebrochen = True
                break
            if time.time() - start > cfg.TIMEOUT_FAHRT_S:
                self._aus()
                self.status = "NOREF"
                raise AchsenFehler(f"{self.name}: Fahrt nach {cfg.TIMEOUT_FAHRT_S:.0f} s nicht beendet "
                                   "(Motor blockiert oder Encoder zählt nicht?)")
            time.sleep(cfg.ZYKLUS_S)

        self._aus()
        gefahren = self._zaehler_ruhig()              # Nachlauf vollständig mitzählen
        self._richtung, self._gelesen = (-1 if nach_links else +1), gefahren
        if nach_links:
            self.pos = max(start_pos - gefahren, 0)
        else:
            self.pos = start_pos + gefahren
            if self.pos >= self.pos_ende:
                self.status = "NOREF"
                raise AchsenFehler(f"{self.name}: Endlage {self.pos_ende} überschritten (Position {self.pos})")
        self.status = "READY"
        if abgebrochen:
            raise AchsenFehler(f"{self.name}: Fahrt abgebrochen (Position {self.pos})")


# ===========================================================================
class EndschalterAchse(_Achse):
    """Achse ohne Encoder, fährt bis zu einem Endtaster (C++: TxtAxisNSwitch).
    Beim HBW der Ausleger: Taster 0 = eingefahren, Taster 1 = ausgefahren."""

    def __init__(self, name, hw, motor, taster, nothalt=None):
        super().__init__(name, hw, motor, nothalt)
        self.taster = list(taster)
        self.status = "READY"

    def taster_gedrueckt(self, idx):
        return self.hw.eingang(self.taster[idx]) == 1

    def fahre_zu(self, idx):
        """C++: moveS2X(idx)."""
        self._start_pruefen()
        if self.taster_gedrueckt(idx):
            self._aus()
            self.status = "READY"
            return
        if idx == 0:
            self.hw.motor(self.motor, +self.geschwindigkeit)   # setMotorLeft
        else:
            self.hw.motor(self.motor, -self.geschwindigkeit)   # setMotorRight
        self.status = "MOVING"
        start = time.time()
        while True:
            if self.taster_gedrueckt(idx):
                self._aus()
                break
            if self._pruefe_stop():
                self.status = "READY"
                raise AchsenFehler(f"{self.name}: Bewegung abgebrochen")
            if time.time() - start > cfg.TIMEOUT_FAHRT_S:
                self._aus()
                self.status = "READY"
                raise AchsenFehler(f"{self.name}: Taster I{self.taster[idx]} nach "
                                   f"{cfg.TIMEOUT_FAHRT_S:.0f} s nicht erreicht")
            time.sleep(cfg.ZYKLUS_S)
        self.status = "READY"

    def einfahren(self):   # C++: moveS1()
        self.fahre_zu(0)

    def ausfahren(self):   # C++: moveS2()
        self.fahre_zu(1)


# ===========================================================================
class Foerderband:
    """C++: TxtConveyorBeltLightBarriers. Das Original fährt rein zeitgesteuert –
    die Lichtschranken I1/I4 werden dort nicht ausgewertet (hier nur angezeigt)."""

    def __init__(self, hw, motor, nothalt=None):
        self.hw = hw
        self.motor = motor
        self.nothalt = nothalt
        self.geschwindigkeit = cfg.GESCHWINDIGKEIT
        self._stop_angefordert = False

    def stop(self):
        self._stop_angefordert = True

    def _laufen(self, v, dauer):
        self._stop_angefordert = False
        if self.nothalt is not None and self.nothalt.is_set():
            raise AchsenFehler("Förderband: Not-Halt aktiv")
        self.hw.motor(self.motor, v)
        ende = time.time() + dauer
        try:
            while time.time() < ende:
                if self._stop_angefordert or (self.nothalt is not None and self.nothalt.is_set()):
                    self._stop_angefordert = False
                    raise AchsenFehler("Förderband: abgebrochen")
                time.sleep(cfg.ZYKLUS_S)
        finally:
            self.hw.motor(self.motor, 0)

    def rein(self):    # C++: moveIn()  -> moveLeft, 1000 ms
        self._laufen(+self.geschwindigkeit, cfg.BAND_REIN_S)

    def raus(self):    # C++: moveOut() -> moveRight, 1200 ms
        self._laufen(-self.geschwindigkeit, cfg.BAND_RAUS_S)
