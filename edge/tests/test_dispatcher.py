"""Despachador de ordenes del Local Safety Controller: la peticion de una ESP32 nunca espera
la orden que la propia regla decidio (la ESP32 podria no atender mientras espera su respuesta)."""
import threading

from edge.actuation.dispatcher import ThreadDispatcher
from edge.shared.models import CommandRow
from edge.tests.fakes import ManualDispatcher


def statuses(h, actuator):
    with h.db.session() as s:
        return [r.status for r in s.query(CommandRow).filter(CommandRow.actuator == actuator)]


def test_reading_response_does_not_wait_for_the_esp32_order(make_harness):
    dispatcher = ManualDispatcher()
    h = make_harness(_dispatcher=dispatcher)
    h.heartbeat("node-3")
    for _ in range(3):
        assert h.post_reading(value=40).status_code == 201
    assert h.esp32.sent == []                      # la regla decidio, pero nada se envio aun
    assert statuses(h, "buzzer") == ["PENDING"]
    dispatcher.run_pending()                       # el hilo trabajador envia
    assert ("buzzer", "on") in [(b["actuator"], b["action"]) for _, b in h.esp32.sent]
    assert statuses(h, "buzzer") == ["APPLIED"]


def test_order_is_queued_before_the_reading_is_published(make_harness):
    dispatcher = ManualDispatcher()
    h = make_harness(_dispatcher=dispatcher)
    h.heartbeat("node-3")
    h.post_reading(value=29)
    assert len(dispatcher.queue) > 0               # encolada en la misma peticion
    h.tick()
    assert any(m["payload"]["metricType"] == "temperature" for m in h.mqtt.messages)


def test_sound_shot_response_does_not_wait_for_the_siren(make_harness):
    dispatcher = ManualDispatcher()
    h = make_harness(_dispatcher=dispatcher)
    h.heartbeat("node-1")
    h.client.post("/api/v1/simulate/security/person", json={"known": False})
    assert h.client.post("/api/v1/security/sound", json={"node": "node-1"}).status_code == 201
    assert h.esp32.sent == []
    dispatcher.run_pending()
    assert ("siren", "on") in [(b["actuator"], b["action"]) for _, b in h.esp32.sent]


def test_heartbeat_reconciliation_does_not_wait_for_the_esp32(make_harness):
    dispatcher = ManualDispatcher()
    h = make_harness(_dispatcher=dispatcher)
    h.heartbeat("node-3", actuators={"valve": "closed"})
    h.esp32.simulate_failed("valve")
    h.command("valve", "open")                     # orden explicita: sincrona, falla
    h.esp32.responses.clear()
    h.esp32.sent.clear()
    assert h.heartbeat("node-3", actuators={"valve": "closed"}).status_code == 200
    assert h.esp32.sent == []
    dispatcher.run_pending()
    assert [b["action"] for _, b in h.esp32.sent] == ["open"]


def test_thread_dispatcher_runs_jobs_in_order_in_its_own_thread():
    dispatcher = ThreadDispatcher()
    seen, done = [], threading.Event()
    caller = threading.current_thread()
    dispatcher.submit(lambda: seen.append((1, threading.current_thread() is caller)))
    dispatcher.submit(lambda: seen.append((2, threading.current_thread() is caller)))
    dispatcher.submit(done.set)
    assert done.wait(2)
    assert seen == [(1, False), (2, False)]
    dispatcher.stop()


def test_thread_dispatcher_survives_a_failing_job():
    dispatcher = ThreadDispatcher()
    done = threading.Event()
    dispatcher.submit(lambda: 1 / 0)
    dispatcher.submit(done.set)
    assert done.wait(2)
    dispatcher.stop()
