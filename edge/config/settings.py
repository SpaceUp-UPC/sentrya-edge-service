"""Configuracion desde variables de entorno (.env, AGENTS.md seccion 11)."""
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

PACKAGE_DIR = Path(__file__).resolve().parents[1]


def _bool(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    space_id: str = "1"
    threshold_profile: str = "demo"
    flask_host: str = "0.0.0.0"
    flask_port: int = 5000
    mqtt_broker_url: str = "tcp://localhost:1883"
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_tls: bool = False
    camera_stream_url: str = ""
    person_detector: str = "mock"
    face_recognizer: str = "mock"
    sound_triggers_required: int = 1
    sound_window_seconds: float = 3
    lock_energize_to_unlock: bool = True
    lock_pulse_seconds: float = 3
    enable_simulation: bool = True
    retention_days: int = 7
    evidence_dir: Path = PACKAGE_DIR / "data" / "evidence"
    profiles_dir: Path = PACKAGE_DIR / "config" / "profiles"
    devices_path: Path = PACKAGE_DIR / "config" / "devices.yaml"
    db_url: str = f"sqlite:///{(PACKAGE_DIR / 'data' / 'edge.db').as_posix()}"

    @classmethod
    def from_env(cls, env: Mapping[str, str], **overrides) -> "Settings":
        def get(name, default):
            value = env.get(name)
            return default if value in (None, "") else value

        d = cls()
        values = dict(
            space_id=str(get("SPACE_ID", d.space_id)),
            threshold_profile=get("THRESHOLD_PROFILE", d.threshold_profile),
            flask_host=get("FLASK_HOST", d.flask_host),
            flask_port=int(get("FLASK_PORT", d.flask_port)),
            mqtt_broker_url=get("MQTT_BROKER_URL", d.mqtt_broker_url),
            mqtt_username=get("MQTT_USERNAME", ""),
            mqtt_password=get("MQTT_PASSWORD", ""),
            mqtt_tls=_bool(get("MQTT_TLS", "false")),
            camera_stream_url=get("CAMERA_STREAM_URL", ""),
            person_detector=get("PERSON_DETECTOR", d.person_detector),
            face_recognizer=get("FACE_RECOGNIZER", d.face_recognizer),
            sound_triggers_required=int(get("SOUND_TRIGGERS_REQUIRED", d.sound_triggers_required)),
            sound_window_seconds=float(get("SOUND_WINDOW_SECONDS", d.sound_window_seconds)),
            lock_energize_to_unlock=_bool(get("LOCK_ENERGIZE_TO_UNLOCK", "true")),
            lock_pulse_seconds=float(get("LOCK_PULSE_SECONDS", d.lock_pulse_seconds)),
            enable_simulation=_bool(get("ENABLE_SIMULATION", "true")),
            retention_days=int(get("RETENTION_DAYS", d.retention_days)),
            evidence_dir=Path(get("EVIDENCE_DIR", d.evidence_dir)),
        )
        values.update(overrides)
        return cls(**values)
