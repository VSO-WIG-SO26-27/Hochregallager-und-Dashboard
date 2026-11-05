# Hochregallager und Dashboard

Python-Steuerung für die fischertechnik Lernfabrik 4.0 (Modul Fördertechnik): Hochregallager und Sauggreifer mit Ein-/Ausgabe, mit Dashboard im Browser.

| Ordner | Wofür | Starten |
|---|---|---|
| [`Hauptprogramm/`](Hauptprogramm/) | Betrieb der Anlage: Ein-/Auslagern, Lagerlogik, Kennzahlen, Dashboard | `cd Hauptprogramm` → `python3 start.py` (ohne Anlage: `--sim`) |
| [`Kalibrierprogramm/`](Kalibrierprogramm/) | Positionen des Regalbediengeräts je Fach einlernen | `cd Kalibrierprogramm` → `python3 start.py` (ohne Anlage: `--sim`) |

Immer nur eines der beiden Programme mit der Anlage verbinden. Eingelernte Positionen stehen danach in `Kalibrierprogramm/positionen.json` und werden von Hand in `Hauptprogramm/code/konfiguration.py` (`RBG_POSITIONEN`) eingetragen.

Benötigt: Python 3 und `pip3 install ftrobopy`. Details stehen im README des jeweiligen Ordners.
