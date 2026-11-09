from edge.rules_engine import Action
from edge.shared.models import CommandRow


def status(h):
    return h.client.get("/api/v1/status").get_json()


def commands(h):
    with h.db.session() as s:
        return s.query(CommandRow).order_by(CommandRow.created_at).all()


# ---- heartbeat ----

def test_heartbeat_stores_ip_last_seen_and_actuators_and_status_shows_them(harness):
    r = harness.heartbeat("node-3", ip="10.0.0.3", actuators={"fan": "off", "valve": "closed"})
    assert r.status_code == 200
    node = status(harness)["nodes"]["node-3"]
    assert node["ip"] == "10.0.0.3"
    assert node["actuators"] == {"fan": "off", "valve": "closed"}
    assert node["uptime"] == 100
    assert node["lastSeenAt"] == "2026-01-01T12:00:00.000Z"
    harness.clock.advance(5)
    harness.heartbeat("node-3", ip="10.0.0.9", actuators={"fan": "on"})
    node = status(harness)["nodes"]["node-3"]
    assert node["ip"] == "10.0.0.9" and node["lastSeenAt"] == "2026-01-01T12:00:05.000Z"


def test_heartbeat_rejects_bad_body_and_unknown_node(harness):
    assert harness.client.post("/api/v1/nodes/node-3/heartbeat", json={"actuators": {}}).status_code == 400
    assert harness.client.post("/api/v1/nodes/node-3/heartbeat", json={"ip": ""}).status_code == 400
    assert harness.client.post("/api/v1/nodes/node-9/heartbeat", json={"ip": "1.1.1.1"}).status_code == 404


# ---- ordenes manuales ----

def test_demo_command_reaches_esp32_with_command_id_and_is_recorded_applied(harness):
    harness.heartbeat("node-3")
    r = harness.command("valve", "open")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "APPLIED" and body["simulated"] is False
    (ip, sent), = harness.esp32.sent
    assert ip == "10.0.0.3"
    assert sent["commandId"] == body["commandId"]
    assert (sent["actuator"], sent["action"], sent["params"]) == ("valve", "open", {})
    (row,) = commands(harness)
    assert (row.command_id, row.node, row.status, row.simulated) == (body["commandId"], "node-3", "APPLIED", False)
    assert row.acked_at is not None


def test_simulated_reply_is_recorded(harness):
    harness.heartbeat("node-3")
    harness.esp32.simulate_missing("valve")
    body = harness.command("valve", "open").get_json()
    assert body["status"] == "APPLIED" and body["simulated"] is True
    assert commands(harness)[0].simulated is True


def test_node_failed_reply_is_recorded_failed_without_retry(harness):
    harness.heartbeat("node-3")
    harness.esp32.simulate_failed("valve")
    assert harness.command("valve", "open").get_json()["status"] == "FAILED"
    assert len(harness.esp32.sent) == 1


def test_timeout_is_retried_up_to_three_times_with_same_command_id(harness):
    harness.heartbeat("node-3")
    harness.esp32.timeout_next("10.0.0.3", 3)
    body = harness.command("valve", "open").get_json()
    assert body["status"] == "APPLIED"
    assert len(harness.esp32.sent) == 4
    assert {b["commandId"] for _, b in harness.esp32.sent} == {body["commandId"]}


def test_no_answer_after_retries_is_failed_and_recorded(harness):
    harness.heartbeat("node-3")
    harness.esp32.unreachable.add("10.0.0.3")
    body = harness.command("valve", "open").get_json()
    assert body["status"] == "FAILED"
    assert len(harness.esp32.sent) == 4
    assert commands(harness)[0].status == "FAILED"


def test_command_to_node_without_known_ip_fails_without_inventing_state(harness):
    body = harness.command("valve", "open").get_json()
    assert body["status"] == "FAILED"
    assert harness.esp32.sent == []
    assert commands(harness)[0].status == "FAILED"
    assert status(harness)["nodes"] == {}  # el estado reportado no se inventa
    # la intencion si queda: #6 la reconciliara cuando el nodo mande heartbeat
    assert status(harness)["actuators"]["valve"] == {
        "reported": None, "desired": {"action": "open", "params": {}}}


def test_demo_command_validation(harness):
    def post(b):
        return harness.client.post("/api/v1/demo/commands", json=b).status_code
    assert post({"deviceKey": "toaster", "action": "on"}) == 400
    assert post({"deviceKey": "valve", "action": "explode"}) == 400
    assert post({"deviceKey": "fan", "action": "set", "params": {"duty": 150}}) == 400
    assert post({"action": "open"}) == 400
    assert harness.esp32.sent == []


# ---- cerradura ----

def test_unlock_pulses_when_energize_to_unlock(make_harness):
    h = make_harness(LOCK_ENERGIZE_TO_UNLOCK="true", LOCK_PULSE_SECONDS="4")
    h.heartbeat("node-1")
    h.command("lock", "unlock")
    h.command("lock", "lock")
    unlock, lock = (b for _, b in h.esp32.sent)
    assert unlock["action"] == "unlock" and unlock["params"] == {"pulseSeconds": 4.0}
    assert lock["action"] == "lock" and lock["params"] == {}


def test_unlock_has_no_pulse_when_not_energize_to_unlock(make_harness):
    h = make_harness(LOCK_ENERGIZE_TO_UNLOCK="false")
    h.heartbeat("node-1")
    h.command("lock", "unlock")
    assert h.esp32.sent[0][1]["params"] == {}


def test_rules_can_never_unlock_but_explicit_orders_can(harness):
    harness.heartbeat("node-1")
    controller = harness.app.extensions["edge"].controller
    controller.execute([Action("lock", "unlock")])
    assert harness.esp32.sent == []
    controller.execute([Action("lock", "lock")])
    assert len(harness.esp32.sent) == 1
    assert harness.command("lock", "unlock").get_json()["status"] == "APPLIED"


# ---- estado deseado (base de #6) ----

def test_desired_state_tracks_last_order_per_actuator(harness):
    harness.heartbeat("node-3")
    controller = harness.app.extensions["edge"].controller
    controller.execute([Action("fan", "set", {"duty": 50})])
    controller.execute([Action("fan", "set", {"duty": 80}), Action("valve", "open")])
    desired = controller.desired_state()
    assert desired["fan"].action == "set" and desired["fan"].params == {"duty": 80}
    assert desired["valve"].action == "open"
    assert set(desired) == {"fan", "valve"}


def test_failed_order_still_sets_desired_but_failed_unlock_does_not(harness):
    harness.heartbeat("node-1")
    controller = harness.app.extensions["edge"].controller
    harness.command("lock", "lock")
    harness.esp32.unreachable.add("10.0.0.1")
    controller.execute([Action("siren", "on")])
    assert controller.desired_state()["siren"].action == "on"
    harness.command("lock", "unlock")
    assert controller.desired_state()["lock"].action == "lock"


def test_status_shows_desired_state(harness):
    harness.heartbeat("node-3", actuators={"valve": "closed"})
    harness.command("valve", "open")
    assert status(harness)["actuators"]["valve"] == {
        "reported": "closed", "desired": {"action": "open", "params": {}}}
