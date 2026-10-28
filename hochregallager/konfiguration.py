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

# ---------------------------------------------------------------------------
# MQTT (optional) – nur nötig, wenn das Python-HBW mit der restlichen
# Lernfabrik (Sauggreifer/VGR auf dem Haupt-TXT) zusammenarbeiten soll.
# Werte aus TxtFactoryClient/src/main.cpp bzw. MqttInterface.md.
# ---------------------------------------------------------------------------
MQTT_HOST = "192.168.0.10"
MQTT_PORT = 1883
MQTT_BENUTZER = "txt"
MQTT_PASSWORT = "xtx"
