"""Humedad (#7): Seam 2 (evaluate puro) y Seam 1 (app por HTTP con fakes)."""
from edge.humidity.rules import MODULE
from edge.rules_engine import Action, RuleInput
from edge.shared.models import OutboxRow
from edge.tests.fakes import FakeClock

CFG = {"humidity": {"on_below": 40, "off_at": 55, "high_above": 65, "high_hysteresis": 3,
                    "consecutive_readings": 3, "read_interval_seconds": 10, "telemetry_seconds": 60}}


def reading(value=None, error=False):
    return RuleInput("reading", CFG, "humidity", "humidity", value, {"error": True} if error else {})


TICK = RuleInput("tick", CFG)


class Runner:
    """Aplica evaluate en secuencia acumulando acciones y eventos."""

    def __init__(self, mode="auto"):
        self.clock = FakeClock()
        self.state = MODULE.set_mode(MODULE.initial_state(), mode)
        self.actions, self.events = [], []

    def feed(self, *values):
        for v in values:
            self.step(reading(v))

    def step(self, rule_input, advance=0):
        self.clock.advance(advance)
        self.state, a, e = MODULE.evaluate(rule_input, self.state, self.clock)
        self.actions += a
        self.events += e
        return a, e


# ---- Seam 2 ----

def test_three_low_readings_turn_humidifier_on_then_off_at_threshold():
    r = Runner()
    r.feed(35, 35)
    assert r.state["state"] == "NORMAL" and r.actions == []
    r.feed(35)
    assert r.state["state"] == "LOW" and r.actions == [Action("humidifier", "on")]
    r.feed(50)
    assert r.state["state"] == "LOW"
    r.feed(55)
    assert r.state["state"] == "NORMAL"
    assert r.actions == [Action("humidifier", "on"), Action("humidifier", "off")]


def test_low_readings_must_be_consecutive():
    r = Runner()
    r.feed(35, 35, 45, 35, 35)
    assert r.state["state"] == "NORMAL" and r.actions == []


def test_high_entry_and_exit_with_hysteresis():
    r = Runner()
    r.feed(70, 70)
    assert r.state["state"] == "NORMAL"
    r.feed(70)
    assert r.state["state"] == "HIGH"
    assert r.actions == [Action("humidifier", "off")]
    r.feed(63, 63, 63)          # 63 no es < 62: no sale
    assert r.state["state"] == "HIGH"
    r.feed(61, 61)
    assert r.state["state"] == "HIGH"
    r.feed(61)
    assert r.state["state"] == "NORMAL"


def test_high_exit_counter_resets_if_a_reading_goes_back_up():
    r = Runner()
    r.feed(70, 70, 70, 61, 61, 64, 61, 61)
    assert r.state["state"] == "HIGH"


def test_sensor_error_turns_humidifier_off_and_emits_device_error_once():
    r = Runner()
    r.feed(35, 35, 35)
    _, events = r.step(reading(error=True))
    assert r.actions[-1] == Action("humidifier", "off")
    assert [e.metric_type for e in events] == ["device_error"]
    _, events = r.step(reading(error=True))
    assert events == []


def test_sensor_down_by_silence_uses_tick_and_clock():
    r = Runner()
    r.feed(35, 35, 35)
    a, e = r.step(TICK, advance=30)       # 30 s = 3 x 10 s: aun no
    assert a == [] and e == []
    a, e = r.step(TICK, advance=1)
    assert a == [Action("humidifier", "off")]
    assert [x.metric_type for x in e] == ["device_error"]


def test_sensor_recovers_with_next_reading():
    r = Runner()
    r.feed(50)
    r.step(TICK, advance=31)
    assert r.state["sensor_down"]
    r.feed(50)
    assert not r.state["sensor_down"]


def test_manual_mode_never_turns_on_but_does_turn_off_for_high_and_sensor_down():
    r = Runner(mode="manual")
    r.feed(35, 35, 35)
    assert r.state["state"] == "LOW" and r.actions == []
    r.feed(70, 70, 70)
    assert r.state["state"] == "HIGH" and r.actions == [Action("humidifier", "off")]
    m = Runner(mode="manual")
    m.feed(50)
    m.step(TICK, advance=31)
    assert m.actions == [Action("humidifier", "off")]


