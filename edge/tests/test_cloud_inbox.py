"""Nube -> edge por MQTT (#11): commands, security/validation y config."""
import threading

from edge.communication.mqtt_publisher import PahoPublisher
from edge.config import thresholds as thresholds_config

PREFIX = "sentrya/7"


def open_intrusion(h):
    h.heartbeat("node-1")
    h.client.post("/api/v1/simulate/security/person", json={"known": False})
    for _ in range(3):
        h.client.post("/api/v1/security/sound", json={"node": "node-1"})


def status(h):
    return h.client.get("/api/v1/status").get_json()


def cloud(h, topic, payload, **flags):
    """Mensaje de la nube: el hilo de paho solo lo encola; el siguiente tick lo atiende."""
    h.mqtt.deliver(topic, payload, **flags)
    h.tick()


def test_subscribes_to_the_three_cloud_topics(harness):
    assert set(harness.mqtt.subscriptions) == {
        f"{PREFIX}/+/commands", f"{PREFIX}/security/validation", f"{PREFIX}/config"}


def test_command_gives_the_same_esp32_order_as_the_demo_endpoint(harness):
    harness.heartbeat("node-3")
    harness.command("valve", "close")
    demo = harness.esp32.sent[-1]
    harness.esp32.sent.clear()
    cloud(harness, f"{PREFIX}/valve/commands",
                         {"commandId": "c-1", "action": "close", "params": {}})
    from_cloud = harness.esp32.sent[-1]
    assert from_cloud[0] == demo[0]
    assert {k: v for k, v in from_cloud[1].items() if k != "commandId"} == \
           {k: v for k, v in demo[1].items() if k != "commandId"}


def test_command_topic_accepts_the_iot_device_id(harness):
    cloud(harness, f"{PREFIX}/dev-hum-1/commands",
                         {"commandId": "c-1", "action": "mode", "params": {"mode": "manual"}})
    assert status(harness)["subsystems"]["humidity"]["mode"] == "manual"


def test_humidifier_mode_command_goes_through_the_rules_engine(harness):
    cloud(harness, f"{PREFIX}/humidifier/commands",
                         {"commandId": "c-1", "action": "mode", "params": {"mode": "manual"}})
    assert status(harness)["subsystems"]["humidity"]["mode"] == "manual"
    assert harness.esp32.sent == []


def test_cloud_unlock_is_allowed_like_the_demo_endpoint(harness):
    harness.heartbeat("node-1")
    cloud(harness, f"{PREFIX}/lock/commands", {"commandId": "c", "action": "unlock"})
    assert harness.esp32.sent[-1][1]["action"] == "unlock"


def test_validation_reject_has_the_same_effect_as_the_demo_endpoint(harness):
    open_intrusion(harness)
    assert status(harness)["subsystems"]["security"]["state"] == "INTRUSION"
    cloud(harness, f"{PREFIX}/security/validation", {"eventId": "x", "decision": "reject"})
    assert status(harness)["subsystems"]["security"]["state"] == "INTRUSION"   # eventId ajeno
    cloud(harness, f"{PREFIX}/security/validation", {"decision": "reject"})
    assert status(harness)["subsystems"]["security"]["state"] == "MONITORING"
    assert harness.esp32.sent[-1][1]["actuator"] == "siren"
    assert harness.esp32.sent[-1][1]["action"] == "off"


def test_validation_confirm_escalates(harness):
    open_intrusion(harness)
    cloud(harness, f"{PREFIX}/security/validation", {"decision": "confirm"})
    assert any(m["payload"].get("deviceId") == "dev-escalation-1" for m in harness.mqtt.messages)


def test_config_changes_thresholds_and_is_saved_in_sqlite(harness):
    cloud(harness, f"{PREFIX}/config", {"temperature": {"t1_warning": 30},
                                              "security": {"siren_max_seconds": 20}})
    current = status(harness)["thresholds"]
    assert current["temperature"]["t1_warning"] == 30
    assert current["temperature"]["t2_critical"] == 32   # lo demas intacto
    _, saved = thresholds_config.read_active(harness.db)
    assert saved["temperature"]["t1_warning"] == 30 and saved["security"]["siren_max_seconds"] == 20


def test_retained_config_is_ignored_because_the_yaml_rules_on_every_start(harness):
    cloud(harness, f"{PREFIX}/config", {"temperature": {"t1_warning": 30}}, retain=True)
    assert status(harness)["thresholds"]["temperature"]["t1_warning"] == 28
    _, saved = thresholds_config.read_active(harness.db)
    assert saved["temperature"]["t1_warning"] == 28


