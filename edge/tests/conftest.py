from dataclasses import dataclass
from pathlib import Path

import pytest

from edge.app import create_app
from edge.config.settings import Settings
from edge.shared.db import Database
from edge.tests.fakes import FakeClock, FakeEsp32Client, FakeMqttPublisher, FakeVision

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"

DEVICES_YAML = """
- key: temperature
  node: node-3
  deviceId: "dev-temp-1"
  metricType: temperature
  unit: C
- key: intrusion
  node: node-1
  deviceId: "dev-intrusion-1"
  metricType: intrusion
  unit: event
- key: escalation
  node: edge
  deviceId: "dev-escalation-1"
  metricType: escalation
  unit: event
"""


@dataclass
class Harness:
    app: object
    client: object
    clock: FakeClock
    esp32: FakeEsp32Client
    mqtt: FakeMqttPublisher
    vision: FakeVision
    db: Database
    settings: Settings

    def tick(self, seconds=0):
        """Avanza el reloj falso y dispara un tick del edge (reglas + outbox)."""
        if seconds:
            self.clock.advance(seconds)
        self.app.extensions["edge"].tick()

    def heartbeat(self, node="node-3", ip=None, actuators=None, uptime=100):
        ip = ip or {"node-1": "10.0.0.1", "node-2": "10.0.0.2", "node-3": "10.0.0.3"}[node]
        return self.client.post(f"/api/v1/nodes/{node}/heartbeat",
                                json={"ip": ip, "actuators": actuators or {}, "uptime": uptime})

    def command(self, device_key, action, **params):
        body = {"deviceKey": device_key, "action": action}
        if params:
            body["params"] = params
        return self.client.post("/api/v1/demo/commands", json=body)

    def post_reading(self, **overrides):
        body = {"node": "node-3", "deviceKey": "temperature", "metricType": "temperature",
                "value": 26.4, "unit": "C", "measuredAt": "2026-01-01T11:59:59Z"}
        body.update(overrides)
        body = {k: v for k, v in body.items() if v is not None}
        return self.client.post("/api/v1/readings", json=body)


@pytest.fixture
def make_harness(tmp_path):
    """Fabrica de apps; varias llamadas comparten reloj, fakes y base (reinicio simulado)."""
    devices = tmp_path / "devices.yaml"
    devices.write_text(DEVICES_YAML, encoding="utf-8")
    db_path = tmp_path / "edge.db"
    shared = {"clock": FakeClock(), "esp32": FakeEsp32Client(),
              "mqtt": FakeMqttPublisher(), "vision": FakeVision()}

    def build(_profiles_dir=None, _vision=None, **env):
        """`_vision` reemplaza la vision falsa compartida (p. ej. una CameraVision con fuente falsa)."""
        values = {"SPACE_ID": "7", "THRESHOLD_PROFILE": "demo"}
        values.update(env)
        settings = Settings.from_env(
            values, profiles_dir=_profiles_dir or CONFIG_DIR / "profiles",
            devices_path=devices, evidence_dir=tmp_path / "evidence", db_url=f"sqlite:///{db_path.as_posix()}")
        db = Database(settings.db_url)
        vision = _vision if _vision is not None else shared["vision"]
        app = create_app(settings, clock=shared["clock"], esp32=shared["esp32"],
                         publisher=shared["mqtt"], vision=vision, db=db)
        return Harness(app, app.test_client(), shared["clock"], shared["esp32"],
                       shared["mqtt"], vision, db, settings)

    return build


@pytest.fixture
def harness(make_harness):
    return make_harness()
