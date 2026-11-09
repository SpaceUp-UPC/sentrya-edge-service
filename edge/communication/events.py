"""Eventos publicables como "lecturas evento" (ADR 0001): value 1, unit "event", campos extra de `data`."""
import uuid

from edge.communication import outbox
from edge.shared.clock import iso_utc
from edge.shared.logging import get_logger

log = get_logger("events")


def enqueue_events(session, events, devices, clock, space_id) -> None:
    """Encola cada Event en el outbox. Un evento sin dispositivo registrado se descarta con aviso."""
    for event in events:
        device = devices.get(event.device_key)
        if device is None:
            log.warning("Evento %s sin dispositivo '%s' en devices.yaml: no se publica",
                        event.metric_type, event.device_key)
            continue
        now = iso_utc(clock.now_utc())
        event_id = str(uuid.uuid4())
        payload = {"deviceId": device.device_id, "metricType": event.metric_type, "value": 1,
                   "unit": "event", "readingId": event_id, "measuredAt": now, **event.data}
        outbox.enqueue(session, event_id, f"sentrya/{space_id}/{device.device_id}/readings",
                       payload, now)
