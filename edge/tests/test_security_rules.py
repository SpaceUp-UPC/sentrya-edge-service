"""Seam 2: evaluate() puro del modulo de reglas de seguridad."""
from edge.rules_engine import RuleInput
from edge.security import rules
from edge.tests.fakes import FakeClock

TH = {"security": {"correlation_window_seconds": 10}}


def run(state, person=None, sound=None, event_id="ev-1"):
    extra = {"last_unknown_person_at": person, "last_suspicious_sound_at": sound,
             "event_id": event_id}
    return rules.MODULE.evaluate(RuleInput("security", TH, extra=extra), state, FakeClock())


def init():
    return rules.MODULE.initial_state()


def test_person_then_sound_within_window_is_intrusion():
    state, actions, events = run(init(), person=100.0, sound=105.0)
    assert state["state"] == "INTRUSION" and state["event_id"] == "ev-1"
    assert [(a.actuator, a.action) for a in actions] == [("siren", "on"), ("lock", "lock")]
    assert [(e.metric_type, e.data["eventId"]) for e in events] == [("intrusion", "ev-1")]


def test_sound_then_person_within_window_is_intrusion():
    state, _, events = run(init(), person=108.0, sound=100.0)
    assert state["state"] == "INTRUSION" and len(events) == 1


def test_exactly_ten_seconds_counts_but_more_does_not():
    assert run(init(), person=100.0, sound=110.0)[0]["state"] == "INTRUSION"
    state, actions, events = run(init(), person=100.0, sound=110.5)
    assert state["state"] == "MONITORING" and not actions and not events


def test_missing_mark_is_not_intrusion():
    assert run(init(), person=100.0, sound=None)[0]["state"] == "MONITORING"
    assert run(init(), person=None, sound=100.0)[0]["state"] == "MONITORING"


def test_no_second_intrusion_while_intrusion_open():
    state, _, _ = run(init(), person=100.0, sound=101.0)
    state2, actions, events = run(state, person=102.0, sound=103.0, event_id="ev-2")
    assert state2["state"] == "INTRUSION" and state2["event_id"] == "ev-1"
    assert not actions and not events


def test_consumed_marks_do_not_retrigger_after_returning_to_monitoring():
    state, _, _ = run(init(), person=100.0, sound=101.0)
    state = {**state, "state": "MONITORING"}  # lo que hara el ticket #9
    _, actions, _ = run(state, person=100.0, sound=101.0)
    assert not actions
    # persona nueva con el sonido viejo ya consumido: tampoco
    _, actions, _ = run(state, person=105.0, sound=101.0)
    assert not actions
    # persona nueva y sonido nuevo: si
    _, actions, _ = run(state, person=105.0, sound=106.0)
    assert actions


def test_tick_input_changes_nothing():
    state = init()
    assert rules.MODULE.evaluate(RuleInput("tick", TH), state, FakeClock()) == (state, [], [])
