"""Validacion y normalizacion de lecturas entrantes."""
from dataclasses import dataclass
from datetime import datetime, timezone

from edge.shared.clock import iso_utc


class ValidationError(ValueError):
    """Entrada invalida; el mensaje se devuelve a la ESP32 con HTTP 400."""


@dataclass(frozen=True)
class NormalizedReading:
    device: object
    metric_type: str
    value: float
    unit: str
    measured_at: str
    error: bool = False


def normalize_reading(body, devices, clock) -> NormalizedReading:
    if not isinstance(body, dict):
        raise ValidationError("El cuerpo debe ser un objeto JSON")
    # Una lectura con "error" (p. ej. DHT22 fallido) no lleva valor ni unidad.
    errored = bool(body.get("error"))
    required = ("node", "deviceKey", "metricType") if errored else \
        ("node", "deviceKey", "metricType", "value", "unit")
    for field in required:
        if body.get(field) is None:
            raise ValidationError(f"Falta el campo obligatorio '{field}'")

    device = devices.get(body["deviceKey"])
    if device is None:
        raise ValidationError(f"deviceKey desconocido: '{body['deviceKey']}'")
    if body["metricType"] != device.metric_type:
        raise ValidationError(
            f"metricType '{body['metricType']}' no coincide con '{device.metric_type}' del deviceKey")

    if errored:
        return NormalizedReading(device, body["metricType"], None, device.unit,
                                 iso_utc(clock.now_utc()), error=True)

    value = body["value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("value debe ser numerico")

    measured_at = body.get("measuredAt")
    if measured_at is None:
        stamp = clock.now_utc()
    else:
        try:
            stamp = datetime.fromisoformat(str(measured_at).replace("Z", "+00:00"))
        except ValueError:
            raise ValidationError("measuredAt debe ser ISO-8601") from None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)

    return NormalizedReading(device, body["metricType"], float(value), device.unit, iso_utc(stamp))
