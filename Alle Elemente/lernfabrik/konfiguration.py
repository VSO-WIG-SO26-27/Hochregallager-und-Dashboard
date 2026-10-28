"""
Zentrale Einstellungen für das Hochregallager (HBW = High-Bay Warehouse).

Alle Werte sind aus dem Original-C++-Code der fischertechnik Lernfabrik 4.0
übernommen (Repo fischertechnik/txt_training_factory). In Klammern steht
jeweils die Stelle im C++-Code, aus der der Wert stammt.

Nummerierung: Hier wird wie auf dem TXT gezählt (M1..M4, I1..I8, C1..C4).
Der C++-Code zählt ab 0 – dort ist z. B. "chM = 1" der Motor M2.
"""

# ---------------------------------------------------------------------------
# Verbindung
# ---------------------------------------------------------------------------
# Feste IP des HBW-TXT im Lernfabrik-Netz (doc/Network_Config.md: TXT2 HBW).
# Der Mac muss dafür im WLAN des TP-Link-Routers der Lernfabrik sein.
TXT_IP = "192.168.0.12"
TXT_PORT = 65000

# Feste IP des Greifer-TXT (TXT3 VGR+DPS) – steuert auch die Ein-/Ausgabestation
VGR_IP = "192.168.0.13"

# Port des Test-Dashboards im Browser: http://127.0.0.1:8000
WEB_PORT = 8000

# ---------------------------------------------------------------------------
# Pin-Belegung (TxtSmartFactoryLib/doc/PinAssignmentTXT.md + Konstruktor in
# TxtHighBayWarehouse.cpp)
# ---------------------------------------------------------------------------
# Achse X = horizontal (Regalspalten 1..3)
#   C++: axisX("HBW_X", pT, 1, 4, 2050)  -> M2, Referenztaster I5, Ende 2050
MOTOR_X = 2
REF_X = 5
ENDE_X = 2050

# Achse Y = vertikal (Regalzeilen A..C)
#   C++: axisY("HBW_Y", pT, 3, 7, 1050)  -> M4, Referenztaster I8, Ende 1050
MOTOR_Y = 4
REF_Y = 8
ENDE_Y = 1050

# Achse Z = Ausleger (fährt ins Regal / aufs Band)
#   C++: axisZ("HBW_Z", pT, 2, 5, 6)     -> M3, Taster eingefahren I6, ausgefahren I7
MOTOR_Z = 3
TASTER_Z_EINGEFAHREN = 6
TASTER_Z_AUSGEFAHREN = 7

# Förderband
#   C++: convBelt(pT, 0, 0, 3)           -> M1, Lichtschranken I1 (außen) / I4 (innen)
MOTOR_BAND = 1
LICHTSCHRANKE_AUSSEN = 1
LICHTSCHRANKE_INNEN = 4

# Die Encoder hängen an den Zählern mit derselben Nummer wie der Motor
# (M2 -> C2, M4 -> C4). Das ist beim TXT fest so verdrahtet.

# ---------------------------------------------------------------------------
# Bewegungsparameter
# ---------------------------------------------------------------------------
GESCHWINDIGKEIT = 512          # TxtAxis: speed(512), 512 = volle Leistung
TIMEOUT_REFERENZ_S = 20.0      # TxtAxis.h: TIMEOUT_S_MOVEREF
TIMEOUT_FAHRT_S = 20.0         # TxtAxis.h: TIMEOUT_S_MOVELEFT / MOVERIGHT
ZYKLUS_S = 0.01                # TxtAxis.h: CYCLE_MS_AXIS = 10 ms

# Hub beim Aufnehmen/Absetzen eines Behälters (TxtHighBayWarehouse.h: ydelta = 40)
Y_DELTA = 40

# Laufzeiten Förderband (TxtConveyorBelt.cpp: moveIn 1000 ms, moveOut 1200 ms)
BAND_REIN_S = 1.0
BAND_RAUS_S = 1.2

# ---------------------------------------------------------------------------
# Standard-Kalibrierung (TxtHighBayWarehouseCalibData::saveDefault)
# Wird nur benutzt, wenn noch keine daten/kalibrierung.json existiert.
# ---------------------------------------------------------------------------
STANDARD_KALIBRIERUNG = {
    "hbx": [780, 1390, 1995],   # X-Position Spalte 1, 2, 3
    "hby": [80, 445, 855],      # Y-Position Zeile A, B, C
    "conv": {"x": 20, "y": 720} # Übergabeposition am Förderband
}

