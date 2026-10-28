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

# ---------------------------------------------------------------------------
# MQTT (optional) – nur nötig, wenn das Python-HBW mit der restlichen
# Lernfabrik (Sauggreifer/VGR auf dem Haupt-TXT) zusammenarbeiten soll.
# Werte aus TxtFactoryClient/src/main.cpp bzw. MqttInterface.md.
# ---------------------------------------------------------------------------
MQTT_HOST = "192.168.0.10"
MQTT_PORT = 1883
MQTT_BENUTZER = "txt"
MQTT_PASSWORT = "xtx"
