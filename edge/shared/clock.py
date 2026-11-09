"""Reloj inyectable: monotonico para ventanas/temporizadores, UTC para marcas de tiempo."""
import time
from datetime import datetime, timezone


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def now_utc(self) -> datetime:
        return datetime.now(timezone.utc)


def iso_utc(moment: datetime) -> str:
    """UTC ISO-8601 con milisegundos y sufijo Z."""
    moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"
