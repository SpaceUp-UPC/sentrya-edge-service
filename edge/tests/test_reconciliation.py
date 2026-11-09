"""Reconciliacion por estado deseado en cada heartbeat (Seam 1, ticket #6)."""
from edge.shared.models import CommandRow


def commands(h, actuator=None):
    with h.db.session() as s:
        rows = s.query(CommandRow).all()
        return [(r.actuator, r.action, r.status) for r in rows if actuator in (None, r.actuator)]


def sent(h, actuator):
    return [b for _, b in h.esp32.sent if b["actuator"] == actuator]


def test_failed_order_is_resent_once_when_heartbeat_differs(harness):
    harness.heartbeat("node-3", actuators={"valve": "closed"})
    harness.esp32.simulate_failed("valve")
    assert harness.command("valve", "open").get_json()["status"] == "FAILED"
    harness.esp32.responses.clear()
    harness.esp32.sent.clear()
    harness.heartbeat("node-3", actuators={"valve": "closed"})
    resent = sent(harness, "valve")
    assert len(resent) == 1 and resent[0]["action"] == "open"
    assert commands(harness, "valve")[-1] == ("valve", "open", "APPLIED")
    assert ("valve", "open", "SUPERSEDED") in commands(harness, "valve")


def test_only_latest_desired_is_sent_and_older_orders_are_superseded(harness):
    harness.heartbeat("node-3", actuators={"valve": "closed"})
    harness.esp32.unreachable.add("10.0.0.3")
    harness.command("valve", "open")
    harness.command("valve", "close")
    harness.command("valve", "open")
    harness.esp32.unreachable.clear()
    harness.esp32.sent.clear()
    harness.heartbeat("node-3", actuators={"valve": "closed"})
    resent = sent(harness, "valve")
    assert [b["action"] for b in resent] == ["open"]
    statuses = [s for _, _, s in commands(harness, "valve")]
    assert statuses.count("SUPERSEDED") == 3 and statuses[-1] == "APPLIED"


def test_nothing_is_sent_when_heartbeat_matches_desired(harness):
    harness.heartbeat("node-3", actuators={"valve": "closed"})
    harness.command("valve", "open")
    harness.esp32.sent.clear()
    harness.heartbeat("node-3", actuators={"valve": "open"})
    assert harness.esp32.sent == []


def test_fan_duty_is_compared_with_reported_value(harness):
    harness.heartbeat("node-3", actuators={"fan": "off"})
    harness.esp32.unreachable.add("10.0.0.3")
    harness.command("fan", "set", duty=50)
    harness.esp32.unreachable.clear()
    harness.esp32.sent.clear()
    harness.heartbeat("node-3", actuators={"fan": 50})
    assert harness.esp32.sent == []
    harness.heartbeat("node-3", actuators={"fan": 0})
    assert [b["params"] for b in sent(harness, "fan")] == [{"duty": 50}]


def test_failed_unlock_is_never_resent_but_failed_lock_is(harness):
    harness.heartbeat("node-1", actuators={"lock": "locked"})
    harness.command("lock", "lock")
    harness.esp32.unreachable.add("10.0.0.1")
    harness.command("lock", "unlock")
    harness.esp32.unreachable.clear()
    harness.esp32.sent.clear()
    harness.heartbeat("node-1", actuators={"lock": "locked"})
    assert harness.esp32.sent == []
    # un lock fallido si se reintenta
    harness.esp32.unreachable.add("10.0.0.1")
    harness.command("lock", "lock")
    harness.esp32.unreachable.clear()
    harness.esp32.sent.clear()
    harness.heartbeat("node-1", actuators={"lock": "unlocked"})
    assert [b["action"] for b in sent(harness, "lock")] == ["lock"]


def test_applied_unlock_that_relocked_by_pulse_is_not_reissued(harness):
    harness.heartbeat("node-1", actuators={"lock": "locked"})
    assert harness.command("lock", "unlock").get_json()["status"] == "APPLIED"
    harness.esp32.sent.clear()
    harness.heartbeat("node-1", actuators={"lock": "locked"})
    assert harness.esp32.sent == []


def test_order_to_node_without_ip_is_sent_on_first_heartbeat(harness):
    assert harness.command("valve", "open").get_json()["status"] == "FAILED"
    harness.heartbeat("node-3", actuators={})
    assert [b["action"] for b in sent(harness, "valve")] == ["open"]
    harness.esp32.sent.clear()
    harness.heartbeat("node-3", actuators={})
    assert harness.esp32.sent == []  # ya aplicada: no se repite


def test_reconciliation_only_touches_actuators_of_the_reporting_node(harness):
    harness.heartbeat("node-3", actuators={"valve": "closed"})
    harness.esp32.unreachable.add("10.0.0.3")
    harness.command("valve", "open")
    harness.esp32.unreachable.clear()
    harness.esp32.sent.clear()
    harness.heartbeat("node-2", actuators={"humidifier": "off"})
    assert harness.esp32.sent == []
