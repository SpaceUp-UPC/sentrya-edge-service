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


# ---- ventana de validacion, escalamiento y fin de la alarma (#9) ----

TH9 = {"security": {"correlation_window_seconds": 10, "validation_window_seconds": 30,
                    "siren_max_seconds": 15}}


def opened(clock):
    """Estado con una intrusion abierta en el instante actual del reloj."""
    extra = {"last_unknown_person_at": 100.0, "last_suspicious_sound_at": 101.0,
             "event_id": "ev-1"}
    state, _, _ = rules.MODULE.evaluate(RuleInput("security", TH9, extra=extra), init(), clock)
    return state


def tick(state, clock):
    return rules.MODULE.evaluate(RuleInput("tick", TH9), state, clock)


def validate(state, clock, decision, event_id=None):
    extra = {"decision": decision, "event_id": event_id}
    return rules.MODULE.evaluate(RuleInput("validation", TH9, extra=extra), state, clock)


def acts(actions):
    return [(a.actuator, a.action) for a in actions]


def escalations(events):
    return [(e.metric_type, e.device_key, e.data["eventId"], e.data["reason"]) for e in events]


def test_window_timeout_publishes_escalation_once():
    clock = FakeClock()
    state = opened(clock)
    clock.advance(29.9)
    state, actions, events = tick(state, clock)
    assert state["state"] == "INTRUSION" and not actions and not events
    clock.advance(0.1)
    state, _, events = tick(state, clock)
    assert escalations(events) == [("escalation", "escalation", "ev-1", "timeout")]
    clock.advance(1)
    _, _, events = tick(state, clock)
    assert not events


def test_confirm_publishes_escalation_and_keeps_the_siren():
    clock = FakeClock()
    state = opened(clock)
    clock.advance(5)
    state, actions, events = validate(state, clock, "confirm")
    assert escalations(events) == [("escalation", "escalation", "ev-1", "confirmed")]
    assert state["state"] == "ALARM" and not actions
    clock.advance(30)
    _, _, events = tick(state, clock)
    assert not any(e.metric_type == "escalation" for e in events)  # no escala dos veces


def test_reject_turns_the_siren_off_without_escalating():
    clock = FakeClock()
    state = opened(clock)
    clock.advance(5)
    state, actions, events = validate(state, clock, "reject")
    assert state["state"] == "MONITORING" and acts(actions) == [("siren", "off")]
    assert not events
    clock.advance(30)
    assert tick(state, clock)[1:] == ([], [])


def test_validation_for_another_event_or_without_window_is_ignored():
    clock = FakeClock()
    state = opened(clock)
    assert validate(state, clock, "confirm", event_id="otro")[1:] == ([], [])
    assert validate(state, clock, "confirm", event_id="ev-1")[2]
    assert validate(init(), clock, "reject")[1:] == ([], [])


def test_siren_turns_off_at_its_max_duration_after_confirming():
    clock = FakeClock()
    state = opened(clock)
    clock.advance(2)
    state, _, _ = validate(state, clock, "confirm")
    clock.advance(12.9)  # 14.9 s desde que se encendio
    state, actions, _ = tick(state, clock)
    assert state["state"] == "ALARM" and not actions
    clock.advance(0.1)
    state, actions, events = tick(state, clock)
    assert state["state"] == "MONITORING" and acts(actions) == [("siren", "off")] and not events


def test_timeout_after_the_max_duration_escalates_and_ends_the_alarm_at_once():
    clock = FakeClock()  # demo: ventana 30 s > sirena 15 s
    state = opened(clock)
    clock.advance(30)
    state, actions, events = tick(state, clock)
    assert escalations(events) == [("escalation", "escalation", "ev-1", "timeout")]
    assert acts(actions) == [("siren", "off")] and state["state"] == "MONITORING"


def test_siren_keeps_sounding_after_timeout_until_its_max_duration():
    th = {"security": {**TH9["security"], "siren_max_seconds": 300}}  # perfil report
    clock = FakeClock()
    state = opened(clock)
    clock.advance(30)
    state, actions, _ = rules.MODULE.evaluate(RuleInput("tick", th), state, clock)
    assert state["state"] == "ALARM" and not actions
    clock.advance(270)
    state, actions, _ = rules.MODULE.evaluate(RuleInput("tick", th), state, clock)
    assert state["state"] == "MONITORING" and acts(actions) == [("siren", "off")]


def test_no_second_intrusion_while_the_alarm_sounds():
    clock = FakeClock()
    state = opened(clock)
    state, _, _ = validate(state, clock, "confirm")
    extra = {"last_unknown_person_at": 200.0, "last_suspicious_sound_at": 201.0,
             "event_id": "ev-2"}
    state, actions, events = rules.MODULE.evaluate(RuleInput("security", TH9, extra=extra),
                                                   state, clock)
    assert state["event_id"] == "ev-1" and not actions and not events


def test_restart_abandons_the_window_and_silences_the_siren():
    clock = FakeClock()
    state = opened(clock)
    state, actions, events = rules.MODULE.evaluate(RuleInput("restart", TH9), state, clock)
    assert state["state"] == "MONITORING" and acts(actions) == [("siren", "off")]
    assert not events  # el escalamiento sale de security_events, no del estado
    _, actions, _ = rules.MODULE.evaluate(RuleInput("restart", TH9), init(), clock)
    assert not actions


def test_no_security_transition_ever_unlocks():
    clock = FakeClock()
    seen = []
    for decision in ("confirm", "reject", None):
        state = opened(clock)
        if decision:
            state, actions, _ = validate(state, clock, decision)
            seen += actions
        for _ in range(4):
            clock.advance(10)
            state, actions, _ = tick(state, clock)
            seen += actions
        seen += rules.MODULE.evaluate(RuleInput("restart", TH9), opened(clock), clock)[1]
    assert seen and all(a.action != "unlock" for a in seen)
