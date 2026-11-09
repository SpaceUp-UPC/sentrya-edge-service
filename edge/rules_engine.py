"""Rules Engine: nucleo que itera sobre los modulos de reglas registrados.

Cada modulo expone la misma interfaz pura:
    evaluate(input, state, clock) -> (new_state, actions[], events[])
Agregar un subsistema es registrar un modulo; el nucleo no cambia.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from edge.shared import state_store
from edge.shared.clock import iso_utc
from edge.shared.logging import get_logger

log = get_logger("rules")


@dataclass(frozen=True)
class RuleInput:
    """Entrada a un modulo. kind: 'reading' | 'tick' (los tickets siguientes agregan mas)."""
    kind: str
    thresholds: dict
    device_key: str | None = None
    metric_type: str | None = None
    value: float | None = None
    extra: dict = field(default_factory=dict)  # sonido, persona, validacion, etc.


@dataclass(frozen=True)
class Action:
    """Orden a un actuador, para el Local Safety Controller."""
    actuator: str
    action: str
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Event:
    """Evento publicable (se publica como 'lectura evento', ADR 0001)."""
    metric_type: str          # intrusion | escalation | device_error
    device_key: str
    data: dict = field(default_factory=dict)  # reason, evidenceRef, ...


class RuleModule(Protocol):
    name: str
    metric_types: tuple
    threshold_keys: tuple   # claves que exige en thresholds[name] ("a.b" = anidada)
    def initial_state(self) -> dict: ...
    def evaluate(self, input: RuleInput, state: dict, clock) -> tuple[dict, list, list]: ...


@dataclass
class Evaluation:
    subsystem: str
    state: dict
    actions: list
    events: list
    publish_reading: bool = True  # False: el modulo publica por su cuenta (p. ej. humedad)


def check_thresholds(modules, thresholds: dict) -> None:
    """Al arrancar: el perfil debe traer todos los umbrales de cada modulo (no hay respaldo
    en el codigo, AGENTS.md §6). Lanza ValueError con las claves que faltan."""
    missing = []
    for module in modules:
        for key in getattr(module, "threshold_keys", ()):
            node = thresholds.get(module.name) if isinstance(thresholds, dict) else None
            for part in key.split("."):
                node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                missing.append(f"{module.name}.{key}")
    if missing:
        raise ValueError("El perfil de umbrales no define: " + ", ".join(missing))


class RulesEngine:
    def __init__(self, modules: list, clock, thresholds: dict):
        self.modules = {m.name: m for m in modules}
        self.clock = clock
        self.thresholds = thresholds
        check_thresholds(self.modules.values(), thresholds)

    def _run(self, session, module, rule_input) -> Evaluation:
        state, _ = state_store.load_state(session, module.name)
        state = state if state is not None else module.initial_state()
        new_state, actions, events = module.evaluate(rule_input, state, self.clock)
        if new_state["state"] != state["state"]:
            log.info("[%s] %s -> %s (motivo: %s)", module.name, state["state"],
                     new_state["state"], self._reason(rule_input))
        state_store.save_state(session, module.name, new_state, iso_utc(self.clock.now_utc()))
        return Evaluation(module.name, new_state, list(actions), list(events),
                          getattr(module, "publish_readings", True))

    @staticmethod
    def _reason(rule_input) -> str:
        if rule_input.kind == "reading":
            return f"lectura {rule_input.device_key}={rule_input.value}"
        return f"entrada {rule_input.kind}"

    def reset(self, session) -> None:
        """Demo: todos los modulos vuelven a su estado inicial (modo AUTO incluido)."""
        for module in self.modules.values():
            state, _ = state_store.load_state(session, module.name)
            initial = module.initial_state()
            if state is not None and state["state"] != initial["state"]:
                log.info("[%s] %s -> %s (motivo: reinicio de demo)", module.name,
                         state["state"], initial["state"])
            state_store.save_state(session, module.name, initial, iso_utc(self.clock.now_utc()))

    def handle_reading(self, session, device_key, metric_type, value, extra=None) -> list[Evaluation]:
        rule_input = RuleInput("reading", self.thresholds, device_key, metric_type, value,
                               extra or {})
        return [self._run(session, m, rule_input)
                for m in self.modules.values() if metric_type in m.metric_types]

    def handle_input(self, session, subsystem, kind, extra) -> Evaluation:
        """Entrada no basada en lecturas (sonido, persona, validacion) para un modulo concreto."""
        rule_input = RuleInput(kind, self.thresholds, extra=extra)
        return self._run(session, self.modules[subsystem], rule_input)

    def tick(self, session) -> list[Evaluation]:
        rule_input = RuleInput("tick", self.thresholds)
        return [self._run(session, m, rule_input) for m in self.modules.values()]

    def set_mode(self, session, device_key, mode) -> bool:
        """Cambia el modo AUTO/MANUAL del modulo que lo soporta para ese dispositivo."""
        for module in self.modules.values():
            if getattr(module, "mode_device_key", None) == device_key:
                state, _ = state_store.load_state(session, module.name)
                state = module.set_mode(state if state is not None else module.initial_state(), mode)
                state_store.save_state(session, module.name, state, iso_utc(self.clock.now_utc()))
                return True
        return False

    def snapshot(self, session) -> dict:
        """Estado actual de cada modulo para /status."""
        out = {}
        for name, module in self.modules.items():
            state, updated_at = state_store.load_state(session, name)
            current = state or module.initial_state()
            out[name] = {"state": current["state"], "updatedAt": updated_at}
            if "mode" in current:
                out[name]["mode"] = current["mode"]
        return out
