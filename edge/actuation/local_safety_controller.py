"""Local Safety Controller: UNICO componente que envia ordenes a las ESP32.

Interfaz para el resto del edge:
- `execute(actions)`: entrada del Rules Engine (lista de `Action`). Una regla local nunca
  desbloquea la cerradura: esas acciones se descartan (ADR 0003).
- `send(actuator, action, params, explicit=True)`: orden explicita (demo, nube).
- `desired_state()`: ultimo estado deseado por actuador; base de la reconciliacion por heartbeat (#6).
- `node_ip(node)`: IP vigente del nodo segun el ultimo heartbeat.

No debe llamarse dentro de una transaccion de base abierta: hace I/O de red y usa sesiones propias.
"""
import json
import uuid
from dataclasses import dataclass, field

from edge.actuation import catalog
from edge.shared.clock import iso_utc
from edge.shared.logging import get_logger
from edge.shared.models import CommandRow, DesiredStateRow, NodeRow

log = get_logger("lsc")

MAX_RETRIES = 3  # reintentos tras el primer intento (timeout de 2 s lo aplica el adaptador)


@dataclass(frozen=True)
class CommandResult:
    command_id: str
    node: str
    actuator: str
    action: str
    status: str            # APPLIED | FAILED
    simulated: bool = False


@dataclass(frozen=True)
class DesiredState:
    actuator: str
    action: str
    params: dict = field(default_factory=dict)
    command_id: str | None = None
    updated_at: str | None = None