def test_paho_publisher_passes_the_retain_flag():
    stub = StubPahoClient()
    publisher = PahoPublisher("tcp://localhost:1883", client=stub)
    seen = []
    publisher.subscribe("a/config", lambda topic, payload, retained: seen.append(retained))
    publisher._on_connect(stub, None, None, 0, None)

    class Message:
        topic, payload, retain = "a/config", b"{}", True
    stub.callbacks["a/config"](stub, None, Message())
    assert seen == [True]


def test_config_applies_to_the_next_readings(harness):
    cloud(harness, f"{PREFIX}/config", {"temperature": {"t1_warning": 20}})
    harness.heartbeat("node-3")
    for _ in range(3):
        harness.post_reading(value=22)
    assert status(harness)["subsystems"]["temperature"]["state"] != "NORMAL"


def test_restart_goes_back_to_the_yaml(make_harness):
    first = make_harness()
    cloud(first, f"{PREFIX}/config", {"temperature": {"t1_warning": 30}})
    second = make_harness()
    assert status(second)["thresholds"]["temperature"]["t1_warning"] == 28


def test_malformed_messages_are_ignored(harness):
    before = status(harness)["thresholds"]
    for topic, payload in [
        (f"{PREFIX}/config", "no es json"), (f"{PREFIX}/config", b"\xff\xfe"),
        (f"{PREFIX}/config", [1, 2]), (f"{PREFIX}/config", {"temperature": {"t1_warning": "x"}}),
        (f"{PREFIX}/config", {"temperature": {"inventado": 1}}),
        (f"{PREFIX}/config", {"nada": {"t": 1}}),
        (f"{PREFIX}/valve/commands", "{"), (f"{PREFIX}/valve/commands", {"action": 3}),
        (f"{PREFIX}/valve/commands", {"action": "explotar"}),
        (f"{PREFIX}/desconocido/commands", {"action": "on"}),
        (f"{PREFIX}/humidifier/commands", {"action": "mode", "params": {"mode": "raro"}}),
        (f"{PREFIX}/security/validation", {"decision": "quizas"}),
        (f"{PREFIX}/security/validation", "[]"),
    ]:
        cloud(harness, topic, payload)   # no debe lanzar
    assert status(harness)["thresholds"] == before
    assert harness.esp32.sent == []


def test_mqtt_thread_only_queues_messages_and_never_waits_for_the_edge_lock(harness):
    edge = harness.app.extensions["edge"]
    done = threading.Event()

    def deliver():   # como el hilo de red de paho
        harness.mqtt.deliver(f"{PREFIX}/config", {"temperature": {"t1_warning": 31}})
        done.set()

    with edge.lock:
        worker = threading.Thread(target=deliver)
        worker.start()
        assert done.wait(2)                      # no espera el lock
    worker.join(2)
    assert status(harness)["thresholds"]["temperature"]["t1_warning"] == 28   # aun sin aplicar
    harness.tick()
    assert status(harness)["thresholds"]["temperature"]["t1_warning"] == 31


def test_confirm_from_the_cloud_publishes_a_single_escalation(harness):
    """En el hilo de paho no llega el PUBACK: publicar ahi daria un falso fallo y un reenvio."""
    open_intrusion(harness)
    cloud(harness, f"{PREFIX}/security/validation", {"decision": "confirm"})
    harness.tick(1)
    harness.tick(1)
    escalations = [m for m in harness.mqtt.messages
                   if m["payload"].get("deviceId") == "dev-escalation-1"]
    assert len(escalations) == 1


class StubPahoClient:
    def __init__(self):
        self.subscribed = []
        self.callbacks = {}

    def username_pw_set(self, *args):
        pass

    def reconnect_delay_set(self, **kwargs):
        pass

    def is_connected(self):
        return False

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)

    def message_callback_add(self, topic, callback):
        self.callbacks[topic] = callback


def test_paho_publisher_resubscribes_on_every_connect():
    stub = StubPahoClient()
    publisher = PahoPublisher("tcp://localhost:1883", client=stub)
    publisher.subscribe("a/+/commands", lambda t, p: None)
    publisher.subscribe("a/config", lambda t, p: None)
    publisher._on_connect(stub, None, None, 0, None)
    publisher._on_connect(stub, None, None, 0, None)   # reconexion
    assert stub.subscribed == ["a/+/commands", "a/config"] * 2
    assert set(stub.callbacks) == {"a/+/commands", "a/config"}
