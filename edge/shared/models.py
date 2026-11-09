"""Tablas SQLite (AGENTS.md seccion 10). Las marcas de tiempo son strings ISO-8601 UTC."""
from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class NodeRow(Base):
    __tablename__ = "nodes"
    node: Mapped[str] = mapped_column(String, primary_key=True)
    ip: Mapped[str | None] = mapped_column(String, nullable=True)
    last_seen_at: Mapped[str | None] = mapped_column(String, nullable=True)
    actuators_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReadingRow(Base):
    __tablename__ = "readings"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    device_key: Mapped[str] = mapped_column(String)
    metric_type: Mapped[str] = mapped_column(String)
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String)
    measured_at: Mapped[str] = mapped_column(String)
    local_state: Mapped[str | None] = mapped_column(String, nullable=True)


class OutboxRow(Base):
    __tablename__ = "outbox"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # readingId
    topic: Mapped[str] = mapped_column(String)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String)
    synced: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class SubsystemStateRow(Base):
    __tablename__ = "subsystem_state"
    subsystem: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[str] = mapped_column(String)
    counters_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[str] = mapped_column(String)


class CommandRow(Base):
    __tablename__ = "commands"
    command_id: Mapped[str] = mapped_column(String, primary_key=True)
    node: Mapped[str] = mapped_column(String)
    actuator: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String)  # PENDING|APPLIED|FAILED|SUPERSEDED
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String)
    acked_at: Mapped[str | None] = mapped_column(String, nullable=True)


class SecurityEventRow(Base):
    __tablename__ = "security_events"
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    type: Mapped[str] = mapped_column(String)
    evidence_path: Mapped[str | None] = mapped_column(String, nullable=True)
    validation: Mapped[str | None] = mapped_column(String, nullable=True)
    opened_at: Mapped[str] = mapped_column(String)
    deadline_at: Mapped[str | None] = mapped_column(String, nullable=True)
    closed_at: Mapped[str | None] = mapped_column(String, nullable=True)


class ThresholdsRow(Base):
    """Copia vigente de los umbrales (una sola fila, id='active')."""
    __tablename__ = "thresholds"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    profile: Mapped[str] = mapped_column(String)
    params_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(String)