def test_state_change_emits_immediate_telemetry_and_tick_every_60s():
    r = Runner()
    r.feed(35, 35)
    _, events = r.step(reading(35))
    assert [(e.metric_type, e.data["value"], e.data["unit"], e.data["state"]) for e in events] \
        == [("humidity", 35, "%", "LOW")]
    _, e = r.step(TICK, advance=5)
    assert e == []
    r.step(reading(36), advance=50)                # lectura fresca; telemetria cumple 60 s
    _, e = r.step(TICK, advance=5)
    assert [x.metric_type for x in e] == ["humidity"] and e[0].data["value"] == 36


# ---- Seam 1 ----

def post(h, value=None, error=False, advance=0):
    if advance:
        h.clock.advance(advance)
    body = {"node": "node-2", "deviceKey": "humidity", "metricType": "humidity"}
    if error:
        body["error"] = "dht22"
    else:
        body.update(value=value, unit="%")
    r = h.client.post("/api/v1/readings", json=body)
    assert r.status_code == 201, r.get_json()
    return r


def humidifier_commands(h):
    return [b["action"] for _, b in h.esp32.sent if b["actuator"] == "humidifier"]


def published(h, metric):
    return [m["payload"] for m in h.mqtt.messages if m["payload"]["metricType"] == metric]


def test_http_low_turns_humidifier_on_before_mqtt_and_off_at_threshold(harness):
    harness.heartbeat("node-2")
    for _ in range(3):
        post(harness, 35)
    assert humidifier_commands(harness) == ["on"]
    post(harness, 56)
    assert humidifier_commands(harness) == ["on", "off"]
    harness.tick()
    # la orden a la ESP32 ocurre antes de publicar por MQTT: nada publicado hasta el tick
    assert [p["value"] for p in published(harness, "humidity")][:2] == [35, 56]


def test_http_readings_are_not_published_one_by_one_only_on_change_and_every_60s(harness):
    harness.heartbeat("node-2")
    post(harness, 50)
    harness.tick()                          # primera telemetria
    assert len(published(harness, "humidity")) == 1
    for _ in range(5):
        post(harness, 50, advance=5)
        harness.tick()
    assert len(published(harness, "humidity")) == 1
    post(harness, 50, advance=10)           # t=35
    harness.tick(35)                        # t=70 -> pasaron 60 s
    post(harness, 50)
    harness.tick()
    assert len(published(harness, "humidity")) == 2


def test_http_dht22_error_and_silence_publish_device_error(harness):
    harness.heartbeat("node-2")
    post(harness, error=True)
    harness.tick()
    (event,) = published(harness, "device_error")
    assert event["deviceId"] == "dev-hum-1" and event["value"] == 1 and event["unit"] == "event"
    assert humidifier_commands(harness) == ["off"]

    post(harness, 50)
    harness.tick(31)
    assert len(published(harness, "device_error")) == 2


def test_http_manual_mode_via_demo_commands_and_survives_restart(make_harness):
    h = make_harness()
    h.heartbeat("node-2")
    r = h.command("humidifier", "mode", mode="manual")
    assert r.status_code == 200 and r.get_json()["mode"] == "manual"
    assert h.client.get("/api/v1/status").get_json()["subsystems"]["humidity"]["mode"] == "manual"
    for _ in range(3):
        post(h, 35)
    assert humidifier_commands(h) == []
    for _ in range(3):
        post(h, 70)
    assert humidifier_commands(h) == ["off"]

    h2 = make_harness()                       # nueva app sobre la misma base
    assert h2.client.get("/api/v1/status").get_json()["subsystems"]["humidity"]["mode"] == "manual"
    assert h2.command("humidifier", "mode", mode="auto").get_json()["mode"] == "auto"


def test_http_invalid_mode_is_rejected(harness):
    assert harness.command("humidifier", "mode", mode="turbo").status_code == 400
    assert harness.command("valve", "mode", mode="manual").status_code == 400
