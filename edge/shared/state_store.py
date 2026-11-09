"""Persistencia del estado de cada subsistema (tabla subsystem_state).

El estado de un modulo de reglas es un dict JSON con la clave "state"; el resto va a counters_json.
"""
import json

from edge.shared.models import SubsystemStateRow


def load_state(session, subsystem: str):
    """Devuelve (estado dict, updated_at) o (None, None) si nunca se guardo."""
    row = session.get(SubsystemStateRow, subsystem)
    if row is None:
        return None, None
    state = json.loads(row.counters_json)
    state["state"] = row.state
    return state, row.updated_at


def save_state(session, subsystem: str, state: dict, updated_at: str) -> None:
    rest = {k: v for k, v in state.items() if k != "state"}
    row = session.get(SubsystemStateRow, subsystem)
    if row is None:
        row = SubsystemStateRow(subsystem=subsystem)
        session.add(row)
    row.state = state["state"]
    row.counters_json = json.dumps(rest)
    row.updated_at = updated_at
