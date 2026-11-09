"""API HTTP local para las ESP32 (blueprint de Flask)."""
from flask import Blueprint, current_app, jsonify, request

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


@bp.get("/status")
def get_status():
    edge = _edge()
    with edge.db.session() as session:
        subsystems = edge.rules.snapshot(session)
        pending = outbox.pending_count(session)
    return jsonify({
        "profile": edge.profile,
        "thresholds": edge.rules.thresholds,
        "subsystems": subsystems,
        "actuators": {},
        "outbox": {"pending": pending},
        "mqtt": {"connected": edge.iot_client.connected},
    })
