"""Nube -> edge: commands, security/validation y config (AGENTS.md seccion 9).

Los mensajes llegan en el hilo de paho. Cada uno se atiende bajo el lock de seguridad (el mismo
del tick y de la validacion), asi nunca se cruza con una ventana abierta ni con el tick.
Un mensaje mal formado se registra y se ignora: el hilo del cliente nunca se cae.
"""
import json

from edge.actuation.commands import run_command
from edge.config import thresholds as thresholds_config
from edge.sensors.normalizers import ValidationError
from edge.shared.logging import get_logger

log = get_logger("cloud")

# deviceKey logico de devices.yaml -> actuador cuando no coinciden (la nube ve al sensor)
ACTUATOR_ALIASES = {"humidity": "humidifier"}


class CloudInbox:
    def __init__(self, edge):
        self.edge = edge
        prefix = f"sentrya/{edge.settings.space_id}"
        self.topics = {
            f"{prefix}/+/commands": self.on_command,
            f"{prefix}/security/validation": self.on_validation,
            f"{prefix}/config": self.on_config,
        }

    def subscribe(self, publisher) -> None:
        for topic_filter, handler in self.topics.items():
            publisher.subscribe(topic_filter, self._guarded(handler))

    def _guarded(self, handler):
        def callback(topic, payload):
            try:
                body = json.loads(payload)
                with self.edge.security.lock:
                    handler(topic, body)
            except (ValueError, ValidationError) as error:   # JSON o contenido invalido
                log.warning("Mensaje de la nube ignorado en %s: %s", topic, error)
            except Exception:
                log.exception("Error atendiendo el mensaje de la nube en %s", topic)
        return callback

    def _resolve_actuator(self, segment: str) -> str:
        for device in self.edge.devices.values():   # el segmento es el deviceId de IoT
            if device.device_id == segment:
                return ACTUATOR_ALIASES.get(device.key, device.key)
        return ACTUATOR_ALIASES.get(segment, segment)   # o el nombre logico del actuador

    def on_command(self, topic, body) -> None:
        if not isinstance(body, dict) or not isinstance(body.get("action"), str):
            raise ValidationError("commands requiere un objeto con action")
        actuator = self._resolve_actuator(topic.split("/")[2])
        result = run_command(self.edge, actuator, body["action"], body.get("params") or {})
        log.info("Orden de la nube %s -> %s %s: %s", body.get("commandId"), actuator,
                 body["action"], result)

    def on_validation(self, topic, body) -> None:
        if not isinstance(body, dict) or body.get("decision") not in ("confirm", "reject"):
            raise ValidationError("decision debe ser 'confirm' o 'reject'")
        event_id = body.get("eventId")
        if event_id is not None and not isinstance(event_id, str):
            raise ValidationError("eventId debe ser un string")
        if self.edge.security.validate(body["decision"], event_id) is None:
            log.info("Validacion de la nube sin ventana abierta para %s: ignorada", event_id)

    def on_config(self, topic, body) -> None:
        thresholds_config.apply_cloud(self.edge.db, self.edge.rules.thresholds, body,
                                      self.edge.clock)
        log.info("Umbrales de la nube aplicados (vigentes hasta el proximo reinicio)")
