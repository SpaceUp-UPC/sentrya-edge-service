"""Seam 2: maquina de estados completa del modulo de temperatura (#5)."""
from edge.rules_engine import RuleInput
from edge.temperature.rules import MODULE
from edge.tests.fakes import FakeClock

T = {"temperature": {"t1_warning": 30, "t2_critical": 40, "hysteresis": 2,
                     "consecutive_readings": 3, "rate_rise_enabled": False,
                     "rate_rise_c_per_min": 8,
                     "sampling_seconds": {"normal": 30, "warning": 5, "critical": 1},
                     "sensor_min_interval_seconds": 1}}


def feed(values, thresholds=None, clock=None, state=None):
    """Alimenta lecturas; devuelve (estado final, lista de (acciones, eventos) por lectura)."""
    state = state or MODULE.initial_state()
    clock = clock or FakeClock()
    trace = []
    for value in values:
        rule_input = RuleInput(kind="reading", thresholds=thresholds or T, device_key="temperature",
                               metric_type="temperature", value=value)
        state, actions, events = MODULE.evaluate(rule_input, state, clock)
        trace.append((actions, events))
    return state, trace


def as_tuples(actions):
    return [(a.actuator, a.action, a.params) for a in actions]


def test_reading_at_t1_enters_warning_with_fan_50_and_sampling_5():
    state, trace = feed([30])
    assert state["state"] == "WARNING"
    assert as_tuples(trace[0][0]) == [("fan", "set", {"duty": 50}),
                                      ("sampling", "set", {"intervalSeconds": 5})]


def test_one_or_two_readings_over_t2_keep_warning_and_third_goes_critical():
    state, _ = feed([41, 41])
    assert state["state"] == "WARNING"
    state, trace = feed([41], state=state)
    assert state["state"] == "CRITICAL"
    assert as_tuples(trace[0][0]) == [("fan", "set", {"duty": 100}), ("buzzer", "on", {}),
                                      ("sampling", "set", {"intervalSeconds": 1})]


def test_non_consecutive_readings_over_t2_do_not_confirm():
    state, _ = feed([41, 41, 35, 41, 41])
    assert state["state"] == "WARNING"


def test_escalation_once_per_entry_and_rearmed_after_leaving_critical():
    state, trace = feed([41, 41, 41, 45, 45])
    events = [e for _, evs in trace for e in evs]
    assert len(events) == 1
    assert events[0].metric_type == "escalation" and events[0].data["reason"] == "temperature_critical"
    state, trace = feed([37, 41, 41, 41], state=state)  # 37 < 38: baja a WARNING; reentra
    assert state["state"] == "CRITICAL"
    assert len([e for _, evs in trace for e in evs]) == 1


def test_hysteresis_critical_to_warning_only_below_t2_minus_hysteresis():
    state, _ = feed([41, 41, 41])
    state, _ = feed([38], state=state)
    assert state["state"] == "CRITICAL"
    state, trace = feed([37.9], state=state)
    assert state["state"] == "WARNING"
    assert as_tuples(trace[0][0]) == [("fan", "set", {"duty": 50}), ("buzzer", "off", {}),
                                      ("sampling", "set", {"intervalSeconds": 5})]


def test_hysteresis_warning_to_normal_only_below_t1_minus_hysteresis():
    state, _ = feed([31])
    state, _ = feed([28], state=state)
    assert state["state"] == "WARNING"
    state, trace = feed([27.9], state=state)
    assert state["state"] == "NORMAL"
    assert as_tuples(trace[0][0]) == [("fan", "set", {"duty": 0}), ("buzzer", "off", {}),
                                      ("sampling", "set", {"intervalSeconds": 30})]


def test_no_actions_while_state_does_not_change():
    _, trace = feed([31, 32, 33])
    assert trace[1] == ([], []) and trace[2] == ([], [])


def test_sampling_uses_sensor_minimum_when_greater():
    thresholds = {"temperature": {**T["temperature"], "sensor_min_interval_seconds": 2}}
    _, trace = feed([41, 41, 41], thresholds=thresholds)
    assert as_tuples(trace[2][0])[-1] == ("sampling", "set", {"intervalSeconds": 2})


def test_rate_rise_disabled_by_default_and_enabled_goes_critical():
    clock = FakeClock()
    state, _ = feed([20], clock=clock)
    clock.advance(30)
    state, _ = feed([29], clock=clock, state=state)
    assert state["state"] == "NORMAL"
    enabled = {"temperature": {**T["temperature"], "rate_rise_enabled": True}}
    clock = FakeClock()
    state, _ = feed([20], thresholds=enabled, clock=clock)
    clock.advance(30)
    state, trace = feed([29], thresholds=enabled, clock=clock, state=state)
    assert state["state"] == "CRITICAL"
    assert trace[0][1][0].data["reason"] == "temperature_rate_rise"
