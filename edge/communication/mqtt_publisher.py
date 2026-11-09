"""Publicador MQTT real (paho-mqtt 2.x). No se prueba en CI: se verifica contra Mosquitto."""
from urllib.parse import urlparse

import paho.mqtt.client as mqtt


class PahoPublisher:
    def __init__(self, broker_url: str, username: str = "", password: str = "", tls: bool = False):
        url = urlparse(broker_url)
        self._host = url.hostname or "localhost"
        self._port = url.port or (8883 if tls else 1883)
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if username:
            self._client.username_pw_set(username, password)
        if tls:
            self._client.tls_set()
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)

    def start(self) -> None:
        self._client.connect_async(self._host, self._port)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    @property
    def connected(self) -> bool:
        return self._client.is_connected()

    def publish(self, topic: str, payload: str, qos: int = 1) -> bool:
        """Bloquea hasta el PUBACK (max 5 s). False si no hay conexion o no hubo acuse."""
        if not self.connected:
            return False
        info = self._client.publish(topic, payload, qos=qos)
        try:
            info.wait_for_publish(timeout=5)
        except (ValueError, RuntimeError):
            return False
        return info.is_published()
