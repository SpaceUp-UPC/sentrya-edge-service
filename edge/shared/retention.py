"""Retencion al arrancar (AGENTS.md seccion 10).

Se purga lo sincronizado del outbox, las lecturas y las ordenes con mas de N dias.
Nunca se tocan el outbox no sincronizado, `security_events` ni la evidencia.
Las marcas de tiempo son ISO-8601 UTC, asi que comparan bien como texto.
"""
from datetime import timedelta

from sqlalchemy import delete

from edge.shared.clock import iso_utc
from edge.shared.logging import get_logger
from edge.shared.models import CommandRow, OutboxRow, ReadingRow

log = get_logger("retention")


def purge(db, clock, retention_days: int) -> dict:
    cutoff = iso_utc(clock.now_utc() - timedelta(days=retention_days))
    with db.session() as session:
        removed = {
            "outbox": session.execute(delete(OutboxRow).where(
                OutboxRow.synced.is_(True), OutboxRow.created_at < cutoff)).rowcount,
            "readings": session.execute(delete(ReadingRow).where(
                ReadingRow.measured_at < cutoff)).rowcount,
            "commands": session.execute(delete(CommandRow).where(
                CommandRow.created_at < cutoff)).rowcount,
        }
    log.info("Retencion (%s dias): %s", retention_days, removed)
    return removed
