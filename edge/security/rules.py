"""Reglas de seguridad (Nodo 1): maquina de estados MONITORING -> INTRUSION.

Pura y determinista. Este ticket (#8) cubre la correlacion y la reaccion local inmediata.
El ticket #9 extiende el estado INTRUSION (que representa la ventana de validacion abierta):
ventana de 30 s, validacion del usuario, escalamiento, duracion maxima de la sirena y
retorno a MONITORING. Para ello el estado guarda `event_id` y `opened_at` (reloj monotonico).

Entrada (kind="security"): extra con `last_unknown_person_at` y `last_suspicious_sound_at`
(marcas monotonicas o None) y `event_id` (id nuevo por si se abre una intrusion).
Las marcas ya usadas por una intrusion se guardan como consumidas: no la vuelven a disparar.
"""
from edge.rules_engine import Action, Event

NAME = "security"
MONITORING = "MONITORING"
INTRUSION = "INTRUSION"


class SecurityRules:
    name = NAME
    metric_types = ()  # no consume lecturas: recibe entradas "security" y "tick"

    def initial_state(self) -> dict:
        return {"state": MONITORING, "event_id": None, "opened_at": None,
                "consumed_person_at": None, "consumed_sound_at": None}

    def evaluate(self, input, state, clock):
        state = dict(state)
        if input.kind != "security" or state["state"] != MONITORING:
            return state, [], []
        person = input.extra.get("last_unknown_person_at")
        sound = input.extra.get("last_suspicious_sound_at")
        if person is None or sound is None:
            return state, [], []
        if person == state.get("consumed_person_at") or sound == state.get("consumed_sound_at"):
            return state, [], []  # al menos una marca ya genero una intrusion
        window = input.thresholds["security"]["correlation_window_seconds"]
        if abs(person - sound) > window:
            return state, [], []
        event_id = input.extra["event_id"]
        state.update(state=INTRUSION, event_id=event_id, opened_at=clock.monotonic(),
                     consumed_person_at=person, consumed_sound_at=sound)
        actions = [Action("siren", "on"), Action("lock", "lock")]
        events = [Event("intrusion", "intrusion", {"eventId": event_id})]
        return state, actions, events


MODULE = SecurityRules()
