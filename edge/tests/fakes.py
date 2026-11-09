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


def _topic_matches(topic_filter, topic):
    f, t = topic_filter.split("/"), topic.split("/")
    return len(f) == len(t) and all(a in ("+", b) for a, b in zip(f, t))


class FakeMqttPublisher:
    """Broker falso. `broker_up = False` simula caida: publish devuelve False (sin PUBACK)."""

    def __init__(self):
        self.messages = []  # lista de dicts {topic, payload (dict), qos}
        self.broker_up = True
        self.subscriptions = {}  # filtro -> handler(topic, payload_bytes)

    @property
    def connected(self):
        return self.broker_up

    def subscribe(self, topic_filter, handler, qos=1):
        self.subscriptions[topic_filter] = handler

    def deliver(self, topic, payload):
        """Inyecta un mensaje entrante (dict/str/bytes) como lo haria el hilo de paho.

        Como en paho, publicar desde el callback envia el mensaje pero el PUBACK no se procesa
        mientras el hilo de red esta ocupado: `publish` devuelve False (falso fallo).
        """
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        self._in_network_thread = True
        try:
            for topic_filter, handler in list(self.subscriptions.items()):
                if _topic_matches(topic_filter, topic):
                    handler(topic, payload)
        finally:
            self._in_network_thread = False

    def publish(self, topic, payload, qos=1):
        if not self.broker_up:
            return False
        self.messages.append({"topic": topic, "payload": json.loads(payload), "qos": qos})
        return not getattr(self, "_in_network_thread", False)


class FakeVision(MockPersonDetector):
    """Vision falsa: el detector mock (las personas se inyectan por /simulate/security/person)."""


class ManualDispatcher:
    """Despachador de ordenes que no envia nada hasta `run_pending()`: simula el hilo trabajador
    en produccion (la respuesta HTTP a la ESP32 no espera la orden)."""

    def __init__(self):
        self.queue = []

    def submit(self, job):
        self.queue.append(job)
        return None

    def run_pending(self):
        jobs, self.queue = self.queue, []
        for job in jobs:
            job()
