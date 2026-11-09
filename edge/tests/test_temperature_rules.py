"""Seam 2: evaluate(input, state, clock) del modulo de temperatura."""
from edge.rules_engine import RuleInput
from edge.temperature.rules import MODULE
from edge.tests.fakes import FakeClock

THRESHOLDS = {"temperature": {"t1_warning": 30, "t2_critical": 40, "hysteresis": 2}}


def reading(value):
    return RuleInput(kind="reading", thresholds=THRESHOLDS, device_key="temperature",
                     metric_type="temperature", value=value)


def test_reading_below_t1_keeps_normal_without_actions_or_events():
    state = MODULE.initial_state()
    new_state, actions, events = MODULE.evaluate(reading(25.0), state, FakeClock())
    assert new_state["state"] == "NORMAL"
    assert actions == [] and events == []


def test_tick_does_not_change_state():
    state = MODULE.initial_state()
    tick = RuleInput(kind="tick", thresholds=THRESHOLDS)
    assert MODULE.evaluate(tick, state, FakeClock()) == (state, [], [])
