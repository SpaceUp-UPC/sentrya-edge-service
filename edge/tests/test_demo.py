"""Seam 1 (#10): herramientas de demo (reset, simulacion, logs, status) por HTTP."""
import logging

import pytest

from edge.tests.test_security import intrusions, person, security_state, sound
from edge.tests.test_status_and_config import status


def orders(h):
    return [(b["actuator"], b["action"], b["params"]) for _, b in h.esp32.sent]


def critical(h, n=3):
    for _ in range(n):
        assert h.post_reading(value=40).status_code == 201


def reset(h):
    return h.client.post("/api/v1/demo/reset")


@pytest.fixture
def h(harness):
    for node in ("node-1", "node-2", "node-3"):
        harness.heartbeat(node)
    return harness


def test_reset_after_intrusion_and_critical_restores_initial_state_without_unlock(h):
    person(h)
    sound(h)
    h.tick()
    critical(h)
    assert security_state(h) == "INTRUSION"
    assert status(h)["subsystems"]["temperature"]["state"] == "CRITICAL"
    h.esp32.sent.clear()

    resp = reset(h)

    assert resp.status_code == 200
    subsystems = status(h)["subsystems"]
    assert subsystems["temperature"]["state"] == "NORMAL"
    assert subsystems["security"]["state"] == "MONITORING"
    sent = orders(h)
    assert ("siren", "off", {}) in sent
    assert ("buzzer", "off", {}) in sent
    assert ("fan", "set", {"duty": 0}) in sent
    assert all(action != "unlock" for _, action, _ in sent)
    assert all(actuator != "lock" for actuator, _, _ in sent)


def test_reset_asks_node_3_for_the_normal_sampling_interval(h):
    critical(h)
    h.esp32.sent.clear()
    reset(h)
    sampling = [(ip, b["params"]) for ip, b in h.esp32.sent if b["actuator"] == "sampling"]
    assert sampling == [("10.0.0.3", {"intervalSeconds": 30})]


def test_reset_keeps_the_humidifier_mode(h):
    h.command("humidifier", "mode", mode="manual")
    reset(h)
    assert status(h)["subsystems"]["humidity"]["mode"] == "manual"
    assert ("humidifier", "off", {}) in orders(h)


def test_reset_closes_the_open_window_and_does_not_escalate_later(h):
    person(h)
    sound(h)
    h.tick()
    reset(h)
    n = len(h.mqtt.messages)
    h.tick(60)
    assert [m for m in h.mqtt.messages[n:] if m["payload"]["metricType"] == "escalation"] == []
    assert security_state(h) == "MONITORING"


def test_reset_makes_the_demo_repeatable(h):
    person(h)
    sound(h)
    h.tick()
    reset(h)
    h.tick(1)
    person(h)
    sound(h)
    h.tick()
    assert security_state(h) == "INTRUSION"
    assert len(intrusions(h)) == 2


def test_reset_with_unreachable_node_still_resets_state(make_harness):
    h = make_harness()  # sin heartbeats: ningun nodo tiene IP
    critical(h)
    resp = reset(h)
    assert resp.status_code == 200
    assert status(h)["subsystems"]["temperature"]["state"] == "NORMAL"
    assert all(c["status"] == "FAILED" for c in resp.get_json()["commands"])


def test_simulate_readings_has_the_same_effect_as_a_real_reading(h):
    one = {"node": "node-3", "deviceKey": "temperature", "metricType": "temperature",
           "value": 40, "unit": "C"}
    assert h.client.post("/api/v1/simulate/readings", json=one).status_code == 201
    assert h.client.post("/api/v1/simulate/readings", json={"readings": [one, one]}).status_code == 201
    assert status(h)["subsystems"]["temperature"]["state"] == "CRITICAL"
    assert any(a == "fan" for a, _, _ in orders(h))
    h.tick()
    assert any(m["payload"]["metricType"] == "temperature" for m in h.mqtt.messages)


def test_simulate_readings_validates_input(h):
    assert h.client.post("/api/v1/simulate/readings", json={}).status_code == 400
    assert h.client.post("/api/v1/simulate/readings", json={"readings": "x"}).status_code == 400


def test_simulate_endpoints_are_404_when_disabled(make_harness):
    h = make_harness(ENABLE_SIMULATION="false")
    assert h.client.post("/api/v1/simulate/readings", json={}).status_code == 404
    assert h.client.post("/api/v1/simulate/security/person", json={"known": False}).status_code == 404
    assert h.client.post("/api/v1/simulate/otra-cosa", json={}).status_code == 404
    assert h.post_reading().status_code == 201  # la API real sigue viva
    assert reset(h).status_code == 200            # el reset no es simulacion


def test_transitions_and_orders_are_logged_with_subsystem_before_after_and_reason(h, caplog):
    with caplog.at_level(logging.INFO, logger="edge"):
        critical(h)
    text = caplog.text
    assert "[temperature]" in text and "NORMAL -> " in text and "CRITICAL" in text
    assert "lectura temperature=40" in text
    assert "Orden node-3 fan set" in text


def test_status_reports_subsystems_camera_outbox_actuators_and_mqtt(h):
    body = status(h)
    assert body["subsystems"]["security"]["camera"] == "MOCK"
    assert body["outbox"] == {"pending": 0}
    assert "actuators" in body and body["mqtt"] == {"connected": True}