class LocalSafetyController:
    def __init__(self, db, esp32, clock, settings=None):
        self.db = db
        self.esp32 = esp32
        self.clock = clock
        self.settings = settings

    # ---- entrada del Rules Engine ----

    def execute(self, actions: list) -> list[CommandResult]:
        results = []
        for item in actions:
            if item.action == "unlock":
                log.warning("Regla local intento desbloquear %s: descartado (ADR 0003)", item.actuator)
                continue
            results.append(self.send(item.actuator, item.action, dict(item.params)))
        return results

    # ---- envio ----

    def send(self, actuator: str, action: str, params: dict | None = None) -> CommandResult:
        """Envia una orden y la registra. Las llamadas de reglas pasan por `execute`;
        `send` directo es para ordenes explicitas (demo, nube) y puede desbloquear."""
        params = dict(params or {})
        catalog.validate(actuator, action, params)
        params = self._lock_params(actuator, action, params)
        node = catalog.ACTUATOR_NODES[actuator]
        command_id = str(uuid.uuid4())
        now = iso_utc(self.clock.now_utc())
        with self.db.session() as session:
            # Las ordenes previas aun sin cumplir quedan superadas por esta (reconciliacion, #6).
            for old in session.query(CommandRow).filter(
                    CommandRow.actuator == actuator, CommandRow.status.in_(("PENDING", "FAILED"))):
                old.status = "SUPERSEDED"
            session.add(CommandRow(command_id=command_id, node=node, actuator=actuator,
                                   action=action, params_json=json.dumps(params),
                                   status="PENDING", simulated=False, created_at=now))
            row = session.get(NodeRow, node)
            ip = row.ip if row else None

        status, simulated = self._deliver(ip, command_id, actuator, action, params)

        acked = iso_utc(self.clock.now_utc())
        with self.db.session() as session:
            command = session.get(CommandRow, command_id)
            command.status, command.simulated, command.acked_at = status, simulated, acked
            # Un unlock fallido no queda como deseado: nunca se reintenta solo (ADR 0003).
            if not (action == "unlock" and status == "FAILED"):
                desired = session.get(DesiredStateRow, actuator)
                if desired is None:
                    desired = DesiredStateRow(actuator=actuator)
                    session.add(desired)
                desired.action = action
                desired.params_json = json.dumps(params)
                desired.command_id = command_id
                desired.updated_at = acked
        log.info("Orden %s %s %s %s -> %s%s", node, actuator, action, params or "", status,
                 " (simulada)" if simulated else "")
        return CommandResult(command_id, node, actuator, action, status, simulated)

    def _deliver(self, ip, command_id, actuator, action, params):
        if not ip:
            log.warning("Nodo de %s sin IP conocida: FAILED", actuator)
            return "FAILED", False
        body = {"commandId": command_id, "actuator": actuator, "action": action, "params": params}
        for attempt in range(1 + MAX_RETRIES):
            try:
                reply = self.esp32.send_command(ip, body)
            except Exception as error:  # timeout, conexion, HTTP, JSON invalido
                log.warning("Intento %d/%d a %s fallo: %s", attempt + 1, 1 + MAX_RETRIES, ip, error)
                continue
            status = "APPLIED" if reply.get("status") == "APPLIED" else "FAILED"
            return status, bool(reply.get("simulated", False))
        return "FAILED", False

    def _lock_params(self, actuator, action, params):
        pulse = self.settings.lock_pulse_seconds if self.settings else 3
        energize = self.settings.lock_energize_to_unlock if self.settings else True
        if actuator == "lock" and action == "unlock" and energize:
            params = {**params, "pulseSeconds": float(pulse)}
        return params

    # ---- reconciliacion por heartbeat (#6) ----

    def reconcile(self, node: str, reported: dict) -> list[CommandResult]:
        """Compara el estado deseado con lo que reporta `node` y envia solo la diferencia.

        - Un deseado `unlock` nunca se reconcilia: la cerradura vuelve a `locked` sola tras el
          pulso y un unlock fallido no queda como deseado (ADR 0003).
        - Un actuador que el heartbeat no reporta solo se reenvia si su ultima orden fallo.
        No debe llamarse dentro de una transaccion de base abierta.
        """
        results = []
        for actuator, desired in self.desired_state().items():
            if catalog.ACTUATOR_NODES.get(actuator) != node or desired.action == "unlock":
                continue
            if actuator in reported:
                if _matches(actuator, desired, reported[actuator]):
                    continue
            elif self._last_status(desired.command_id) != "FAILED":
                continue
            log.info("Reconciliando %s: deseado %s %s, reportado %s", actuator, desired.action,
                     desired.params or "", reported.get(actuator, "sin dato"))
            results.append(self.send(actuator, desired.action, dict(desired.params)))
        return results

    def _last_status(self, command_id):
        with self.db.session() as session:
            row = session.get(CommandRow, command_id) if command_id else None
            return row.status if row else None

    # ---- consulta ----

    def desired_state(self) -> dict[str, DesiredState]:
        with self.db.session() as session:
            return {r.actuator: DesiredState(r.actuator, r.action, json.loads(r.params_json),
                                             r.command_id, r.updated_at)
                    for r in session.query(DesiredStateRow).all()}

    def node_ip(self, node: str) -> str | None:
        with self.db.session() as session:
            row = session.get(NodeRow, node)
            return row.ip if row else None


_EXPECTED = {
    ("siren", "on"): {"on"}, ("siren", "off"): {"off"},
    ("humidifier", "on"): {"on"}, ("humidifier", "off"): {"off"},
    ("buzzer", "on"): {"on"}, ("buzzer", "off"): {"off"},
    ("lock", "lock"): {"locked", "lock"},
    ("valve", "open"): {"open", "opened"}, ("valve", "close"): {"closed", "close"},
}


def _matches(actuator: str, desired: DesiredState, reported) -> bool:
    """Interpreta el valor reportado en el heartbeat. El ventilador acepta el duty numerico
    ("50", 50) o on/off; el resto acepta las palabras de `_EXPECTED` (propuesta, ver pendientes)."""
    if actuator == "fan":
        duty = desired.params.get("duty", 0)
        if isinstance(reported, str) and reported.strip().lower() in ("on", "off"):
            return (reported.strip().lower() == "on") == (duty > 0)
        try:
            return float(reported) == float(duty)
        except (TypeError, ValueError):
            return False
    return str(reported).strip().lower() in _EXPECTED.get((actuator, desired.action), set())
