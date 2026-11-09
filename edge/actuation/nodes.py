"""Registro de nodos: heartbeat de las ESP32 y vista para /status."""
import json

from edge.actuation import catalog
from edge.sensors.normalizers import ValidationError
from edge.shared.clock import iso_utc
from edge.shared.logging import get_logger
from edge.shared.models import DesiredStateRow, NodeRow

log = get_logger("nodes")


class UnknownNode(Exception):
    pass


def record_heartbeat(db, clock, node: str, body, extra_nodes=()) -> None:
    if node not in catalog.known_nodes() | set(extra_nodes):
        raise UnknownNode(node)
    if not isinstance(body, dict) or not isinstance(body.get("ip"), str) or not body["ip"].strip():
        raise ValidationError("ip es obligatoria")
    actuators = body.get("actuators", {})
    if not isinstance(actuators, dict):
        raise ValidationError("actuators debe ser un objeto")
    uptime = body.get("uptime")
    if uptime is not None and (isinstance(uptime, bool) or not isinstance(uptime, (int, float))):
        raise ValidationError("uptime debe ser numerico")
    with db.session() as session:
        row = session.get(NodeRow, node)
        if row is None:
            row = NodeRow(node=node)
            session.add(row)
        row.ip = body["ip"].strip()
        row.last_seen_at = iso_utc(clock.now_utc())
        row.actuators_json = json.dumps(actuators)
        row.uptime = uptime
    log.info("Heartbeat de %s desde %s", node, row.ip)


def nodes_view(session) -> dict:
    return {r.node: {"ip": r.ip, "lastSeenAt": r.last_seen_at, "uptime": r.uptime,
                     "actuators": json.loads(r.actuators_json or "{}")}
            for r in session.query(NodeRow).all()}


def actuators_view(session) -> dict:
    """Por actuador: estado reportado por su nodo (heartbeat) y estado deseado."""
    nodes = nodes_view(session)
    desired = {r.actuator: {"action": r.action, "params": json.loads(r.params_json)}
               for r in session.query(DesiredStateRow).all()}
    out = {}
    for actuator, node in catalog.ACTUATOR_NODES.items():
        reported = nodes.get(node, {}).get("actuators", {}).get(actuator)
        if reported is not None or actuator in desired:
            out[actuator] = {"reported": reported, "desired": desired.get(actuator)}
    return out
