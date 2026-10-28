"""
Optionale MQTT-Anbindung an die restliche Lernfabrik (C++: TxtMqttFactoryClient
und der CLIENT_HBW-Teil von TxtFactoryClient/src/main.cpp).

Nur nötig, wenn das Python-Hochregallager vom Sauggreifer (VGR, Haupt-TXT)
Aufträge bekommen soll bzw. das Original-Dashboard der Lernfabrik den
Lagerbestand anzeigen soll. Für den Einzeltest mit dem eigenen Dashboard wird
MQTT NICHT gebraucht.

Topics (TxtSmartFactoryLib/doc/MqttInterface.md):
  empfängt:  fl/vgr/do      Aufträge vom Sauggreifer
             f/o/state/ack  "Quittieren"-Knopf im Lernfabrik-Dashboard
             fl/ssc/joy     Joystick der Sensorstation (Kalibrierung)
  sendet:    f/i/state/hbw  Status (1=bereit, 2=beschäftigt, 4=Fehler, 7=Kalibrierung)
             f/i/stock      Lagerbestand
             fl/hbw/ack     Quittungen an den Sauggreifer
"""

import datetime
import json

from . import konfiguration as cfg

# TxtVgrDoCode_t
VGR_BEFEHLE = {
    1: "behaelter_holen",     # VGR_HBW_FETCHCONTAINER
    2: "einlagern",           # VGR_HBW_STORE_WP
    3: "auslagern",           # VGR_HBW_FETCH_WP
    4: "behaelter_zurueck",   # VGR_HBW_STORECONTAINER
    5: "lager_reset",         # VGR_HBW_RESETSTORAGE
    6: "kalibrieren",         # VGR_HBW_CALIB
}


def zeitstempel():
    """Format wie im Original: YYYY-MM-DDThh:mm:ss.fffZ (UTC)."""
    t = datetime.datetime.now(datetime.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


class MqttBruecke:
    def __init__(self, hbw, host=cfg.MQTT_HOST, port=cfg.MQTT_PORT):
        try:
            import paho.mqtt.client as mqtt
        except ImportError as e:
            raise RuntimeError("Für MQTT bitte installieren: pip3 install paho-mqtt") from e
        self.hbw = hbw
        self.verbunden = False
        try:   # paho-mqtt 2.x
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="TxtFactoryHBW-Python")
        except AttributeError:   # paho-mqtt 1.x
            self.client = mqtt.Client(client_id="TxtFactoryHBW-Python")
        self.client.username_pw_set(cfg.MQTT_BENUTZER, cfg.MQTT_PASSWORT)
        self.client.on_connect = self._verbunden
        self.client.on_disconnect = lambda *a: setattr(self, "verbunden", False)
        self.client.on_message = self._nachricht

        hbw.bei_status.append(self.sende_status)
        hbw.bei_quittung.append(self.sende_quittung)
        hbw.lager.beobachter.append(lambda lager: self.sende_bestand())

        self.client.connect_async(host, port, keepalive=60)
        self.client.loop_start()
        hbw.log(f"MQTT: verbinde mit {host}:{port} ...")

    def _verbunden(self, client, userdata, flags, rc):
        if rc == 0:
            self.verbunden = True
            for t in ("fl/vgr/do", "f/o/state/ack", "fl/ssc/joy"):
                client.subscribe(t, qos=1)
            self.hbw.log("MQTT: verbunden")
            self.sende_bestand()
        else:
            self.hbw.log(f"MQTT: Verbindung abgelehnt (Code {rc})")

    def _sende(self, topic, daten):
        if self.verbunden:
            self.client.publish(topic, json.dumps(daten), qos=1)

    # --- Senden ------------------------------------------------------------
    def sende_status(self, code, aktiv):
        self._sende("f/i/state/hbw", {"ts": zeitstempel(), "station": "hbw", "code": code,
                                      "description": "", "active": 1 if aktiv else 0, "target": ""})

    def sende_bestand(self):
        items = [{"location": ort, "workpiece": wp}
                 for ort, wp in sorted(self.hbw.lager.bestand().items())]
        self._sende("f/i/stock", {"ts": zeitstempel(), "stockItems": items})

    def sende_quittung(self, code, wp):
        self._sende("fl/hbw/ack", {"ts": zeitstempel(), "code": code, "workpiece": wp})

    # --- Empfangen ---------------------------------------------------------
    def _nachricht(self, client, userdata, msg):
        try:
            d = json.loads(msg.payload.decode("utf-8"))
        except ValueError:
            return
        if msg.topic == "f/o/state/ack":
            self.hbw.anfordern("quittieren", quelle="mqtt")
        elif msg.topic == "fl/ssc/joy":
            self.hbw.joystick(d)
        elif msg.topic == "fl/vgr/do":
            code = int(d.get("code", -1))
            wp = d.get("workpiece")
            if wp is not None:
                wp = {"id": wp.get("id", ""), "type": wp.get("type", "NONE"), "state": wp.get("state", "RAW")}
            if code == 0:
                self.hbw.log("MQTT: VGR meldet Programmende")
            elif code in VGR_BEFEHLE:
                self.hbw.log(f"MQTT: Auftrag vom VGR: {VGR_BEFEHLE[code]}")
                self.hbw.anfordern(VGR_BEFEHLE[code], quelle="mqtt", werkstueck=wp)

    def beenden(self):
        self.client.loop_stop()
        self.client.disconnect()
