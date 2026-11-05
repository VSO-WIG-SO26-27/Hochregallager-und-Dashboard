"""
Auftragsverwaltung (Warteschlange) – Leitstand-Ebene über Greifer und Hochregallager.

  * bis zu cfg.AUFTRAEGE_MAX offene Aufträge (wartend + aktiv)
  * Auslageraufträge kommen aus dem Dashboard (Farbe, Priorität normal/eilig)
  * Einlageraufträge entstehen automatisch, sobald ein Werkstück in der Eingabe liegt
  * Reihenfolge: eilig vor normal, dann Vorrang (Einlagerung vor Auslagerung oder
    umgekehrt, einstellbar), dann nach Eingang (FIFO)
  * Verfügbarkeitsprüfung: Es werden nur so viele Auslagerungen einer Farbe
    angenommen, wie Werkstücke im Lager sind (Bestand minus offene Aufträge)

Den Ablauf führt der Greifer aus (greifer.py): Ist er frei, holt er sich mit
naechster() den nächsten ausführbaren Auftrag.
"""

import collections
import itertools
import threading
import time

from . import konfiguration as cfg
from .daten import FARBEN
from .steuerung import ACK_FETCHED, ACK_STORED

ART_TEXT = {"EIN": "Einlagerung", "AUS": "Auslagerung"}
STATUS_TEXT = {"WARTEND": "wartet", "AKTIV": "in Arbeit", "FERTIG": "erledigt",
               "STORNIERT": "storniert", "ABGEBROCHEN": "abgebrochen", "AUSGESCHLEUST": "ausgeschleust"}


