"""Modulo de reglas de temperatura (Nodo 3). Maquina de estados de AGENTS.md seccion 7.

NORMAL -> WARNING con una lectura >= T1.
WARNING -> CRITICAL con N lecturas consecutivas >= T2 (o, opcional, un aumento rapido).
CRITICAL -> WARNING solo bajo T2 - histeresis; WARNING -> NORMAL solo bajo T1 - histeresis.
Las acciones y el escalamiento se emiten solo al cambiar de estado.
"""
from edge.rules_engine import Action, Event

NAME = "temperature"

NORMAL, WARNING, CRITICAL = "NORMAL", "WARNING", "CRITICAL"
RATE_WINDOW_SECONDS = 60.0



class TemperatureRules:
    name = NAME
    metric_types = ("temperature",)
    threshold_keys = ("t1_warning", "t2_critical", "hysteresis", "consecutive_readings",
                      "rate_rise_enabled", "rate_rise_c_per_min", "sampling_seconds.normal",
                      "sampling_seconds.warning", "sampling_seconds.critical",
                      "sensor_min_interval_seconds")

    def initial_state(self) -> dict:
        return {"state": NORMAL, "consecutive": 0, "samples": []}

    def evaluate(self, input, state, clock):
        if input.kind != "reading" or input.metric_type != "temperature" or input.value is None:
            return dict(state), [], []
        params = input.thresholds[NAME]   # todo sale del perfil (AGENTS.md §6)
        value = float(input.value)
        previous = state.get("state", NORMAL)

        consecutive = state.get("consecutive", 0) + 1 if value >= params["t2_critical"] else 0
        samples, rate_hit = self._track_rate(state, value, clock, params)
        current = self._next_state(previous, value, consecutive, rate_hit, params)

        new_state = {"state": current, "consecutive": consecutive, "samples": samples}
        if current == previous:
            return new_state, [], []
        events = []
        if current == CRITICAL:  # una sola vez por entrada: solo en la transicion
            reason = ("temperature_critical" if consecutive >= params["consecutive_readings"]
                      else "temperature_rate_rise")
            events.append(Event("escalation", "escalation",
                                {"reason": reason, "temperature": value}))
        return new_state, self._actions(previous, current, params), events

    @staticmethod
    def _next_state(previous, value, consecutive, rate_hit, params):
        t1, t2, hysteresis = params["t1_warning"], params["t2_critical"], params["hysteresis"]
        if previous == CRITICAL:
            if rate_hit or value >= t2 - hysteresis:
                return CRITICAL
            return WARNING
        if consecutive >= params["consecutive_readings"] or rate_hit:
            return CRITICAL
        if previous == NORMAL:
            return WARNING if value >= t1 else NORMAL
        return NORMAL if value < t1 - hysteresis else WARNING

    @staticmethod
    def _track_rate(state, value, clock, params):
        """Conserva la ventana de 60 s; devuelve (muestras, aumento >= umbral en la ventana)."""
        if not params["rate_rise_enabled"]:
            return [], False
        now = clock.monotonic()
        samples = [s for s in state.get("samples", []) if now - s[0] <= RATE_WINDOW_SECONDS]
        hit = bool(samples) and value - min(s[1] for s in samples) >= params["rate_rise_c_per_min"]
        samples.append([now, value])
        return samples, hit

    @staticmethod
    def _actions(previous, current, params):
        sampling = params["sampling_seconds"]
        interval = max(sampling[current.lower()], params["sensor_min_interval_seconds"])
        actions = []
        if current == CRITICAL:
            actions += [Action("fan", "set", {"duty": 100}), Action("buzzer", "on")]
        elif current == WARNING:
            actions.append(Action("fan", "set", {"duty": 50}))
            if previous == CRITICAL:
                actions.append(Action("buzzer", "off"))
        else:
            actions += [Action("fan", "set", {"duty": 0}), Action("buzzer", "off")]
        actions.append(Action("sampling", "set", {"intervalSeconds": interval}))
        return actions


MODULE = TemperatureRules()
