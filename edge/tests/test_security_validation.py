"""Seam 1 (#9): ventana de validacion, escalamiento y fin de la alarma, por HTTP."""
import pytest

from edge.shared.models import SecurityEventRow
from edge.tests.test_security import intrusions, person, security_state, sound


def validate(h, decision, **extra):
    return h.client.post("/api/v1/demo/security/validation", json={"decision": decision, **extra})


def escalations(h):
    return [m["payload"] for m in h.mqtt.messages if m["payload"]["metricType"] == "escalation"]


def orders(h):
    return [(b["actuator"], b["action"]) for _, b in h.esp32.sent]


def event_row(h):
    with h.db.session() as s:
        (row,) = s.query(SecurityEventRow).all()
        return {c: getattr(row, c) for c in ("event_id", "validation", "opened_at",
                                             "deadline_at", "closed_at")}


def intrude(h):
    person(h)
    sound(h)
    h.tick()
    (msg,) = intrusions(h)
    return msg["payload"]["eventId"]


@pytest.fixture
def h(harness):
    harness.heartbeat("node-1")
    return harness


def test_window_timeout_publishes_escalation(h):
    event_id = intrude(h)
    assert event_row(h)["deadline_at"] == "2026-01-01T12:00:30.000Z"
    h.tick(29)
    assert not escalations(h) and security_state(h) == "INTRUSION"
    h.tick(1)
    (esc,) = escalations(h)
    assert (esc["eventId"], esc["reason"], esc["value"], esc["unit"]) == \
        (event_id, "timeout", 1, "event")
    assert (h.mqtt.messages[-1]["topic"]) == "sentrya/7/dev-escalation-1/readings"
    row = event_row(h)
    assert row["validation"] == "timeout" and row["closed_at"] == "2026-01-01T12:00:30.000Z"
    h.tick(60)
    assert len(escalations(h)) == 1


def test_confirm_publishes_escalation(h):
    event_id = intrude(h)
    h.tick(3)
    resp = validate(h, "confirm")
    assert resp.status_code == 200
    assert resp.get_json() == {"eventId": event_id, "decision": "confirm", "state": "ALARM"}
    (esc,) = escalations(h)
    assert (esc["eventId"], esc["reason"]) == (event_id, "confirmed")
    assert event_row(h)["validation"] == "confirmed"
    h.tick(30)
    assert len(escalations(h)) == 1  # vencer la ventana ya no escala otra vez


def test_reject_turns_the_siren_off_and_returns_to_monitoring_without_escalating(h):
    intrude(h)
    h.tick(3)
    assert validate(h, "reject").get_json()["state"] == "MONITORING"
    assert orders(h) == [("siren", "on"), ("lock", "lock"), ("siren", "off")]
    assert security_state(h) == "MONITORING" and event_row(h)["validation"] == "rejected"
    h.tick(60)
    assert not escalations(h)


def test_second_correlation_with_the_window_open_is_no_new_intrusion(h):
    intrude(h)
    h.tick(11)  # fuera del cooldown de persona: marcas nuevas
    person(h)
    sound(h)
    h.tick()
    assert len(intrusions(h)) == 1 and security_state(h) == "INTRUSION"
    assert orders(h) == [("siren", "on"), ("lock", "lock")]


def test_siren_turns_off_by_itself_after_the_profile_max_and_monitoring_resumes(h):
    intrude(h)
    h.tick(2)
    validate(h, "confirm")
    h.tick(12)  # 14 s con la sirena encendida (demo: 15 s)
    assert security_state(h) == "ALARM" and orders(h)[-1] == ("lock", "lock")
    h.tick(1)
    assert orders(h)[-1] == ("siren", "off") and security_state(h) == "MONITORING"
    h.tick(11)  # la demo se puede repetir
    person(h)
    sound(h)
    h.tick()
    assert len(intrusions(h)) == 2


def test_timeout_with_the_siren_past_its_max_ends_the_alarm_too(h):
    intrude(h)
    h.tick(30)  # demo: ventana 30 s > sirena 15 s
    assert len(escalations(h)) == 1
    assert orders(h)[-1] == ("siren", "off") and security_state(h) == "MONITORING"


def test_actuation_comes_before_publishing_the_escalation(h):
    intrude(h)
    log = []
    send, publish = h.esp32.send_command, h.mqtt.publish
    h.esp32.send_command = lambda ip, body: (log.append(("esp32", body["action"])), send(ip, body))[1]
    h.mqtt.publish = lambda *a, **k: (log.append(("mqtt", None)), publish(*a, **k))[1]
    h.tick(30)
    assert log == [("esp32", "off"), ("mqtt", None)]


def test_no_security_transition_sends_unlock(h):
    intrude(h)
    validate(h, "reject")
    h.tick(11)
    intrude_again = (person(h), sound(h), h.tick())
    validate(h, "confirm")
    h.tick(20)
    h.tick(11)
    person(h)
    sound(h)
    h.tick(31)
    assert intrude_again and len(intrusions(h)) == 3 and len(escalations(h)) == 2
    assert ("lock", "unlock") not in orders(h)
    assert all(a != "unlock" for _, a in orders(h))


def test_restart_with_an_open_window_closes_it_as_timeout_and_escalates(make_harness):
    h = make_harness()
    h.heartbeat("node-1")
    event_id = intrude(h)
    h.tick(5)
    h2 = make_harness()  # reinicio: nueva app sobre la misma base
    h2.tick()
    (esc,) = escalations(h2)
    assert (esc["eventId"], esc["reason"]) == (event_id, "timeout")
    row = event_row(h2)
    assert row["validation"] == "timeout" and row["closed_at"] == "2026-01-01T12:00:05.000Z"
    assert security_state(h2) == "MONITORING"
    assert orders(h2)[-1] == ("siren", "off")
    assert all(a != "unlock" for _, a in orders(h2))
    h2.tick(60)  # la ventana no se retoma
    assert len(escalations(h2)) == 1
    h3 = make_harness()  # otro reinicio: el evento ya cerrado no vuelve a escalar
    h3.tick()
    assert len(escalations(h3)) == 1


def test_validation_endpoint_errors(h):
    assert validate(h, "confirm").status_code == 409  # sin ventana abierta
    assert validate(h, "maybe").status_code == 400
    assert h.client.post("/api/v1/demo/security/validation", json={}).status_code == 400
    event_id = intrude(h)
    assert validate(h, "confirm", eventId="otro").status_code == 409
    assert validate(h, "confirm", eventId=event_id).status_code == 200
