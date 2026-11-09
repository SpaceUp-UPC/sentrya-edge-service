import re

import pytest

from edge.tests.conftest import CONFIG_DIR


def status(h):
    return h.client.get("/api/v1/status").get_json()


def test_thresholds_come_from_the_profile_named_in_env(make_harness):
    assert status(make_harness(THRESHOLD_PROFILE="report"))["thresholds"]["temperature"]["t1_warning"] == 45
    demo = status(make_harness(THRESHOLD_PROFILE="demo"))
    assert demo["profile"] == "demo"
    assert demo["thresholds"]["temperature"]["t1_warning"] == 28


DEMO_YAML = (CONFIG_DIR / "profiles" / "demo.yaml").read_text(encoding="utf-8")


def write_demo(profiles, text):
    profiles.mkdir(exist_ok=True)
    (profiles / "demo.yaml").write_text(text, encoding="utf-8")


def test_profile_yaml_wins_on_every_start(make_harness, tmp_path):
    profiles = tmp_path / "profiles"
    write_demo(profiles, DEMO_YAML.replace("t1_warning: 28", "t1_warning: 99"))
    assert status(make_harness(_profiles_dir=profiles))["thresholds"]["temperature"]["t1_warning"] == 99

    write_demo(profiles, DEMO_YAML.replace("t1_warning: 28", "t1_warning: 31"))
    assert status(make_harness(_profiles_dir=profiles))["thresholds"]["temperature"]["t1_warning"] == 31


@pytest.mark.parametrize("line, key", [
    ("  consecutive_readings: 3\n  rate_rise_enabled", "temperature.consecutive_readings"),
    ("  sensor_min_interval_seconds: 1\n", "temperature.sensor_min_interval_seconds"),
    ("  telemetry_seconds: 60\n", "humidity.telemetry_seconds"),
    ("  siren_max_seconds: 15\n", "security.siren_max_seconds"),
])
def test_startup_fails_clearly_when_the_profile_lacks_a_threshold(make_harness, tmp_path, line, key):
    removed = line.split("\n", 1)[0] + "\n"
    assert removed in DEMO_YAML and line in DEMO_YAML
    write_demo(tmp_path / "profiles", DEMO_YAML.replace(removed, "", 1))
    with pytest.raises(ValueError, match=re.escape(key)):
        make_harness(_profiles_dir=tmp_path / "profiles")


@pytest.mark.parametrize("profile", ["demo", "report"])
def test_both_profiles_have_every_threshold(make_harness, profile):
    assert status(make_harness(THRESHOLD_PROFILE=profile))["profile"] == profile


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
