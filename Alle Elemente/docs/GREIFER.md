# Greifer (VGR) mit Ein-/Ausgabestation (DPS) – Leitfaden der Greifer-Gruppe

Gruppe: Viola, Laurens · Modul Fördertechnik WS 2026/27

Dieses Dokument erklärt **unseren** Teil der Lernfabrik: den Vakuum-Sauggreifer und die Ein-/Ausgabestation.
Der Rest des Programms (Hochregallager, Ofen, Sortierstrecke) steht in der [README](../README.md).

## 1. Was macht der Greifer in der Fabrik?

Der Greifer ist die **Drehscheibe** der Lernfabrik. Er ist die einzige Station, die Werkstücke zwischen den anderen Stationen bewegt, und er gibt den anderen Stationen ihre Aufträge:

- **Einlagern:** Werkstück aus der Eingabe holen → Farbe messen → ins Hochregallager bringen. Bei unbekannter Farbe → Ablage für falsche Werkstücke.
- **Bestellen:** Werkstück aus dem Hochregallager holen → auf den Ofenschieber der Bearbeitungsstation legen → später aus der Ablage der Sortierstrecke abholen → in die Ausgabe legen.

Im Original heißt der Greifer **VGR** (Vacuum Gripper Robot) und die Ein-/Ausgabestation **DPS** (Delivery and Pickup Station). Beide hängen am selben TXT-Controller (192.168.0.13).

## 2. Hardware

| Anschluss | Funktion | Hinweis |
|---|---|---|
| M1 + C1, Taster I1 | Drehung (Achse X) | Endlage 1500 Impulse |
| M2 + C2, Taster I2 | Höhe (Achse Y) | Endlage 900, **immer zuerst referenzieren** (Arm hoch) |
| M3 + C3, Taster I3 | Arm vor/zurück (Achse Z) | Endlage 1100 |
| O7 | Kompressor | |
| O8 | Ventil Sauger | Ansaugen: Kompressor an, 2 s warten, Ventil auf |
| I7 | Lichtschranke Eingabe | **0** = Werkstück liegt drin |
| I8 | Farbsensor (analog, mV) | weiß < 850 ≤ rot < 1550 ≤ blau < 2000 (Standard) |
| C4 | Lichtschranke Ausgabe | **1** = frei |
| (USB/I2C) | NFC-Leser | im Python-Code noch **nicht** benutzt |

### Gespeicherte Punkte (x = Drehung, y = Höhe, z = Arm)

Jeder Punkt hat meist einen **Anfahrpunkt** mit einer „0“ am Ende (z. B. `DIN0`): Dort fährt der Greifer zuerst hin, dann senkt er sich auf den eigentlichen Punkt.

| Punkt | Wo? | Wofür |
|---|---|---|
| `DIN` | Eingabe | Werkstück aufnehmen |
| `DCS` | Farbsensor | Farbe messen |
| `DNFC` | NFC-Leser | (Tag lesen/schreiben – kommt noch) |
| `WDC` | Ablage falsche Farbe | Werkstücke mit unbekannter Farbe |
| `DOUT` | Ausgabe | bestellte Werkstücke abgeben |
| `HBW`, `HBW1` | Band am Hochregallager | Werkstück in den Behälter legen / herausholen |
| `MPO` | Ofenschieber der Bearbeitungsstation | Werkstück zur Bearbeitung abgeben |
| `SSD1`, `SSD2`, `SSD3` | Ablagen der Sortierstrecke (weiß, rot, blau) | fertiges Werkstück abholen |

Die Standardwerte stehen in `lernfabrik/konfiguration.py` (`VGR_STANDARD_POSITIONEN`). Kalibrierte Werte speichert das Dashboard in `daten/kalibrierung_greifer.json`.

## 3. Abläufe Schritt für Schritt (Zustandsautomat)

Der Greifer arbeitet als **Zustandsautomat**: Er ist immer in genau einem Zustand (z. B. `IDLE` = bereit) und wechselt nach festen Regeln in den nächsten. Die Zustandsnamen sind aus dem C++-Original übernommen (`TxtVacuumGripperRobotRun.cpp`). Im Code steht das alles in `lernfabrik/greifer.py`, Methode `_schritt()`.

**Einlagern**

| Zustand | Was passiert |
|---|---|
| `IDLE` | wartet; Lichtschranke I7 meldet 0,5 s lang ein Werkstück → los |
| `START_DELIVERY` | Werkstück aus der Eingabe ansaugen, zur NFC-Position |
| `COLOR_DETECTION` | zum Farbsensor, Farbe messen |
| `NFC_RAW` / `NFC_REJECTED` | Farbe erkannt / nicht erkannt (NFC wird noch nicht beschrieben) |
| `WRONG_COLOR` | unbekannte Farbe → Ablage `WDC` |
| `STORE_WP_VGR` | Hochregallager beauftragen: leeren Behälter bringen; zum Band fahren |
| `STORE_WP` | warten, bis der Behälter da ist; Werkstück ablegen; Lager lagert ein |

**Bestellen**

