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


def read_active(db) -> tuple[str, dict]:
    with db.session() as session:
        row = session.get(ThresholdsRow, ACTIVE_ID)
        return row.profile, json.loads(row.params_json)
