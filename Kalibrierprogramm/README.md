# Kalibrierprogramm (RBG-Einlernen)

Eigenständiges Werkzeug, mit dem ihr an der Anlage für jeden Lagerplatz des Hochregallagers (A1 … C3) und für die Übergabe am Förderband (CV) die **Grundhöhe** einlernt.

Das Werkzeug ist vom Hauptprogramm getrennt: Es hat eine eigene Kopie der Hardwareanbindung (`rbg/`, aus `Hauptprogramm/code`) und schreibt nur in `positionen.json` in diesem Ordner. Die Werte werden danach von Hand fest ins Hauptprogramm übernommen: `Hauptprogramm/code/konfiguration.py` → `RBG_POSITIONEN` (X, Grundhöhe je Platz) und `RBG_HUB`.

## Die zwei Höhen

| Höhe | Bedeutung | Wert |
|---|---|---|
| **Grundhöhe (G)** | genau auf Lagerplatzhöhe: der ausgefahrene Ausleger liegt am Behälter an, hebt ihn aber noch nicht | wird eingelernt |
| **Hebehöhe (H)** | Behälter ist angehoben | G − 40 (fest, kleinere Y-Werte = höher) |

Aufnehmen geht immer von G nach H, Abstellen immer von H nach G:

| | Aufnehmen | Abstellen |
|---|---|---|
| **Auslagern** (Fach → Band) | Fach: G anfahren → ausfahren → hoch auf H → einfahren | Band: H anfahren → ausfahren → runter auf G → Band raus → einfahren |
| **Einlagern** (Band → Fach) | Band: G anfahren → ausfahren → Band rein → hoch auf H → einfahren | Fach: H anfahren → ausfahren → runter auf G → einfahren |

## Starten

Vorher das Hauptprogramm beenden – sonst steuern zwei Programme denselben TXT.

```
cd Kalibrierprogramm
python3 start.py          an der Anlage (HBW-TXT 192.168.0.12)
python3 start.py --sim    ohne Anlage ausprobieren
```
Das Dashboard öffnet sich unter http://127.0.0.1:8010/. Beim Start macht das RBG eine Referenzfahrt.
Optionen: `--ip`, `--port`, `--kein-browser`, `--zeitfaktor` (nur Simulation). Braucht nur `ftrobopy` (siehe `requirements.txt`).

## Ablauf

1. Platz anklicken → **Grundhöhe anfahren** (vorher automatische Referenzfahrt, abschaltbar)
2. **Ausleger aus**, mit kleinen Schritten hochtippen, bis sich der Behälter gerade hebt, dann zurück, bis er wieder aufliegt. X so, dass der Ausleger mittig unter dem Behälter steht.
3. **Als Grundhöhe speichern** (oder Enter), dann **Ausleger ein**
4. **Hebehöhe anfahren** zur Kontrolle
5. Alle 9 Fächer und das Band so einlernen. Danach **Auslagern testen** / **Einlagern testen** einzelner Fächer und **Alle 9 Fächer testen** (jedes Fach aus- und wieder einlagern).
6. Ab und zu **Zählung prüfen**: Das RBG fährt zum Referenztaster, zählt dabei mit und kommt zurück. Abweichung über ±5 → die seit der letzten Kontrolle eingelernten Plätze neu einlernen.

Schutzfunktionen: X fährt nur mit eingefahrenem Ausleger. Mit ausgefahrenem Ausleger lässt sich Y nur in kleinen Schritten bewegen. **STOPP** (oder Esc) schaltet sofort alle Motoren ab.

## Genauigkeit (rbg/achsen.py)

Gegenüber der ursprünglichen Achsenlogik verbessert:
- Der Nachlauf des Motors nach dem Stopp wird vollständig mitgezählt (früher gingen bei jeder Fahrt Impulse verloren – bei vielen kleinen Tippschritten summierte sich das zu einer großen Abweichung).
- Die letzten 60 Impulse vor dem Ziel und alle kurzen Fahrten laufen langsam, mit gelerntem Vorhalt für den Nachlauf.

Das Hauptprogramm benutzt für X und Y des RBG dieselbe Achsenlogik (`PraeziseAchse`), damit die eingelernten Höhen im Betrieb genauso getroffen werden.

## Ergebnis: positionen.json

```json
{
  "aktualisiert": "2026-11-05 10:12:03",
  "hub": 40,
  "positionen": {
    "A1": {"x": 783, "grundhoehe": 76, "eingelernt": "2026-11-05 10:02:41"},
    ...
    "CV": {"x": 22, "grundhoehe": 718, "eingelernt": "2026-11-05 10:11:10"}
  }
}
```
`"eingelernt": null` heißt: noch nicht eingelernt, der Wert ist nur ein Startwert. Die Datei wird nach jedem Speichern sofort geschrieben.
