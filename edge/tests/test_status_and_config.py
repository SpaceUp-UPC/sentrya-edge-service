def status(h):
    return h.client.get("/api/v1/status").get_json()


def test_thresholds_come_from_the_profile_named_in_env(make_harness):
    assert status(make_harness(THRESHOLD_PROFILE="report"))["thresholds"]["temperature"]["t1_warning"] == 45
    demo = status(make_harness(THRESHOLD_PROFILE="demo"))
    assert demo["profile"] == "demo"
    assert demo["thresholds"]["temperature"]["t1_warning"] == 28


def test_profile_yaml_wins_on_every_start(make_harness, tmp_path):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "demo.yaml").write_text("temperature:\n  t1_warning: 99\n", encoding="utf-8")
    assert status(make_harness(_profiles_dir=profiles))["thresholds"]["temperature"]["t1_warning"] == 99

    (profiles / "demo.yaml").write_text("temperature:\n  t1_warning: 31\n", encoding="utf-8")
    assert status(make_harness(_profiles_dir=profiles))["thresholds"]["temperature"]["t1_warning"] == 31


def test_status_reports_temperature_state_and_survives_restart(make_harness):
    first = make_harness()
    assert status(first)["subsystems"]["temperature"] == {"state": "NORMAL", "updatedAt": None}

    first.post_reading()
    saved = status(first)["subsystems"]["temperature"]
    assert saved["state"] == "NORMAL" and saved["updatedAt"] is not None

    second = make_harness()  # nueva app sobre la misma base
    assert status(second)["subsystems"]["temperature"] == saved


def test_status_shows_mqtt_connection(harness):
    assert status(harness)["mqtt"] == {"connected": True}
    harness.mqtt.broker_up = False
    assert status(harness)["mqtt"] == {"connected": False}