class Auftragsverwaltung:
    kuerzel = "LVS"

    def __init__(self, hbw, vgr=None):
        self.hbw = hbw
        self.vgr = vgr
        self.einst = hbw.einst
        self._lock = threading.RLock()
        self._nr = itertools.count(1)
        self.offen = []                                  # wartende + aktive Aufträge
        self.erledigt = collections.deque(maxlen=15)
        self.hinweis = ""
        hbw.bei_quittung.append(self._hbw_quittung)
        hbw.bei_fehler.append(lambda st, text: self.aktiven_abbrechen("HBW", text))
        hbw.stillstand = self.stillstand
        if vgr is not None:
            vgr.bei_fehler.append(lambda st, text: self.aktiven_abbrechen("VGR", text))

    def log(self, text):
        (self.vgr or self.hbw).log(f"[Aufträge] {text}")

    # =======================================================================
    # Aufträge anlegen
    # =======================================================================
    def _neu(self, art, farbe, prio="normal", quelle="dashboard"):
        a = {"nr": next(self._nr), "art": art, "farbe": farbe, "prio": prio, "status": "WARTEND",
             "quelle": quelle, "erstellt": time.time(), "gestartet": None, "ende": None,
             "phase": "", "text": ""}
        self.offen.append(a)
        return a

    def _verfuegbar(self, farbe):
        """Bestand im Regal (inkl. Band) minus offene Auslagerungen, deren Werkstück noch im Lager ist."""
        reserviert = sum(1 for a in self.offen if a["art"] == "AUS" and a["farbe"] == farbe
                         and a["phase"] not in ("ENTNOMMEN",))
        return self.hbw.lager.anzahl(farbe) - reserviert

    def bestellen(self, farbe, prio="normal", quelle="dashboard"):
        if farbe not in FARBEN:
            return False, "Bitte Farbe wählen"
        if prio not in ("normal", "eilig"):
            prio = "normal"
        with self._lock:
            if len(self.offen) >= cfg.AUFTRAEGE_MAX:
                return False, f"Warteschlange voll (max. {cfg.AUFTRAEGE_MAX} offene Aufträge)"
            if self._verfuegbar(farbe) <= 0:
                return False, f"Nicht genug Bestand: kein freies Werkstück {farbe} mehr im Lager"
            a = self._neu("AUS", farbe, prio, quelle)
        self.log(f"Auftrag {a['nr']} angelegt: Auslagerung {farbe} ({prio})")
        return True, f"Auftrag {a['nr']}: Auslagerung angelegt"

    def einlagerung_anmelden(self, quelle="Eingabe"):
        """Greifer meldet: Werkstück liegt in der Eingabe. Legt höchstens einen Einlagerauftrag an."""
        with self._lock:
            if any(a["art"] == "EIN" and a["status"] in ("WARTEND", "AKTIV") and a["phase"] in ("", "EINGABE")
                   for a in self.offen):
                return None
            if len(self.offen) >= cfg.AUFTRAEGE_MAX:
                if self.hinweis != "voll":
                    self.log("Warteschlange voll – Werkstück in der Eingabe wartet")
                self.hinweis = "voll"
                return None
            self.hinweis = ""
            a = self._neu("EIN", "", "normal", quelle)
            a["phase"] = "EINGABE"
        self.log(f"Auftrag {a['nr']} angelegt: Einlagerung (Werkstück in der Eingabe)")
        return a

    def einlagerung_abmelden(self):
        """Werkstück wurde aus der Eingabe genommen, bevor der Greifer es geholt hat."""
        with self._lock:
            for a in list(self.offen):
                if a["art"] == "EIN" and a["status"] == "WARTEND":
                    self._beenden(a, "STORNIERT", "Werkstück aus der Eingabe entfernt")

    # =======================================================================
    # Disposition
    # =======================================================================
    def _ausfuehrbar(self, a):
        lager = self.hbw.lager
        if a["art"] == "AUS":
            if not lager.hat_farbe(a["farbe"]):
                return False, f"kein Werkstück {a['farbe']} im Regal"
            return True, ""
        if not lager.hat_leeren_behaelter():
            return False, "kein leerer Behälter im Lager"
        return True, ""

    def reihenfolge(self):
        vorrang = self.einst["vorrang"]
        return sorted((a for a in self.offen if a["status"] == "WARTEND"),
                      key=lambda a: (0 if a["prio"] == "eilig" else 1, 0 if a["art"] == vorrang else 1, a["nr"]))

    def naechster(self):
        """Nächster ausführbarer Auftrag (oder None). Blockierte Aufträge bekommen einen Hinweis."""
        with self._lock:
            for a in self.reihenfolge():
                ok, grund = self._ausfuehrbar(a)
                a["text"] = "" if ok else f"blockiert: {grund}"
                if ok:
                    return a
        return None

    def starten(self, a):
        with self._lock:
            a["status"], a["gestartet"], a["text"] = "AKTIV", time.time(), ""
        self.log(f"Auftrag {a['nr']} gestartet: {ART_TEXT[a['art']]} {a['farbe']}")

    def phase(self, a, phase, farbe=None):
        with self._lock:
            a["phase"] = phase
            if farbe:
                a["farbe"] = farbe

    def _beenden(self, a, status, text=""):
        a["status"], a["ende"], a["text"] = status, time.time(), text
        if a in self.offen:
            self.offen.remove(a)
        self.erledigt.appendleft(a)
        if status == "FERTIG" and a["gestartet"]:
            self.hbw.kpi.durchlauf(ART_TEXT[a["art"]], a["ende"] - a["gestartet"])

    def abschliessen(self, a, status="FERTIG", text=""):
        with self._lock:
            if a["status"] in ("WARTEND", "AKTIV"):
                self._beenden(a, status, text)
        self.log(f"Auftrag {a['nr']} {STATUS_TEXT.get(status, status)}{': ' + text if text else ''}")

    def aktiven_abbrechen(self, wer, text):
        with self._lock:
            # Nach der Übergabe ans HBW hängt ein Einlagerauftrag nicht mehr am Greifer
            aktive = [a for a in self.offen if a["status"] == "AKTIV"
                      and not (wer == "VGR" and a["phase"] == "UEBERGEBEN")]
            for a in aktive:
                self._beenden(a, "ABGEBROCHEN", f"Fehler {wer}: {text}")
        for a in aktive:
            self.log(f"Auftrag {a['nr']} abgebrochen (Fehler {wer})")

    def _hbw_quittung(self, code, wp):
        """HBW meldet: eingelagert (Einlagerauftrag fertig) bzw. Werkstück am Band."""
        with self._lock:
            aktive = [a for a in self.offen if a["status"] == "AKTIV"]
        for a in aktive:
            if code == ACK_STORED and a["art"] == "EIN" and a["phase"] == "UEBERGEBEN":
                self.abschliessen(a, "FERTIG")
            elif code == ACK_FETCHED and a["art"] == "AUS" and a["phase"] == "ANGEFORDERT":
                self.phase(a, "AM_BAND")

    def stillstand(self):
        """Für das automatische Umlagern: nichts zu tun, Greifer ruht, Eingabe leer."""
        with self._lock:
            if self.offen:
                return False
        v = self.vgr
        if v is None:
            return True
        return v.zustand == "IDLE" and not v.aktiv and not v.eingabe_belegt()

    # =======================================================================
    # Dashboard
    # =======================================================================
    def anfordern(self, befehl, quelle="dashboard", **daten):
        if befehl == "not_halt":
            return True, ""
        if befehl == "bestellen":
            return self.bestellen(daten.get("farbe"), daten.get("prio", "normal"), quelle)
        if befehl in ("stornieren", "prio"):
            nr = int(daten.get("nr", -1))
            with self._lock:
                a = next((x for x in self.offen if x["nr"] == nr), None)
                if a is None:
                    return False, "Auftrag nicht gefunden"
                if a["status"] != "WARTEND":
                    return False, "Auftrag ist schon in Arbeit"
                if befehl == "stornieren":
                    self._beenden(a, "STORNIERT", "vom Bediener")
                    self.log(f"Auftrag {nr} storniert")
                    return True, f"Auftrag {nr} storniert"
                a["prio"] = "normal" if a["prio"] == "eilig" else "eilig"
                return True, f"Auftrag {nr}: Priorität {a['prio']}"
        if befehl == "vorrang":
            try:
                self.einst.setze("vorrang", daten.get("wert"))
            except ValueError as e:
                return False, str(e)
            self.log(f"Vorrang: {'Einlagerung' if daten.get('wert') == 'EIN' else 'Auslagerung'} zuerst")
            return True, "Vorrang geändert"
        return False, f"Unbekannter Befehl '{befehl}'"

    def zustandsbericht(self):
        with self._lock:
            reihe = {a["nr"]: i + 1 for i, a in enumerate(self.reihenfolge())}
            jetzt = time.time()

            def zeile(a):
                return {**{k: a[k] for k in ("nr", "art", "farbe", "prio", "status", "phase", "text", "quelle")},
                        "art_text": ART_TEXT[a["art"]], "status_text": STATUS_TEXT[a["status"]],
                        "platz_in_reihe": reihe.get(a["nr"]),
                        "alter_s": round(jetzt - a["erstellt"]),
                        "dauer_s": round((a["ende"] or jetzt) - a["gestartet"]) if a["gestartet"] else None,
                        "ende": time.strftime("%H:%M:%S", time.localtime(a["ende"])) if a["ende"] else ""}
            offen = sorted(self.offen, key=lambda a: (a["status"] != "AKTIV", reihe.get(a["nr"], 0)))
            return {"offen": [zeile(a) for a in offen], "erledigt": [zeile(a) for a in self.erledigt],
                    "max": cfg.AUFTRAEGE_MAX, "vorrang": self.einst["vorrang"],
                    "verfuegbar": {f: self._verfuegbar(f) for f in FARBEN}}