| Zustand | Was passiert |
|---|---|
| `FETCH_WP_VGR` | Hochregallager beauftragen: Werkstück der Farbe auslagern |
| `VGR_WAIT_FETCHED` | warten, Werkstück aus dem Behälter nehmen, Behälter zurück ins Regal |
| `MOVE_VGR2MPO` | Werkstück auf den Ofenschieber legen, Bearbeitungsstation beauftragen |
| `START_PRODUCE` | warten, bis die Bearbeitung gestartet ist → zurück nach `IDLE` (Greifer ist frei!) |
| `IDLE` | … Sortierstrecke meldet „sortiert, Farbe X“ → zur Ablage `SSD1/2/3` |
| `NFC_PRODUCED` | Werkstück ansaugen, zur NFC-Position (NFC kommt noch) |
| `MOVE_PICKUP_WAIT` | wartet, bis die Ausgabe frei ist (C4), legt das Werkstück ab |
| `MOVE_PICKUP` | Bestellung abgeschlossen → `IDLE` |

Im Dashboard kann man den Bestellweg auf **direkt** stellen. Dann geht es nach `VGR_WAIT_FETCHED` sofort zu `MOVE_PICKUP_WAIT`.

**Fehler:** Jeder Fehler (Achse erreicht Taster nicht, andere Station meldet Fehler …) führt in `FAULT`. Alle Motoren stoppen, der Sauger lässt los. Nach dem Beheben im Dashboard **quittieren**.

## 4. Wo steht was im Code?

| Was | Datei / Funktion |
|---|---|
| Anschlüsse, Standardpositionen, Farbschwellen | `lernfabrik/konfiguration.py` (Abschnitt „Vakuum-Sauggreifer“) |
| Ansaugen / Loslassen | `greifer.py`: `greifen()`, `loslassen()` |
| Einzelne Bewegungen | `greifer.py`: `fahre()`, `eingabe_greifen()`, `zum_farbsensor()`, `zur_bearbeitung()`, `zur_ablage()`, `ausgabe_ablegen()` … |
| Zustandsautomat | `greifer.py`: `_schritt()`, `_idle_schritt()` |
| Aufträge an andere Stationen | `greifer.py`: `_an_hbw()`, `verbinde_bearbeitung()`, `_mpo_quittung()`, `_sld_quittung()` |
| Achsen (Referenzfahrt, Position anfahren) | `lernfabrik/achsen.py`: `ReferenzAchse` |
| Simulation des Greifers | `lernfabrik/simulation.py`: `SimVGR` |
| Kalibrierdaten | `lernfabrik/daten.py`: `KalibrierungGreifer`, `KalibrierungFarbsensor` |

## 5. Ausprobieren und testen

```
py start.py --sim                        # Simulation aller Stationen, Dashboard öffnet sich
py hardwaretest.py --sim --station greifer
py -m pytest                             # automatische Tests (vorher: py -m pip install pytest)
```

An der Anlage (Rechner im Lernfabrik-WLAN, Originalprogramm auf dem TXT gestoppt):
```
py hardwaretest.py --station greifer     # zuerst! Reihenfolge 1, 2, 3, ... (Höhe zuerst)
py start.py --stationen hbw,vgr          # nur Lager + Greifer
py start.py                              # alle Stationen
```

## 6. Offene Aufgaben der Greifer-Gruppe

Grob nach Priorität:

1. **Prüfprotokoll an der Anlage:** Hardwaretest Greifer durchgehen, Ergebnisse festhalten (Datum, was geprüft, Ergebnis) z. B. in `docs/pruefprotokolle/`. Vorher die Original-Kalibrierdateien vom TXT sichern (`Data/Calib.VGR.json`, `Data/Calib.DPS.json`).
2. **Kalibrieren:** Alle Punkte im Dashboard prüfen, besonders die neuen Wege **`MPO`** und **`SSD1`–`SSD3`**. Die wurden noch nie mit diesem Code angefahren. Farbsensor mit unseren Werkstücken kalibrieren.
3. **NFC:** Mit Nick und Ibo (Repo `ssc-nfc-python`) abstimmen, welche Funktionen der Greifer aufruft (UID lesen, als RAW / REJECTED / PROCESSED schreiben). Der Leser hängt am Greifer-TXT, ist also per WLAN nicht direkt erreichbar. Dafür braucht es einen kleinen Dienst auf dem TXT. In `greifer.py` sind die Stellen markiert (`NFC_RAW`, `NFC_REJECTED`, `NFC_PRODUCED`).
4. **Statusmeldungen aufräumen:** `fahre()` setzt bei jeder einzelnen Bewegung BUSY und danach READY. Für eine spätere MQTT-Anbindung sollte BUSY für den ganzen Ablauf gelten.
5. **MQTT (falls die Gesamtgruppe das will):** Greifer-Status `f/i/state/vgr`, Bestellstatus `f/i/order`, Bestellungen `f/o/order`, Aufträge `fl/vgr/do` usw. wie im Original. Vorlage: `lernfabrik/mqtt_bruecke.py` (dort für das Hochregallager).
6. **Mit den anderen Gruppen abstimmen:** Ofen (Repo `MPO-…`) und Sortierung (Repo `Sortierstation-SLD`) haben eigene Gruppen. Unser Code enthält schon einen Vorschlag für beide Stationen. Klären, ob sie darauf aufbauen oder eigene Lösungen schreiben. Die Schnittstelle zum Greifer muss in jedem Fall passen.

## 7. Herkunft

Dieses Repo baut auf [Hochregallager-und-Dashboard](https://github.com/VSO-WIG-SO26-27/Hochregallager-und-Dashboard) auf (Hochregallager und erste Greifer-Steuerung, lkruse301). Dessen Commit-Historie ist hier enthalten. Größere Änderungen, die auch das Hochregallager betreffen, mit ihm abstimmen.
