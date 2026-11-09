"""Reglas de seguridad (Nodo 1): MONITORING -> INTRUSION -> ALARM -> MONITORING.

Pura y determinista; todas las ventanas usan el reloj monotonico.

- MONITORING: persona desconocida y sonido sospechoso a <= 10 s (cualquier orden) abren una
  intrusion: sirena encendida, cerradura bloqueada y evento `intrusion` (#8).
- INTRUSION: la ventana de validacion esta abierta (`opened_at` + `validation_window_seconds`).
  No se dispara otra intrusion. Confirmar o vencer la ventana publica `escalation` y pasa a
  ALARM; rechazar apaga la sirena y vuelve a MONITORING sin escalar.
- ALARM: intrusion escalada con la sirena sonando. La sirena se apaga sola al cumplir
  `siren_max_seconds` desde que se encendio (al abrir la intrusion) y el estado vuelve a
  MONITORING. Si al escalar ya se cumplio la duracion maxima, se apaga en el mismo paso.

Ninguna transicion envia `unlock`: la cerradura sigue bloqueada (ADR 0003).

Entradas:
- kind="security": extra con `last_unknown_person_at` y `last_suspicious_sound_at` (marcas
  monotonicas o None) y `event_id` (id nuevo por si se abre una intrusion). Las marcas ya
  usadas por una intrusion se guardan como consumidas: no la vuelven a disparar.
- kind="validation": extra con `decision` ("confirm" | "reject") y `event_id` opcional; si viene
  y no es el de la intrusion abierta, se ignora.
- kind="tick": vence la ventana y la duracion maxima de la sirena.
- kind="restart": al arrancar. El reloj monotonico del proceso anterior no sirve, asi que no se
  retoma ninguna ventana: se apaga la sirena si estaba sonando y se vuelve a MONITORING. El
  escalamiento de los eventos abiertos lo publica el servicio a partir de `security_events`.
"""
from edge.rules_engine import Action, Event

NAME = "security"
MONITORING = "MONITORING"
INTRUSION = "INTRUSION"
ALARM = "ALARM"


class SecurityRules:
    name = NAME
    metric_types = ()  # no consume lecturas: recibe entradas "security", "validation", "tick"
    # Incluye los del Person Detection Service, que lee la misma seccion del perfil.
    threshold_keys = ("correlation_window_seconds", "validation_window_seconds",
                      "siren_max_seconds", "unknown_person_cooldown_seconds",
                      "person_confidence", "face_match_confidence")

    def initial_state(self) -> dict:
        return {"state": MONITORING, "event_id": None, "opened_at": None,
                "consumed_person_at": None, "consumed_sound_at": None}

    def reset_actions(self, thresholds) -> list:
        """Demo: sirena apagada. La cerradura no se toca (ADR 0003)."""
        return [Action("siren", "off")]

    def evaluate(self, input, state, clock):
        state = dict(state)
        handler = {"security": self._correlate, "validation": self._validate,
                   "tick": self._tick, "restart": self._restart}.get(input.kind)
        if handler is None:
            return state, [], []
        return handler(input, state, clock)

    # ---- MONITORING -> INTRUSION ----

    def _correlate(self, input, state, clock):
        if state["state"] != MONITORING:
            return state, [], []  # con la ventana abierta (o la alarma sonando) no hay otra
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

    # ---- respuesta del usuario ----

    def _validate(self, input, state, clock):
        if state["state"] != INTRUSION:
            return state, [], []
        event_id = input.extra.get("event_id")
        if event_id is not None and event_id != state["event_id"]:
            return state, [], []
        decision = input.extra.get("decision")
        if decision == "confirm":
            return self._escalate(input, state, clock, "confirmed")
        if decision == "reject":
            return self._end_alarm(state), [Action("siren", "off")], []
        return state, [], []

    # ---- tiempo ----

    def _tick(self, input, state, clock):
        if state["state"] == INTRUSION:
            window = input.thresholds["security"]["validation_window_seconds"]
            if clock.monotonic() - state["opened_at"] >= window:
                return self._escalate(input, state, clock, "timeout")
        elif state["state"] == ALARM:
            return self._siren_expiry(input, state, clock)
        return state, [], []

    def _escalate(self, input, state, clock, reason):
        """INTRUSION -> ALARM con el escalamiento; si la sirena ya cumplio su maximo, termina."""
        event_id = state["event_id"]
        state.update(state=ALARM)
        state, actions, _ = self._siren_expiry(input, state, clock)
        events = [Event("escalation", "escalation", {"eventId": event_id, "reason": reason,
                                                     "localState": state["state"]})]
        return state, actions, events

    def _siren_expiry(self, input, state, clock):
        """ALARM: la sirena se apaga al cumplir su duracion maxima desde que se encendio."""
        siren_max = input.thresholds["security"]["siren_max_seconds"]
        if clock.monotonic() - state["opened_at"] < siren_max:
            return state, [], []
        return self._end_alarm(state), [Action("siren", "off")], []

    # ---- arranque ----

    def _restart(self, input, state, clock):
        if state["state"] == MONITORING:
            return state, [], []
        return self._end_alarm(state), [Action("siren", "off")], []

    @staticmethod
    def _end_alarm(state):
        """Vuelta a MONITORING. La cerradura no se toca: sigue bloqueada (ADR 0003)."""
        state.update(state=MONITORING, event_id=None, opened_at=None)
        return state


MODULE = SecurityRules()
