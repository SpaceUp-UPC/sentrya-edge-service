"""Publicador/suscriptor MQTT real (paho-mqtt 2.x). Se verifica contra Mosquitto, no en CI."""
from urllib.parse import urlparse

import paho.mqtt.client as mqtt


class PahoPublisher:
    def __init__(self, broker_url: str, username: str = "", password: str = "", tls: bool = False,
                 client=None):
        url = urlparse(broker_url)
        self._host = url.hostname or "localhost"
        self._port = url.port or (8883 if tls else 1883)
        self._client = client or mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._subscriptions = {}   # filtro -> handler(topic, payload_bytes, retained)
        if username:
            self._client.username_pw_set(username, password)
        if tls:
            self._client.tls_set()
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.on_connect = self._on_connect

    def subscribe(self, topic_filter: str, handler, qos: int = 1) -> None:
        """Registra la suscripcion; se rehace en cada conexion (tambien tras reconectar)."""
        self._subscriptions[topic_filter] = (handler, qos)
        if self._client.is_connected():
            self._apply(topic_filter, handler, qos)

    def _apply(self, topic_filter, handler, qos) -> None:
        self._client.message_callback_add(
            topic_filter,
            lambda _client, _userdata, message: handler(message.topic, message.payload,
                                                        bool(message.retain)))
        self._client.subscribe(topic_filter, qos=qos)

    def _on_connect(self, _client, _userdata, _flags, _reason_code, _properties=None) -> None:
        for topic_filter, (handler, qos) in list(self._subscriptions.items()):
            self._apply(topic_filter, handler, qos)

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
