"""Seam 1: app armada por el factory con adaptadores falsos, probada por HTTP."""
import pytest


def test_valid_reading_reaches_mqtt_with_local_state(harness):
    response = harness.post_reading(value=26.4)
    assert response.status_code == 201

    harness.tick()

    assert len(harness.mqtt.messages) == 1
    message = harness.mqtt.messages[0]
    assert message["topic"] == "sentrya/7/dev-temp-1/readings"
    assert message["qos"] == 1
    payload = message["payload"]
    assert payload["deviceId"] == "dev-temp-1"
    assert payload["metricType"] == "temperature"
    assert payload["value"] == 26.4
    assert payload["unit"] == "C"
    assert payload["measuredAt"] == "2026-01-01T11:59:59.000Z"
    assert payload["localState"] == "NORMAL"
    assert payload["readingId"]


@pytest.mark.parametrize("overrides, fragment", [
    ({"value": None}, "value"),
    ({"deviceKey": None}, "deviceKey"),
    ({"deviceKey": "nope"}, "deviceKey"),
    ({"value": "caliente"}, "value"),
    ({"metricType": "humidity"}, "metricType"),
    ({"measuredAt": "ayer"}, "measuredAt"),
])
def test_invalid_reading_is_rejected_and_not_published(harness, overrides, fragment):
    response = harness.post_reading(**overrides)
    assert response.status_code == 400
    assert fragment in response.get_json()["error"]

    harness.tick()
    assert harness.mqtt.messages == []


def test_missing_measured_at_uses_edge_utc_timestamp(harness):
    harness.clock.advance(30)
    assert harness.post_reading(measuredAt=None).status_code == 201
    harness.tick()
    assert harness.mqtt.messages[0]["payload"]["measuredAt"] == "2026-01-01T12:00:30.000Z"


def test_reading_is_kept_while_broker_is_down_and_sent_once_after(harness):
    harness.mqtt.broker_up = False
    harness.post_reading()
    harness.tick()
    assert harness.client.get("/api/v1/status").get_json()["outbox"]["pending"] == 1

    harness.mqtt.broker_up = True
    harness.tick()
    harness.tick()
    assert len(harness.mqtt.messages) == 1
    assert harness.client.get("/api/v1/status").get_json()["outbox"]["pending"] == 0