# ===========================================================================
# Vakuum-Sauggreifer (VGR) und Ein-/Ausgabestation (DPS)
# Quelle: Konstruktor TxtVacuumGripperRobot.cpp, TxtDeliveryPickupStation.cpp,
#         PinAssignmentTXT.md (TXT 5)
# ===========================================================================
# Drehachse      C++: axisX("VGR_X", pT, 0, 0, 1500) -> M1, Encoder C1, Referenz I1
VGR_MOTOR_X, VGR_REF_X, VGR_ENDE_X = 1, 1, 1500
# Hubachse       C++: axisY("VGR_Y", pT, 1, 1, 900)  -> M2, Encoder C2, Referenz I2
VGR_MOTOR_Y, VGR_REF_Y, VGR_ENDE_Y = 2, 2, 900
# Auslegerarm    C++: axisZ("VGR_Z", pT, 2, 2, 1100) -> M3, Encoder C3, Referenz I3
VGR_MOTOR_Z, VGR_REF_Z, VGR_ENDE_Z = 3, 3, 1100
# Sauggreifer    C++: vgripper(pT, 6, 7)             -> Kompressor O7, Ventil O8
VGR_KOMPRESSOR, VGR_VENTIL = 7, 8
VGR_ANSAUGZEIT_S = 2.0          # TxtVacuumGripper::grip(): 2000 ms Kompressor vorlaufen lassen

# Ein-/Ausgabestation (DPS)
DPS_LICHTSCHRANKE_EINGABE = 7   # I7: 0 = Werkstück liegt in der Eingabe (C++: !is_DIN())
DPS_FARBSENSOR = 8              # I8: analog, Spannung in mV
DPS_LICHTSCHRANKE_AUSGABE = 4   # C4 (Zählereingang als Taster): 1 = Ausgabe ist frei

# Farbsensor-Schwellwerte (TxtDeliveryPickupStationCalibData::saveDefault)
#   200 <= mV < th1 -> weiß,  th1 <= mV < th2 -> rot,  th2 <= mV < 2000 -> blau
FARBE_TH1 = 850
FARBE_TH2 = 1550

# Standard-Positionen des Greifers (x = Drehung, y = Höhe, z = Arm)
# TxtVacuumGripperRobotCalibData::saveDefault – "...0" ist jeweils der Anfahrpunkt
VGR_STANDARD_POSITIONEN = {
    "DIN0": (22, 600, 19),    "DIN": (22, 758, 19),      # Eingabe (Delivery In)
    "WDC0": (300, 400, 0),    "WDC": (300, 600, 0),      # Ablage falsche Farbe
    "DCS0": (123, 500, 65),   "DCS": (123, 645, 75),     # Farbsensor
    "DNFC0": (200, 600, 250), "DNFC": (200, 643, 260),   # NFC-Leser
    "DOUT0": (263, 50, 280),  "DOUT": (263, 320, 590),   # Ausgabe (Delivery Out)
    "HBW0": (1410, 0, 0),     "HBW": (1410, 50, 186),    # Übergabe Hochregallager
    "HBW1": (1410, 170, 186),
    "MPO0": (931, 0, 880),    "MPO": (931, 490, 934),    # Bearbeitungsstation (später)
    "SSD10": (469, 300, 280), "SSD1": (469, 845, 380),   # Sortierstrecke (später)
    "SSD20": (390, 300, 280), "SSD2": (390, 845, 434),
    "SSD30": (316, 300, 280), "SSD3": (316, 845, 588),
}

# Werkstück nur annehmen, wenn die Eingabe-Lichtschranke so lange belegt ist
# (verhindert Fehlstarts durch kurzes Flackern)
DPS_ENTPRELLZEIT_S = 0.5

# ===========================================================================
# Bearbeitungsstation mit Ofen (MPO = Multi-Processing Station with Oven)
# Quelle: Konstruktor TxtMultiProcessingStation.cpp, TxtMultiProcessingStationRun.cpp,
#         PinAssignmentTXT.md (TXT 2,3)
#
# Die MPO hat ZWEI TXT: den Master (IP unten) und eine Erweiterung, die per
# Flachbandkabel am EXT-Anschluss des Masters hängt. Anschlüsse der Erweiterung
# werden hier wie im C++-Code mit +8 gezählt:  ERW + 5  = Eingang I5 an der Erweiterung.
# ===========================================================================
MPO_IP = "192.168.0.11"
ERW = 8

# Drehtisch   C++: axisRotTable("rotTable", pT, 0, 0, 1, 2)
#   Master M1, Taster I1 = Position Sauger (S1), I2 = Säge (S2), I3 = Band (S3)
MPO_MOTOR_TISCH = 1
MPO_TISCH_TASTER = (1, 2, 3)

# Werkzeug/Säge   C++: chMsaw = 1 -> Master M2
MPO_MOTOR_SAEGE = 2

# Förderband   C++: convBelt(pT, 2) -> Master M3, Lichtschranke Bandende Master I4 (0 = belegt)
MPO_MOTOR_BAND = 3
MPO_LICHTSCHRANKE_BANDENDE = 4

# Interner Sauggreifer (fährt zwischen Ofen und Drehtisch)
#   C++: axisGripper("gripper", pT, 8+1, 4, 8+2)
#   Motor = Erweiterung M2, S1 = Master I5 (am Drehtisch), S2 = Erweiterung I3 (am Ofen)
MPO_MOTOR_GREIFER = ERW + 2
MPO_GREIFER_TASTER = (5, ERW + 3)

