"""Catalogo de actuadores: nodo que aloja a cada uno y acciones validas (AGENTS.md seccion 8)."""
from edge.sensors.normalizers import ValidationError

ACTUATOR_NODES = {
    "siren": "node-1",
    "lock": "node-1",
    "humidifier": "node-2",
    "fan": "node-3",
    "buzzer": "node-3",
    "valve": "node-3",
    "sampling": "node-3",  # intervalo de muestreo del sensor de temperatura
}

ACTIONS = {
    "siren": ("on", "off"),
    "lock": ("lock", "unlock"),
    "humidifier": ("on", "off"),
    "fan": ("set",),
    "buzzer": ("on", "off"),
    "valve": ("open", "close"),
    "sampling": ("set",),
}


def known_nodes() -> set:
    return set(ACTUATOR_NODES.values())


def validate(actuator: str, action: str, params: dict) -> None:
    if actuator not in ACTUATOR_NODES:
        raise ValidationError(f"actuador desconocido: {actuator}")
    if action not in ACTIONS[actuator]:
        raise ValidationError(f"accion invalida para {actuator}: {action}")
    if actuator == "fan":
        duty = params.get("duty")
        if isinstance(duty, bool) or not isinstance(duty, (int, float)) or not 0 <= duty <= 100:
            raise ValidationError("fan/set requiere params.duty entre 0 y 100")
    if actuator == "sampling":
        interval = params.get("intervalSeconds")
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or interval <= 0:
            raise ValidationError("sampling/set requiere params.intervalSeconds > 0")
