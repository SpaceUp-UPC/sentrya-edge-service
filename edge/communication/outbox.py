"""Outbox store-and-forward en SQLite: todo lo publicable se guarda primero con synced = 0."""
import json

from sqlalchemy import func, select, text

from edge.shared.models import OutboxRow


def enqueue(session, message_id: str, topic: str, payload: dict, created_at: str) -> None:
    session.add(OutboxRow(id=message_id, topic=topic, payload_json=json.dumps(payload),
                          created_at=created_at, synced=False, attempts=0))


def pending(session, limit: int = 100) -> list[OutboxRow]:
    """Pendientes en orden de llegada."""
    query = (select(OutboxRow).where(OutboxRow.synced.is_(False))
             .order_by(OutboxRow.created_at, text("outbox.rowid")).limit(limit))
    return list(session.scalars(query))


def pending_count(session) -> int:
    return session.scalar(select(func.count()).select_from(OutboxRow)
                          .where(OutboxRow.synced.is_(False))) or 0
