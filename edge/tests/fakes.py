"""Dobles de prueba reutilizables por todos los tickets (reloj, ESP32, MQTT, vision)."""
import json
from datetime import datetime, timedelta, timezone


class FakeClock:
    """Reloj controlable: monotonic() en segundos y now_utc() con zona UTC."""

    def __init__(self, start_utc=None, monotonic_start=1000.0):
        self._utc = start_utc or datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        self._mono = monotonic_start

    def monotonic(self):
        return self._mono

    def now_utc(self):
        return self._utc

    def advance(self, seconds):
        self._mono += seconds
        self._utc += timedelta(seconds=seconds)


class FakeEsp32Client:
    """Registra las ordenes enviadas. `responses` fuerza la respuesta por actuador."""

    def __init__(self):
        self.sent = []            # lista de (ip, body)
        self.responses = {}       # actuator -> dict que se mezcla en la respuesta
        self.unreachable = set()  # ips que no responden

    def send_command(self, ip, body):
        self.sent.append((ip, body))
        if ip in self.unreachable:
            raise TimeoutError(ip)
        reply = {"commandId": body["commandId"], "status": "APPLIED", "simulated": False}
        reply.update(self.responses.get(body["actuator"], {}))
        return reply


class FakeMqttPublisher:
    """Broker falso. `broker_up = False` simula caida: publish devuelve False (sin PUBACK)."""

    def __init__(self):
        self.messages = []  # lista de dicts {topic, payload (dict), qos}
        self.broker_up = True

    @property
    def connected(self):
        return self.broker_up

    def publish(self, topic, payload, qos=1):
        if not self.broker_up:
            return False
        self.messages.append({"topic": topic, "payload": json.loads(payload), "qos": qos})
        return True


class FakeVision:
    """Vision falsa: el test fija las marcas de 'ultima persona vista'."""

    def __init__(self):
        self.last_unknown_person_at = None
        self.last_known_person_at = None
        self.available = True
