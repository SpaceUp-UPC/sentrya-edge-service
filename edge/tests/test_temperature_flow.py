"""Seam 1: temperatura por HTTP con ESP32 y MQTT falsos (perfil demo: T1=28, T2=32, hist=1)."""


def sent(h, actuator):
    return [(b["action"], b["params"]) for _, b in h.esp32.sent if b["actuator"] == actuator]


def test_critical_flow_commands_esp32_and_publishes_escalation_once(harness):
    h = harness
    h.heartbeat("node-3")
    for _ in range(3):
        assert h.post_reading(value=33.0).status_code < 300
    assert sent(h, "fan") == [("set", {"duty": 50}), ("set", {"duty": 100})]
    assert sent(h, "buzzer") == [("on", {})]
    assert sent(h, "sampling") == [("set", {"intervalSeconds": 5}), ("set", {"intervalSeconds": 1})]
    h.post_reading(value=34.0)
    h.tick()
    escalations = [m["payload"] for m in h.mqtt.messages if m["payload"]["metricType"] == "escalation"]
    assert len(escalations) == 1
    assert escalations[0]["reason"] == "temperature_critical"
    assert escalations[0]["unit"] == "event" and escalations[0]["value"] == 1


def test_recovery_turns_fan_and_buzzer_off_and_slows_sampling(harness):
    h = harness
    h.heartbeat("node-3")
    for value in (33, 33, 33, 30, 25):  # 30 < T2-1 -> WARNING; 25 < T1-1 -> NORMAL
        h.post_reading(value=value)
    assert sent(h, "fan")[-2:] == [("set", {"duty": 50}), ("set", {"duty": 0})]
    assert sent(h, "buzzer")[-1] == ("off", {})
    assert sent(h, "sampling")[-1] == ("set", {"intervalSeconds": 30})


def test_in_critical_esp32_is_commanded_before_mqtt_publication(harness):
    h = harness
    h.heartbeat("node-3")
    calls = []
    real_send, real_publish = h.esp32.send_command, h.mqtt.publish

    def send(ip, body):
        calls.append("esp32")
        return real_send(ip, body)

    def publish(*args, **kwargs):
        calls.append("mqtt")
        return real_publish(*args, **kwargs)

    h.esp32.send_command, h.mqtt.publish = send, publish
    for _ in range(3):
        h.post_reading(value=33.0)
    h.tick()
    assert "mqtt" in calls and "esp32" in calls
    assert len(calls) - 1 - calls[::-1].index("esp32") < calls.index("mqtt")
