"""IoT Communication Client: envia el outbox por MQTT (QoS 1) en orden.

El publicador es un adaptador inyectable con:
    publish(topic: str, payload: str, qos: int = 1) -> bool   # True = PUBACK recibido
    connected: bool
"""
import threading

from edge.communication import outbox
from edge.shared.logging import get_logger

log = get_logger("mqtt")


class IoTCommunicationClient:
    def __init__(self, db, publisher):
        self.db = db
        self.publisher = publisher
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return bool(self.publisher.connected)

    def flush(self) -> int:
        """Publica los pendientes en orden; se detiene en el primer fallo. Devuelve cuantos envio."""
        sent = 0
        with self._lock:
            while True:
                with self.db.session() as session:
                    rows = outbox.pending(session, limit=1)
                    if not rows:
                        return sent
                    row = rows[0]
                    row.attempts += 1
                    try:
                        acked = self.publisher.publish(row.topic, row.payload_json, qos=1)
                    except Exception as error:  # broker caido: se reintenta en el siguiente tick
                        log.warning("MQTT: fallo al publicar %s (%s)", row.topic, error)
                        acked = False
                    if not acked:
                        return sent
                    row.synced = True
                    sent += 1
                    log.info("MQTT: publicado en %s", row.topic)
