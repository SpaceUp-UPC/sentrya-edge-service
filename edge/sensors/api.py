"""API HTTP local para las ESP32 (blueprint de Flask)."""
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from edge.actuation import nodes
from edge.communication import outbox
from edge.security.camera import vision_status
from edge.sensors.normalizers import ValidationError

bp = Blueprint("api", __name__, url_prefix="/api/v1")


def _edge():
    return current_app.extensions["edge"]


@bp.errorhandler(ValidationError)
def _bad_request(error):
    return jsonify({"error": str(error)}), 400


@bp.before_request
def _simulation_gate():
    """Los /simulate/* solo existen con ENABLE_SIMULATION=true."""
    if request.path.startswith(bp.url_prefix + "/simulate/") and not _edge().settings.enable_simulation:
        return jsonify({"error": "simulacion deshabilitada"}), 404


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
    if body["action"] == "mode":   # AUTO/MANUAL: lo resuelve el modulo de reglas, no el actuador
        mode = params.get("mode")
        edge = _edge()
        try:
            with edge.db.session() as session:
                known = edge.rules.set_mode(session, body["deviceKey"], mode)
        except ValueError as error:
            raise ValidationError(str(error)) from None
        if not known:
            raise ValidationError(f"{body['deviceKey']} no admite el modo")
        return jsonify({"deviceKey": body["deviceKey"], "mode": mode})
    result = _edge().controller.send(body["deviceKey"], body["action"], params)
    return jsonify({"commandId": result.command_id, "status": result.status,
                    "simulated": result.simulated})


@bp.post("/security/sound")
def post_security_sound():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("node"), str) or not body["node"]:
        raise ValidationError("node es obligatorio")
    if body.get("triggeredAt") is not None:
        try:
            datetime.fromisoformat(str(body["triggeredAt"]).replace("Z", "+00:00"))
        except ValueError:
            raise ValidationError("triggeredAt debe ser ISO-8601") from None
    return jsonify({"suspicious": _edge().security.on_sound_shot()}), 201


@bp.post("/demo/security/validation")
def post_demo_validation():
    """Simula la respuesta del usuario en la app (la misma via que el topico MQTT)."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or body.get("decision") not in ("confirm", "reject"):
        raise ValidationError("decision debe ser 'confirm' o 'reject'")
    event_id = body.get("eventId")
    if event_id is not None and not isinstance(event_id, str):
        raise ValidationError("eventId debe ser un string")
    result = _edge().security.validate(body["decision"], event_id)
    if result is None:
        return jsonify({"error": "no hay una ventana de validacion abierta para ese evento"}), 409
    return jsonify(result)


@bp.post("/simulate/security/person")
def post_simulate_person():
    edge = _edge()
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("known"), bool):
        raise ValidationError("known (booleano) es obligatorio")
    if not hasattr(edge.vision, "inject"):
        return jsonify({"error": "el detector activo no es el mock"}), 409
    return jsonify({"known": body["known"], "event": edge.security.simulate_person(body["known"])})


@bp.post("/simulate/readings")
def post_simulate_readings():
    """Lecturas falsas por la misma via que una real. Un objeto o {"readings": [...]}."""
    body = request.get_json(silent=True)
    items = body.get("readings") if isinstance(body, dict) and "readings" in body else [body]
    if not isinstance(items, list) or not items or not all(isinstance(i, dict) for i in items):
        raise ValidationError("se espera una lectura o {\"readings\": [lecturas]}")
    results = [_edge().sensor_manager.ingest_reading(item) for item in items]
    return jsonify(results[0] if len(items) == 1 and "readings" not in body
                   else {"readings": results}), 201


@bp.post("/demo/reset")
def post_demo_reset():
    results = _edge().reset()
    return jsonify({"reset": True, "commands": [
        {"commandId": r.command_id, "actuator": r.actuator, "action": r.action,
         "status": r.status} for r in results]})


def _camera_status(vision) -> str:
    """mock | ok | degraded. Un detector real puede exponer `camera_status()` (ticket de vision)."""
    custom = getattr(vision, "camera_status", None)
    if callable(custom):
        return custom()
    if not getattr(vision, "available", True):
        return "degraded"
    return "mock" if hasattr(vision, "inject") else "ok"


@bp.get("/status")
def get_status():
    edge = _edge()
    with edge.db.session() as session:
        subsystems = edge.rules.snapshot(session)
        pending = outbox.pending_count(session)
        node_view = nodes.nodes_view(session)
        actuator_view = nodes.actuators_view(session)
    if "security" in subsystems:  # camara y modelos: degradado si no estan disponibles
        subsystems["security"].update(vision_status(edge.vision))
    return jsonify({
        "profile": edge.profile,
        "thresholds": edge.rules.thresholds,
        "subsystems": subsystems,
        "nodes": node_view,
        "actuators": actuator_view,
        "outbox": {"pending": pending},
        "mqtt": {"connected": edge.iot_client.connected},
        "camera": {"status": _camera_status(edge.vision)},
    })
