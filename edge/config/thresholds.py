"""Umbrales: el YAML del perfil activo manda en cada arranque; SQLite guarda la copia vigente."""
import json
from pathlib import Path

import yaml

from edge.shared.clock import iso_utc
from edge.shared.models import ThresholdsRow

ACTIVE_ID = "active"


def load_profile(profiles_dir: Path, name: str) -> dict:
    path = Path(profiles_dir) / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"No existe el perfil de umbrales '{name}' ({path})")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def apply_profile(db, profiles_dir: Path, name: str, clock) -> dict:
    """Lee el YAML y sobrescribe la copia en SQLite. Devuelve los umbrales vigentes."""
    params = load_profile(profiles_dir, name)
    with db.session() as session:
        row = session.get(ThresholdsRow, ACTIVE_ID) or ThresholdsRow(id=ACTIVE_ID)
        row.profile = name
        row.params_json = json.dumps(params)
        row.updated_at = iso_utc(clock.now_utc())
        session.add(row)
    return params


def _merge(current: dict, incoming: dict, path: str = "") -> None:
    """Valida `incoming` contra la forma de `current` (solo claves conocidas, mismo tipo)."""
    for key, value in incoming.items():
        where = f"{path}{key}"
        if key not in current:
            raise ValueError(f"umbral desconocido: {where}")
        if isinstance(current[key], dict):
            if not isinstance(value, dict):
                raise ValueError(f"{where} debe ser un objeto")
            _merge(current[key], value, where + ".")
        elif isinstance(current[key], bool):
            if not isinstance(value, bool):
                raise ValueError(f"{where} debe ser booleano")
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{where} debe ser numerico")


def _assign(current: dict, incoming: dict) -> None:
    for key, value in incoming.items():
        if isinstance(value, dict):
            _assign(current[key], value)
        else:
            current[key] = value


def apply_cloud(db, thresholds: dict, payload, clock) -> dict:
    """Aplica umbrales de la nube sobre el dict vigente (en sitio, lo comparten las reglas) y los
    guarda en SQLite. Todo o nada; el YAML vuelve a mandar en el proximo arranque."""
    if isinstance(payload, dict) and isinstance(payload.get("thresholds"), dict):
        payload = payload["thresholds"]
    if not isinstance(payload, dict) or not payload:
        raise ValueError("la config debe ser un objeto con umbrales")
    _merge(thresholds, payload)
    _assign(thresholds, payload)
    with db.session() as session:
        row = session.get(ThresholdsRow, ACTIVE_ID) or ThresholdsRow(id=ACTIVE_ID, profile="cloud")
        row.params_json = json.dumps(thresholds)
        row.updated_at = iso_utc(clock.now_utc())
        session.add(row)
    return thresholds


def read_active(db) -> tuple[str, dict]:
    with db.session() as session:
        row = session.get(ThresholdsRow, ACTIVE_ID)
        return row.profile, json.loads(row.params_json)
