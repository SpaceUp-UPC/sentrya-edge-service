"""Seam 1: app armada con adaptadores falsos, probada por HTTP."""
import pytest

from edge.shared.models import SecurityEventRow


def sound(h, node="node-1"):
    return h.client.post("/api/v1/security/sound",
                         json={"node": node, "triggeredAt": "2026-01-01T12:00:00Z"})


def person(h, known=False):
    return h.client.post("/api/v1/simulate/security/person", json={"known": known})


def security_state(h):
    return h.client.get("/api/v1/status").get_json()["subsystems"]["security"]["state"]


def intrusions(h):
    return [m for m in h.mqtt.messages if m["payload"]["metricType"] == "intrusion"]


def siren_lock(h):
    return [(b["actuator"], b["action"]) for _, b in h.esp32.sent]


@pytest.fixture
def h(harness):
    harness.heartbeat("node-1")
    return harness


def test_person_then_sound_is_intrusion_with_evidence(h):
    assert person(h).status_code == 200
    h.tick(4)
    assert sound(h).get_json()["suspicious"] is True
    h.tick()
    assert siren_lock(h) == [("siren", "on"), ("lock", "lock")]
    (msg,) = intrusions(h)
    assert msg["topic"] == "sentrya/7/dev-intrusion-1/readings"
    assert (msg["payload"]["value"], msg["payload"]["unit"]) == (1, "event")
    ref = msg["payload"]["evidenceRef"]
    assert ref.startswith("evidence/") and ref.endswith(".png")
    saved = h.settings.evidence_dir / ref.split("/", 1)[1]
    assert saved.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    with h.db.session() as s:
        (row,) = s.query(SecurityEventRow).all()
        assert row.event_id == msg["payload"]["eventId"] and row.evidence_path == ref
        assert row.type == "intrusion" and row.closed_at is None
    assert security_state(h) == "INTRUSION"


def test_sound_then_person_is_intrusion(h):
    sound(h)
    h.tick(9)
    person(h)
    h.tick()
    assert len(intrusions(h)) == 1
    assert siren_lock(h) == [("siren", "on"), ("lock", "lock")]


def test_more_than_ten_seconds_apart_is_no_intrusion(h):
    person(h)
    h.tick(11)
    sound(h)
    h.tick()
    assert not intrusions(h) and not h.esp32.sent
    assert security_state(h) == "MONITORING"


def test_known_person_never_triggers(h):
    assert person(h, known=True).status_code == 200
    sound(h)
    h.tick()
    assert not intrusions(h) and not h.esp32.sent


def test_single_shot_is_not_suspicious_with_n2_m3(make_harness):
    h = make_harness(SOUND_TRIGGERS_REQUIRED="2", SOUND_WINDOW_SECONDS="3")
    h.heartbeat("node-1")
    person(h)
    assert sound(h).get_json()["suspicious"] is False
    h.tick()
    assert not intrusions(h)
    h.tick(2)
    assert sound(h).get_json()["suspicious"] is True
    h.tick()
    assert len(intrusions(h)) == 1


def test_shots_outside_the_window_do_not_add_up(make_harness):
    h = make_harness(SOUND_TRIGGERS_REQUIRED="2", SOUND_WINDOW_SECONDS="3")
    sound(h)
    h.tick(4)
    assert sound(h).get_json()["suspicious"] is False


def test_actuators_are_commanded_before_any_publication(h):
    log = []
    send, publish = h.esp32.send_command, h.mqtt.publish
    h.esp32.send_command = lambda ip, body: (log.append("esp32"), send(ip, body))[1]
    h.mqtt.publish = lambda *a, **k: (log.append("mqtt"), publish(*a, **k))[1]
    person(h)
    sound(h)
    h.tick()
    assert log[:2] == ["esp32", "esp32"] and "mqtt" in log and log.index("mqtt") > 1


def test_intrusion_still_acts_locally_when_broker_is_down(h):
    h.mqtt.broker_up = False
    person(h)
    sound(h)
    h.tick()
    assert siren_lock(h) == [("siren", "on"), ("lock", "lock")]
    h.mqtt.broker_up = True
    h.tick()
    assert len(intrusions(h)) == 1


def test_person_event_only_on_appearance_and_respects_cooldown(h):
    assert person(h).get_json()["event"] == "appeared"
    h.tick(5)
    assert person(h).get_json()["event"] == "cooldown"
    h.tick(6)  # 11 s desde la primera
    assert person(h).get_json()["event"] == "appeared"


def test_cooldown_blocks_a_second_person_from_refreshing_the_mark(h):
    person(h)
    h.tick(8)
    person(h)              # en cooldown: la marca sigue en t=0
    h.tick(4)              # t=12: la marca de t=0 queda fuera de 10 s
    sound(h)
    h.tick()
    assert not intrusions(h)


def test_person_simulation_requires_enable_simulation(make_harness):
    h = make_harness(ENABLE_SIMULATION="false")
    assert person(h).status_code == 404


def test_bad_bodies_are_rejected(h):
    assert h.client.post("/api/v1/simulate/security/person", json={}).status_code == 400
    assert h.client.post("/api/v1/security/sound", json={"triggeredAt": "x"}).status_code == 400
    assert h.client.post("/api/v1/security/sound",
                         json={"node": "node-1", "triggeredAt": "no"}).status_code == 400
