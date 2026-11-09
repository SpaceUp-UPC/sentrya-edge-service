"""Seam 1: outbox resiliente ante caidas del broker, reintentos y retencion."""
from sqlalchemy import func, select

from edge.shared.models import CommandRow, OutboxRow, ReadingRow, SecurityEventRow


def status(h):
    return h.client.get("/api/v1/status").get_json()


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_readings_accumulate_while_disconnected_and_status_shows_it(harness):
    harness.mqtt.broker_up = False
    for value in (20.0, 21.0, 22.0):
        harness.post_reading(value=value)
    harness.tick()
    snapshot = status(harness)
    assert snapshot["outbox"]["pending"] == 3
    assert snapshot["mqtt"]["connected"] is False
    assert harness.mqtt.messages == []


def test_reconnect_sends_in_arrival_order(harness):
    harness.mqtt.broker_up = False
    for value in (20.0, 21.0, 22.0):
        harness.clock.advance(1)
        harness.post_reading(value=value)
    harness.mqtt.broker_up = True
    harness.tick()
    assert [m["payload"]["value"] for m in harness.mqtt.messages] == [20.0, 21.0, 22.0]
    assert status(harness)["outbox"]["pending"] == 0


def test_message_without_puback_is_retried_and_attempts_are_counted(harness):
    harness.post_reading()
    harness.mqtt.broker_up = False
    harness.tick()
    harness.tick()
    with harness.db.session() as session:
        assert session.scalar(select(OutboxRow.attempts)) == 2
        assert session.scalar(select(OutboxRow.synced)) is False
    harness.mqtt.broker_up = True
    harness.tick()
    with harness.db.session() as session:
        assert session.scalar(select(OutboxRow.attempts)) == 3
        assert session.scalar(select(OutboxRow.synced)) is True


def test_ingest_never_waits_for_the_broker(harness):
    harness.mqtt.broker_up = False
    assert harness.post_reading(value=60.0).status_code == 201
    # sin tick: la lectura se acepta y queda en cola; nada se intento publicar
    assert harness.mqtt.messages == []
    assert status(harness)["outbox"]["pending"] == 1


def test_startup_purges_old_synced_data_but_never_unsynced_or_security(make_harness):
    first = make_harness(RETENTION_DAYS="7")
    first.post_reading(value=20.0)   # sera sincronizada
    first.tick()
    first.mqtt.broker_up = False
    first.post_reading(value=21.0)   # nunca sincronizada
    first.tick()
    old = "2026-01-01T12:00:00.000Z"
    with first.db.session() as session:
        session.add(CommandRow(command_id="c1", node="node-3", actuator="fan", action="on",
                               status="APPLIED", created_at=old))
        session.add(SecurityEventRow(event_id="e1", type="intrusion", opened_at=old))

    first.clock.advance(8 * 86400)
    second = make_harness(RETENTION_DAYS="7")

    with second.db.session() as session:
        assert [r.synced for r in session.scalars(select(OutboxRow))] == [False]
        assert count(session, ReadingRow) == 0
        assert count(session, CommandRow) == 0
        assert count(session, SecurityEventRow) == 1


def test_startup_keeps_recent_data(make_harness):
    first = make_harness(RETENTION_DAYS="7")
    first.post_reading()
    first.tick()
    first.clock.advance(3 * 86400)
    second = make_harness(RETENTION_DAYS="7")
    with second.db.session() as session:
        assert count(session, OutboxRow) == 1
        assert count(session, ReadingRow) == 1
