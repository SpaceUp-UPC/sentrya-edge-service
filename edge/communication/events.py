"""Publicacion de eventos de las reglas como lecturas en el outbox (ADR 0001)."""
import uuid

from edge.communication import outbox
from edge.shared.clock import iso_utc


def publish_events(session, evaluations, devices, space_id, clock) -> None:
    """Encola cada Event de las evaluaciones. data: value (def. 1), unit (def. 'event') y extras."""
    now = iso_utc(clock.now_utc())
    for evaluation in evaluations:
        for event in evaluation.events:
            device = devices.get(event.device_key)
            if device is None:
                continue
            data = dict(event.data)
            value = data.pop("value", 1)
            unit = data.pop("unit", "event")
            message_id = str(uuid.uuid4())
            payload = {"deviceId": device.device_id, "metricType": event.metric_type,
                       "value": value, "unit": unit, "readingId": message_id,
                       "measuredAt": now, **data}
            outbox.enqueue(session, message_id,
                           f"sentrya/{space_id}/{device.device_id}/readings", payload, now)
