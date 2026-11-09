"""Modulo de reglas de humedad (Nodo 2), AGENTS.md seccion 7.

Estados: NORMAL | LOW | HIGH. El sensor caido no es un estado: es la bandera `sensor_down`
(humidificador apagado + evento device_error). El modo AUTO/MANUAL vive en el estado
(se guarda en subsystem_state y sobrevive a un reinicio).

La humedad no se publica lectura a lectura: se publica cada `telemetry_seconds` con la ultima
lectura y de inmediato en cada cambio de estado (ver `publish_readings = False`).

El sensor caido y la telemetria se miden con el reloj monotonico (`last_reading_at` y
`last_telemetry_at` son marcas monotonicas): un ajuste de la hora UTC no los altera.
"""
from edge.rules_engine import Action, Event

NAME = "humidity"
DEVICE_KEY = "humidity"
ACTUATOR = "humidifier"
MODES = ("auto", "manual")
NORMAL, LOW, HIGH = "NORMAL", "LOW", "HIGH"


def _monotonic_now(clock, state) -> float:
    """Marca monotonica actual. Si las marcas guardadas son de un reloj monotonico anterior
    (reinicio del equipo: el reloj volvio a empezar), se descartan y se toma una linea base."""
    now = clock.monotonic()
    for key in ("last_reading_at", "last_telemetry_at"):
        if state[key] is not None and state[key] > now:
            state[key] = None
    return now


class HumidityRules:
    name = NAME
    metric_types = ("humidity",)
    threshold_keys = ("on_below", "off_at", "high_above", "high_hysteresis",
                      "consecutive_readings", "read_interval_seconds", "telemetry_seconds")
    publish_readings = False   # el nucleo no encola cada lectura; la telemetria la emite el modulo
    mode_device_key = ACTUATOR   # /demo/commands {"deviceKey": "humidifier", "action": "mode"}

    def initial_state(self) -> dict:
        return {"state": NORMAL, "mode": "auto", "humidifier": "off", "sensor_down": False,
                "low_count": 0, "high_count": 0, "exit_high_count": 0,
                "last_value": None, "last_reading_at": None, "last_telemetry_at": None}

    # ---- logs ----

    def describe(self, state) -> str:
        return f"{state['state']} (sensor caido)" if state.get("sensor_down") else state["state"]

    def reason(self, input, old_state, new_state):
        if new_state.get("sensor_down") and not old_state.get("sensor_down"):
            if input.kind == "tick":
                interval = input.thresholds[NAME]["read_interval_seconds"]
                return f"sin lecturas del DHT22 durante mas de 3 x {interval} s"
            return "el DHT22 reporto error"
        if old_state.get("sensor_down") and not new_state.get("sensor_down"):
            return f"el sensor volvio: lectura {input.device_key}={input.value}"
        return None

    def reset_actions(self, thresholds) -> list:
        """Demo: humidificador apagado (el modo AUTO/MANUAL se conserva)."""
        return [Action(ACTUATOR, "off")]

    def set_mode(self, state: dict, mode: str) -> dict:
        if mode not in MODES:
            raise ValueError(f"modo invalido: {mode}")
        return {**state, "mode": mode}

    # ---- interfaz del Rules Engine ----

    def evaluate(self, input, state, clock):
        cfg = input.thresholds["humidity"]
        state = {**self.initial_state(), **state}
        if input.kind == "reading":
            return self._on_reading(cfg, state, input, clock)
        return self._on_tick(cfg, state, clock)

    # ---- lectura ----

    def _on_reading(self, cfg, state, input, clock):
        now = _monotonic_now(clock, state)
        actions, events = [], []
        if input.extra.get("error") or input.value is None:   # el DHT22 devolvio error
            self._sensor_down(state, actions, events)
            return state, actions, events

        value = input.value
        state["last_value"], state["last_reading_at"] = value, now
        state["sensor_down"] = False
        before = state["state"]
        manual = state["mode"] == "manual"
        needed = cfg["consecutive_readings"]

        state["low_count"] = state["low_count"] + 1 if value < cfg["on_below"] else 0
        if state["state"] != HIGH:
            state["high_count"] = state["high_count"] + 1 if value > cfg["high_above"] else 0

        if state["state"] == HIGH:
            exit_below = cfg["high_above"] - cfg["high_hysteresis"]
            state["exit_high_count"] = state["exit_high_count"] + 1 if value < exit_below else 0
            if state["exit_high_count"] >= needed:
                state["state"], state["exit_high_count"] = NORMAL, 0
                state["low_count"] = 0
        else:
            if state["state"] == LOW and value >= cfg["off_at"]:
                state["state"] = NORMAL
                if state["humidifier"] == "on" and not manual:
                    self._humidifier(state, actions, "off")
            if state["high_count"] >= needed:
                state["state"], state["high_count"] = HIGH, 0
                state["exit_high_count"] = 0
                self._humidifier(state, actions, "off", force=True)   # tambien en MANUAL
            elif state["state"] == NORMAL and state["low_count"] >= needed:
                state["state"] = LOW
                if not manual:
                    self._humidifier(state, actions, "on")
            elif state["state"] == LOW and not manual and state["humidifier"] == "off":
                # Sigue en LOW pero apagado (el sensor cayo y se recupero): AUTO lo vuelve a
                # encender con la lectura valida (historia 15).
                self._humidifier(state, actions, "on")

        if state["state"] != before:
            events.append(self._telemetry(state, now))   # cambio de estado: publicacion inmediata
        return state, actions, events

    # ---- tick ----

    def _on_tick(self, cfg, state, clock):
        now = _monotonic_now(clock, state)
        actions, events = [], []
        if state["last_reading_at"] is None:
            state["last_reading_at"] = now   # linea base: el silencio se mide desde el primer tick
        elif not state["sensor_down"] and \
                now - state["last_reading_at"] > 3 * cfg["read_interval_seconds"]:
            self._sensor_down(state, actions, events)
        if not state["sensor_down"] and state["last_value"] is not None and (
                state["last_telemetry_at"] is None
                or now - state["last_telemetry_at"] >= cfg["telemetry_seconds"]):
            events.append(self._telemetry(state, now))
        return state, actions, events

    # ---- auxiliares ----

    def _sensor_down(self, state, actions, events):
        if state["sensor_down"]:
            return
        state["sensor_down"] = True
        state["low_count"] = state["high_count"] = state["exit_high_count"] = 0
        self._humidifier(state, actions, "off", force=True)
        events.append(Event("device_error", DEVICE_KEY,
                            {"value": 1, "unit": "event", "reason": "sensor_down"}))

    def _humidifier(self, state, actions, action, force=False):
        if force or state["humidifier"] != action:
            actions.append(Action(ACTUATOR, action))
        state["humidifier"] = action

    def _telemetry(self, state, now):
        state["last_telemetry_at"] = now
        return Event("humidity", DEVICE_KEY,
                     {"value": state["last_value"], "unit": "%", "state": state["state"]})


MODULE = HumidityRules()
