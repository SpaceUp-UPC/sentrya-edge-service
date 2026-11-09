"""API HTTP local para las ESP32 (blueprint de Flask)."""
from flask import Blueprint, current_app, jsonify, request

from edge.actuation import nodes
from edge.communication import outbox
from edge.sensors.normalizers import ValidationError

bp = Blueprint("api", __name__, url_prefix="/api/v1")


def _edge():
    return current_app.extensions["edge"]


@bp.errorhandler(ValidationError)
def _bad_request(error):
    return jsonify({"error": str(error)}), 400


@bp.post("/readings")
def post_reading():
    body = request.get_json(silent=True)
    return jsonify(_edge().sensor_manager.ingest_reading(body)), 201


@bp.errorhandler(nodes.UnknownNode)
def _unknown_node(error):
    return jsonify({"error": f"nodo desconocido: {error}"}), 404


@bp.post("/nodes/<node>/heartbeat")
def post_heartbeat(node):
    edge = _edge()
    extra = {d.node for d in edge.devices.values()}
    nodes.record_heartbeat(edge.db, edge.clock, node, request.get_json(silent=True), extra)
    body = request.get_json(silent=True)
    edge.controller.reconcile(node, body.get("actuators") or {})
    return jsonify({"ok": True})


@bp.post("/demo/commands")
def post_demo_command():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("deviceKey"), str) \
            or not isinstance(body.get("action"), str):
        raise ValidationError("deviceKey y action son obligatorios")
    params = body.get("params", {})
    if not isinstance(params, dict):
        raise ValidationError("params debe ser un objeto")
    result = _edge().controller.send(body["deviceKey"], body["action"], params)
    return jsonify({"commandId": result.command_id, "status": result.status,
                    "simulated": result.simulated})


@bp.get("/status")
def get_status():
    edge = _edge()
    with edge.db.session() as session:
        subsystems = edge.rules.snapshot(session)
        pending = outbox.pending_count(session)
        node_view = nodes.nodes_view(session)
        actuator_view = nodes.actuators_view(session)
    return jsonify({
        "profile": edge.profile,
        "thresholds": edge.rules.thresholds,
        "subsystems": subsystems,
        "nodes": node_view,
        "actuators": actuator_view,
        "outbox": {"pending": pending},
        "mqtt": {"connected": edge.iot_client.connected},
    })
