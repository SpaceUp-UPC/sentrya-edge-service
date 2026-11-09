"""Local Safety Controller: UNICO componente que envia ordenes a las ESP32.

Interfaz para el resto del edge:
- `execute(actions)`: entrada del Rules Engine (lista de `Action`). Registra las ordenes y las
  envia por el despachador (`edge/actuation/dispatcher.py`): la peticion de la ESP32 que disparo
  la regla no espera. Una regla local nunca desbloquea la cerradura: esas acciones se descartan
  (ADR 0003).
- `send(actuator, action, params)`: orden explicita (demo, nube), sincrona; puede desbloquear.
- `desired_state()`: ultimo estado deseado por actuador; base de la reconciliacion por heartbeat (#6).
- `node_ip(node)`: IP vigente del nodo segun el ultimo heartbeat.

No debe llamarse dentro de una transaccion de base abierta: hace I/O de red y usa sesiones propias.
"""
import json
import threading
import uuid
from dataclasses import dataclass, field

from edge.actuation import catalog
from edge.actuation.dispatcher import SyncDispatcher
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
    status: str            # APPLIED | FAILED | PENDING (aun en el despachador)
    simulated: bool = False


@dataclass(frozen=True)
class _Order:
    """Orden registrada (PENDING) a la espera de su envio."""
    command_id: str
    node: str
    actuator: str
    action: str
    params: dict


@dataclass(frozen=True)
class DesiredState:
    actuator: str
    action: str
    params: dict = field(default_factory=dict)
    command_id: str | None = None
    updated_at: str | None = None


class LocalSafetyController:
    def __init__(self, db, esp32, clock, settings=None, dispatcher=None):
        self.db = db
        self.esp32 = esp32
        self.clock = clock
        self.settings = settings
        self.dispatcher = dispatcher or SyncDispatcher()
        # Seguro entre hilos (peticiones HTTP, tick, nube y el trabajador del despachador):
        # `_lock` hace atomicos el registro de una orden, la decision de reconciliar y el acuse;
        # el lock por nodo serializa los envios HTTP a una misma ESP32.
        self._lock = threading.RLock()
        self._node_locks: dict[str, threading.Lock] = {}
        self._fail_leftover_pending()

    def _fail_leftover_pending(self) -> None:
        """Al arrancar: una orden PENDING de un proceso anterior nunca se envio. Queda FAILED,
        asi la reconciliacion por heartbeat no la toma por una orden aun en camino."""
        with self.db.session() as session:
            for row in session.query(CommandRow).filter(CommandRow.status == "PENDING"):
                row.status = "FAILED"

    # ---- entrada del Rules Engine ----

    def execute(self, actions: list) -> list[CommandResult]:
        """Registra las ordenes de una regla (PENDING) y las entrega al despachador.

        Se registran en el hilo que llama, antes de que este persista o publique nada
        ("primero se acciona"); el envio HTTP lo hace el despachador, asi la respuesta a la
        ESP32 que disparo la regla no espera. Con un despachador asincrono el resultado
        devuelto queda PENDING.
        """
        results = []
        for item in actions:
            if item.action == "unlock":
                log.warning("Regla local intento desbloquear %s: descartado (ADR 0003)", item.actuator)
                continue
            results.append(self._dispatch(self._register(item.actuator, item.action,
                                                         dict(item.params))))
        return results

    # ---- envio ----

    def send(self, actuator: str, action: str, params: dict | None = None) -> CommandResult:
        """Orden explicita (demo, nube): se envia en el hilo que llama y devuelve el resultado
        final. Puede desbloquear; las ordenes de reglas pasan por `execute`."""
        return self._complete(self._register(actuator, action, dict(params or {})))

    def _register(self, actuator: str, action: str, params: dict) -> _Order:
        catalog.validate(actuator, action, params)
        params = self._lock_params(actuator, action, params)
        node = catalog.ACTUATOR_NODES[actuator]
        command_id = str(uuid.uuid4())
        now = iso_utc(self.clock.now_utc())
        with self._lock, self.db.session() as session:
            # Las ordenes previas aun sin cumplir quedan superadas por esta (reconciliacion, #6).
            for old in session.query(CommandRow).filter(
                    CommandRow.actuator == actuator, CommandRow.status.in_(("PENDING", "FAILED"))):
                old.status = "SUPERSEDED"
            session.add(CommandRow(command_id=command_id, node=node, actuator=actuator,
                                   action=action, params_json=json.dumps(params),
                                   status="PENDING", simulated=False, created_at=now))
        return _Order(command_id, node, actuator, action, params)

    def _dispatch(self, order: "_Order") -> CommandResult:
        result = self.dispatcher.submit(lambda: self._complete(order))
        return result if result is not None else CommandResult(
            order.command_id, order.node, order.actuator, order.action, "PENDING")

    def _complete(self, order: "_Order") -> CommandResult:
        """Entrega la orden a su nodo (con reintentos) y registra el acuse y el deseado.

        Una orden superada por otra mas nueva antes de enviarse no se envia: el actuador
        termina siempre en la ultima orden registrada.
        """
        command_id, node, actuator, action, params = (
            order.command_id, order.node, order.actuator, order.action, order.params)
        with self._node_lock(node):
            if self._last_status(command_id) == "SUPERSEDED":
                log.info("Orden %s %s %s %s superada antes de enviarse: no se envia", node,
                         actuator, action, params or "")
                return CommandResult(command_id, node, actuator, action, "SUPERSEDED")
            status, simulated = self._deliver(self.node_ip(node), command_id, actuator, action,
                                              params)

        acked = iso_utc(self.clock.now_utc())
        with self._lock, self.db.session() as session:
            command = session.get(CommandRow, command_id)
            # Superada mientras se enviaba: la mas nueva fija el deseado (se envia despues).
            newer = command.status == "SUPERSEDED"
            command.status, command.simulated, command.acked_at = status, simulated, acked
            # Un unlock fallido no queda como deseado: nunca se reintenta solo (ADR 0003).
            if not newer and not (action == "unlock" and status == "FAILED"):
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

    def _node_lock(self, node: str) -> threading.Lock:
        with self._lock:
            return self._node_locks.setdefault(node, threading.Lock())

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
        - Un actuador con una orden aun en camino (PENDING) no se toca: esa orden es mas nueva
          que el deseado. Decidir y registrar es atomico frente a `send`/`execute`.
        No debe llamarse dentro de una transaccion de base abierta.
        """
        orders = []
        with self._lock:
            in_flight = self._pending_actuators()
            for actuator, desired in self.desired_state().items():
                if catalog.ACTUATOR_NODES.get(actuator) != node or desired.action == "unlock"                         or actuator in in_flight:
                    continue
                if actuator in reported:
                    if _matches(actuator, desired, reported[actuator]):
                        continue
                elif self._last_status(desired.command_id) != "FAILED":
                    continue
                log.info("Reconciliando %s: deseado %s %s, reportado %s", actuator,
                         desired.action, desired.params or "", reported.get(actuator, "sin dato"))
                orders.append(self._register(actuator, desired.action, dict(desired.params)))
        return [self._dispatch(order) for order in orders]

    def _pending_actuators(self) -> set:
        with self.db.session() as session:
            return {r.actuator for r in session.query(CommandRow).filter(
                CommandRow.status == "PENDING")}

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
