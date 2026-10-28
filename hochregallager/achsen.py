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

    # --- Hilfsfunktionen ---------------------------------------------------
    def taster_gedrueckt(self):
        return self.hw.eingang(self.ref_taster) == 1

    def _links(self):
        # C++ TxtAxis::setMotorLeft – nie gegen den gedrückten Taster fahren
        if self.taster_gedrueckt():
            self._aus()
        else:
            self.hw.motor(self.motor, +self.geschwindigkeit)

    def _rechts(self):
        # C++ TxtAxis1RefSwitch::setMotorRight – nie über die Endlage hinaus
        if self.pos >= self.pos_ende:
            self._aus()
        else:
            self.hw.motor(self.motor, -self.geschwindigkeit)

    # --- Referenzfahrt (C++: moveRef) --------------------------------------
    def referenzfahrt(self):
        self._start_pruefen()
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
        self.hw.zaehler_reset(self.motor)
        self.pos = 0
        self.status = "READY"

    # --- Absolut fahren (C++: moveAbs) -------------------------------------
    def fahre_abs(self, ziel):
        ziel = int(ziel)
        if ziel < 0:
            raise AchsenFehler(f"{self.name}: Zielposition {ziel} < 0")
        if ziel >= self.pos_ende:
            # C++: moveRight bricht mit AXIS_ERROR ab, wenn posa + steps >= posEnd
            raise AchsenFehler(f"{self.name}: Zielposition {ziel} >= Endlage {self.pos_ende}")
        if ziel == self.pos:
            return True
        if ziel > self.pos:
            self._fahre(ziel - self.pos, nach_links=False)
        else:
            self._fahre(self.pos - ziel, nach_links=True)
        return True

    def fahre_rel(self, delta):
        """Relativ fahren (C++: moveRel) – z. B. für die Kalibrierung."""
        if self.status != "READY":
            raise AchsenFehler(f"{self.name}: nicht bereit (Status {self.status})")
        return self.fahre_abs(max(0, min(self.pos_ende - 1, self.pos + int(delta))))

    def _fahre(self, schritte, nach_links):
        """C++: moveLeft / moveRight."""
        if self.status == "NOREF":
            raise AchsenFehler(f"{self.name}: keine Referenz – zuerst Referenzfahrt ausführen")
        if self.status != "READY":
            raise AchsenFehler(f"{self.name}: nicht bereit (Status {self.status})")

        self._start_pruefen()
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
        while not self.hw.motor_fertig(self.motor):
            if nach_links and self.taster_gedrueckt():
                # Unterwegs Referenztaster erreicht -> Position ist sicher 0
                self._aus()
                self.hw.zaehler_reset(self.motor)
                self.pos = 0
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
        time.sleep(0.05)                              # Nachlauf noch mitzählen
        gefahren = self.hw.zaehler(self.motor)
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
