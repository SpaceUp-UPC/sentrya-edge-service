"""Sensor Manager: valida, normaliza, persiste y distribuye las lecturas de las ESP32."""
import threading
import uuid

from edge.communication import outbox
from edge.communication.events import enqueue_events
from edge.shared.clock import iso_utc
from edge.shared.logging import get_logger
from edge.shared.models import ReadingRow
from edge.sensors.normalizers import ValidationError, normalize_reading

log = get_logger("sensors")


class SensorManager:
    def __init__(self, db, clock, devices, rules_engine, controller, space_id, lock=None):
        self.db = db
        self.clock = clock
        self.devices = devices
        self.rules = rules_engine
        self.controller = controller
        self.space_id = space_id
        self.lock = lock or threading.RLock()   # el lock del edge (subsystem_state)

    def ingest_reading(self, body) -> dict:
        """Orden: validar -> reglas -> accionar -> persistir -> outbox. Lanza ValidationError."""
        reading = normalize_reading(body, self.devices, self.clock)
        device = reading.device
        reading_id = str(uuid.uuid4())
        with self.lock:
            local_state = self._apply(reading, reading_id)
        log.info("Lectura %s=%s %s (%s) -> %s", reading.device.key, reading.value, reading.unit,
                 reading.measured_at, local_state)
        return {"readingId": reading_id, "localState": local_state}

    def _apply(self, reading, reading_id):
        device = reading.device
        with self.db.session() as session:
            evaluations = self.rules.handle_reading(
                session, device.key, reading.metric_type, reading.value,
                {"error": True} if reading.error else None)
        # Las ordenes se registran fuera de la transaccion y ANTES de persistir la lectura y
        # encolarla para MQTT ("primero se acciona"); el despachador las envia sin bloquear.
        for evaluation in evaluations:
            self.controller.execute(evaluation.actions)
        local_state = evaluations[0].state["state"] if evaluations else None
        with self.db.session() as session:
            # Lectura con error (sin valor) no se guarda; si el modulo publica por su cuenta
            # (humedad: telemetria), tampoco se encola cada lectura.
            if not reading.error:
                session.add(ReadingRow(
                    id=reading_id, device_key=device.key, metric_type=reading.metric_type,
                    value=reading.value, unit=reading.unit, measured_at=reading.measured_at,
                    local_state=local_state))
                if all(e.publish_reading for e in evaluations):
                    payload = {"deviceId": device.device_id, "metricType": reading.metric_type,
                               "value": reading.value, "unit": reading.unit,
                               "readingId": reading_id, "measuredAt": reading.measured_at,
                               "localState": local_state}
                    outbox.enqueue(session, reading_id,
                                   f"sentrya/{self.space_id}/{device.device_id}/readings",
                                   payload, iso_utc(self.clock.now_utc()))
            for evaluation in evaluations:
                enqueue_events(session, evaluation.events, self.devices, self.clock, self.space_id)
        return local_state
