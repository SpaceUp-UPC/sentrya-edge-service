"""Carga de config/devices.yaml: deviceKey logico -> deviceId de IoT Monitoring."""
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Device:
    key: str
    node: str
    device_id: str
    metric_type: str
    unit: str


def load_devices(path: Path) -> dict[str, Device]:
    entries = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    devices = {}
    for entry in entries:
        devices[entry["key"]] = Device(
            key=entry["key"], node=entry["node"], device_id=str(entry["deviceId"]),
            metric_type=entry["metricType"], unit=entry["unit"])
    return devices
