"""Dobles de prueba reutilizables por todos los tickets (reloj, ESP32, MQTT, vision)."""
import json
from datetime import datetime, timedelta, timezone

from edge.security.person_detection import MockPersonDetector


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
    """Registra las ordenes enviadas (`sent`, incluye reintentos) y simula fallos de la ESP32.

    - `unreachable`: ips que nunca responden (timeout en cada intento).
    - `timeout_next(ip, n)`: las proximas n peticiones a esa ip dan timeout y luego responde.
    - `simulate_failed(actuator)`: el nodo responde status FAILED.
    - `simulate_missing(actuator)`: el nodo responde APPLIED con simulated=True.
    - `responses`: mezcla libre sobre la respuesta por actuador.
    """

    def __init__(self):
        self.sent = []            # lista de (ip, body)
        self.responses = {}       # actuator -> dict que se mezcla en la respuesta
        self.unreachable = set()  # ips que no responden
        self._timeouts = {}       # ip -> timeouts pendientes

    def timeout_next(self, ip, times=1):
        self._timeouts[ip] = times

    def simulate_failed(self, actuator):
        self.responses[actuator] = {"status": "FAILED"}

    def simulate_missing(self, actuator):
        self.responses[actuator] = {"simulated": True}

    def send_command(self, ip, body):
        self.sent.append((ip, body))
        if ip in self.unreachable:
            raise TimeoutError(ip)
        if self._timeouts.get(ip, 0) > 0:
            self._timeouts[ip] -= 1
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


class FakeVision(MockPersonDetector):
    """Vision falsa: el detector mock (las personas se inyectan por /simulate/security/person)."""
