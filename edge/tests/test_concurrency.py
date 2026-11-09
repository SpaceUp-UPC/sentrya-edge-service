"""Concurrencia: un solo lock para leer-modificar-guardar subsystem_state y un Local Safety
Controller seguro entre hilos (el despachador, las peticiones HTTP y el tick corren a la vez)."""
import threading

import pytest

from edge.rules_engine import Action
from edge.shared.models import CommandRow
from edge.tests.fakes import ManualDispatcher


def waits_for_the_edge_lock(h, action):
    """Ejecuta `action` en otro hilo mientras se tiene el lock: debe esperar a que se libere."""
    edge = h.app.extensions["edge"]
    done = threading.Event()

    def run():
        action(h.app.test_client())
        done.set()

    with edge.lock:
        worker = threading.Thread(target=run)
        worker.start()
        blocked = not done.wait(0.2)
    worker.join(5)
    return blocked and done.is_set()


READING = {"node": "node-3", "deviceKey": "temperature", "metricType": "temperature",
           "value": 29, "unit": "C"}


@pytest.mark.parametrize("name, action", [
    ("lectura", lambda c: c.post("/api/v1/readings", json=READING)),
    ("modo", lambda c: c.post("/api/v1/demo/commands", json={
        "deviceKey": "humidifier", "action": "mode", "params": {"mode": "manual"}})),
    ("reset", lambda c: c.post("/api/v1/demo/reset")),
    ("sonido", lambda c: c.post("/api/v1/security/sound", json={"node": "node-1"})),
])
def test_state_changes_wait_for_the_single_edge_lock(harness, name, action):
    assert waits_for_the_edge_lock(harness, action), name


def test_security_uses_the_same_lock(harness):
    edge = harness.app.extensions["edge"]
    assert edge.security.lock is edge.lock


def fan_orders(h):
    return [b["params"]["duty"] for _, b in h.esp32.sent if b["actuator"] == "fan"]


def test_reconcile_never_resends_an_old_desired_over_a_newer_order_in_flight(make_harness):
    dispatcher = ManualDispatcher()
    h = make_harness(_dispatcher=dispatcher)
    h.heartbeat("node-3")
    h.command("fan", "set", duty=0)                 # deseado aplicado: 0
    h.esp32.sent.clear()
    h.post_reading(value=29)                        # WARNING: fan 50 en el despachador
    h.heartbeat("node-3", actuators={"fan": 100})   # difiere del deseado viejo
    dispatcher.run_pending()
    assert fan_orders(h) == [50]
    assert h.app.extensions["edge"].controller.desired_state()["fan"].params == {"duty": 50}


def test_order_superseded_before_delivery_is_not_sent(make_harness):
    dispatcher = ManualDispatcher()
    h = make_harness(_dispatcher=dispatcher)
    h.heartbeat("node-3")
    h.post_reading(value=29)                        # fan 50
    for _ in range(3):
        h.post_reading(value=40)                    # CRITICAL: fan 100
    dispatcher.run_pending()
    assert fan_orders(h) == [100]
    with h.db.session() as s:
        rows = {r.params_json: r.status for r in s.query(CommandRow).filter(
            CommandRow.actuator == "fan")}
    assert rows == {'{"duty": 50}': "SUPERSEDED", '{"duty": 100}': "APPLIED"}


def test_order_left_pending_by_a_previous_process_does_not_block_reconciliation(make_harness):
    first = make_harness(_dispatcher=ManualDispatcher())
    first.heartbeat("node-3")
    first.command("valve", "close")
    first.app.extensions["edge"].controller.execute([Action("buzzer", "on")])  # nunca se envia
    first.command("buzzer", "off")
    first.app.extensions["edge"].controller.execute([Action("valve", "open")])   # nunca se envia
    second = make_harness()                         # reinicio
    second.esp32.sent.clear()
    second.heartbeat("node-3", actuators={"valve": "open", "buzzer": "on"})
    sent = [(b["actuator"], b["action"]) for _, b in second.esp32.sent]
    assert ("valve", "close") in sent and ("buzzer", "off") in sent
