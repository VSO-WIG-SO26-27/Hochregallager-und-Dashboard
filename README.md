# Hochregallager und Dashboard

Python-Steuerung für die fischertechnik Lernfabrik 4.0 (Modul Fördertechnik). Das Repo enthält zwei **voneinander unabhängige** Programmstände. Jeder Ordner ist für sich vollständig (eigener Code, eigene `daten/`, eigenes `start.py`) und wird aus seinem eigenen Ordner heraus gestartet.

| Ordner | Inhalt | Stationen |
|---|---|---|
| [`Greifer und HRL/`](Greifer%20und%20HRL/) | Bisheriger Stand dieses Repos | Hochregallager (HBW) + Sauggreifer mit Ein-/Ausgabe (VGR/DPS) |
| [`Alle Elemente/`](Alle%20Elemente/) | Kompletter Inhalt aus [Greifarm-Viola-und-Laurens](https://github.com/VSO-WIG-SO26-27/Greifarm-Viola-und-Laurens) | HBW, VGR/DPS, Bearbeitungsstation (MPO), Sortierstrecke (SLD) |

## Starten

```
cd "Greifer und HRL"
python3 start.py --sim        # Simulation, Dashboard unter http://127.0.0.1:8000
```

```
cd "Alle Elemente"
python3 start.py --sim        # Simulation aller vier Stationen
```

Sollen beide gleichzeitig laufen, einem davon einen anderen Port geben, z. B. `python3 start.py --sim --port 8001`.
Details zu Vorbereitung, Hardwaretest und Bedienung stehen im README des jeweiligen Ordners.

In VS Code gibt es unter *Ausführen und Debuggen* fertige Startkonfigurationen für beide Ordner (`.vscode/launch.json`).