# Ofenschieber   C++: axisOvenInOut("ovenInOut", pT, 8+0, 8+1, 8+0)
#   Motor = Erweiterung M1, S1 (außen) = Erweiterung I2, S2 (im Ofen) = Erweiterung I1
#   ACHTUNG: Die Hersteller-Pinliste nennt es umgekehrt (I1 = eingefahren). Wir folgen
#   dem C++-Code, der an der echten Anlage lief. Mit hardwaretest.py prüfen!
MPO_MOTOR_SCHIEBER = ERW + 1
MPO_SCHIEBER_TASTER = (ERW + 2, ERW + 1)

# Lichtschranke am Ofen (Erweiterung I5): 0 = Werkstück liegt auf dem Schieber
MPO_LICHTSCHRANKE_OFEN = ERW + 5

# Ausgänge Master: O7 Ventil Auswerfer, O8 Kompressor
MPO_VENTIL_AUSWERFER = 7
MPO_KOMPRESSOR = 8
# Ausgänge Erweiterung: O5 Vakuum, O6 Absenken, O7 Ofentür (an = offen), O8 Ofenlicht
MPO_VENTIL_VAKUUM = ERW + 5
MPO_VENTIL_SENKEN = ERW + 6
MPO_VENTIL_TUER = ERW + 7
MPO_OFENLICHT = ERW + 8

# Zeiten aus TxtMultiProcessingStationRun.cpp (Sekunden)
MPO_WARTE_OFEN_S = 5.0          # IDLE: so lange auf das Werkstück am Ofen warten
MPO_BRENNEN_BLINKEN = 14        # BURN: 14x Licht an/aus
MPO_BRENNEN_TAKT_S = 0.2
MPO_SAEGEN_S = 2.5              # TABLE_SAW: je Richtung
MPO_WARTE_BANDENDE_S = 10.0     # TRANSPORT: max. Wartezeit auf die Lichtschranke am Bandende
MPO_BAND_NACHLAUF_S = 2.0       # TRANSPORT: danach Band noch weiterlaufen lassen

# ===========================================================================
# Sortierstrecke mit Farberkennung (SLD = Sorting Line with Color Detection)
# Quelle: TxtSortingLine.cpp, TxtSortingLineRun.cpp, TxtSortingLineCalibData.cpp
# ===========================================================================
SLD_IP = "192.168.0.14"
SLD_MOTOR_BAND = 1                    # M1, läuft immer "rechts"
SLD_LICHTSCHRANKE_START = 1           # I1 vor dem Farbsensor (0 = Werkstück da)
SLD_FARBSENSOR = 2                    # I2 analog (mV) – kleiner Wert = hell
SLD_LICHTSCHRANKE_AUSWURF = 3         # I3 vor den Auswerfern -> Zählung startet
SLD_LICHTSCHRANKE_ABLAGE = {"WHITE": 6, "RED": 7, "BLUE": 8}   # I6..I8: 0 = Ablage belegt
SLD_ZAEHLER = 1                       # C1 Impulszähler am Band
SLD_VENTIL = {"WHITE": 5, "RED": 6, "BLUE": 7}                 # O5..O7 Auswerfer
SLD_KOMPRESSOR = 8                    # O8
SLD_AUSWERFEN_S = 0.5                 # ejectWhite/Red/Blue: Ventil 500 ms

# Standard-Kalibrierung (TxtSortingLineCalibData::saveDefault)
#   Farbe = KLEINSTER Messwert während der Vorbeifahrt
#   200 <= mV < th1 -> weiß, th1 <= mV < th2 -> rot, th2 <= mV < 2000 -> blau
SLD_FARBE_TH1 = 940
SLD_FARBE_TH2 = 1430
# Wie viele Zähl-Flanken nach der Auswurf-Lichtschranke ausgeworfen wird
SLD_ZAEHLER_AUSWURF = {"WHITE": 5, "RED": 15, "BLUE": 26}
SLD_ZAEHLER_FALSCH = 29               # COUNT_WRONG: unbekannte Farbe -> Fehler
# Das Original zählt jede Zustandsänderung von C1 (steigende UND fallende Flanke).
# Der Hardware-Zähler des TXT zählt Impulse. Faktor 2 rechnet Impulse in Flanken um.
# An der Anlage prüfen (hardwaretest.py --station sortierung)!
SLD_FLANKEN_PRO_IMPULS = 2
# Ergänzungen zum Original (dort hängt die Station sonst endlos):
SLD_TIMEOUT_FARBE_S = 10.0            # max. Zeit vom Start bis zur Auswurf-Lichtschranke
SLD_TIMEOUT_ZAEHLEN_S = 10.0          # max. Zeit bis zum Auswurf

# ---------------------------------------------------------------------------
# MQTT (optional) – nur nötig, wenn das Python-HBW mit der restlichen
# Lernfabrik (Sauggreifer/VGR auf dem Haupt-TXT) zusammenarbeiten soll.
# Werte aus TxtFactoryClient/src/main.cpp bzw. MqttInterface.md.
# ---------------------------------------------------------------------------
MQTT_HOST = "192.168.0.10"
MQTT_PORT = 1883
MQTT_BENUTZER = "txt"
MQTT_PASSWORT = "xtx"
