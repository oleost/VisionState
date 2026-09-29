"""Database models and session handling (SQLite)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

SCHEMA_VERSION = 3

# Schema upgrades for existing databases, keyed on the version they upgrade to.
# Each step is a list of (table, column, SQL type) columns to add.
MIGRATIONS: dict[int, list[tuple[str, str, str]]] = {
    2: [("sensor", "triggers", "JSON")],
    3: [("sensor", "review", "JSON")],
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Sensor(Base):
    __tablename__ = "sensor"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    # "single_state" today; reserved for "multi_label", "binary", "count".
    kind: Mapped[str] = mapped_column(String(32), default="single_state")
    source_type: Mapped[str] = mapped_column(String(32))  # see sources.SOURCE_TYPES
    source: Mapped[str] = mapped_column(String(1024))
    roi: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # normalised {x, y, w, h}
    interval_s: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    debounce: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # When to check the camera besides the interval; see settings.TRIGGER_DEFAULTS.
    triggers: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Per-sensor overrides of the review rules; missing/None fields use the global rules.
    review: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    states: Mapped[list[State]] = relationship(
        back_populates="sensor", cascade="all, delete-orphan", order_by="State.position"
    )


class State(Base):
    __tablename__ = "state"

    id: Mapped[int] = mapped_column(primary_key=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensor.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    color: Mapped[str] = mapped_column(String(16))
    position: Mapped[int] = mapped_column(Integer, default=0)

    sensor: Mapped[Sensor] = relationship(back_populates="states")


class Sample(Base):
    __tablename__ = "sample"

    id: Mapped[int] = mapped_column(primary_key=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensor.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(256))
    origin: Mapped[str] = mapped_column(String(32))  # snapshot | upload | video | review | import
    use_roi: Mapped[bool] = mapped_column(Boolean, default=True)
    is_night: Mapped[bool] = mapped_column(Boolean, default=False)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Many-to-many on purpose: single-state sensors use one label, multi-label can use more.
    labels: Mapped[list[SampleLabel]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class SampleLabel(Base):
    __tablename__ = "sample_label"

    sample_id: Mapped[int] = mapped_column(ForeignKey("sample.id", ondelete="CASCADE"), primary_key=True)
    state_id: Mapped[int] = mapped_column(ForeignKey("state.id", ondelete="CASCADE"), primary_key=True)


class Embedding(Base):
    __tablename__ = "embedding"

    sample_id: Mapped[int] = mapped_column(ForeignKey("sample.id", ondelete="CASCADE"), primary_key=True)
    backbone: Mapped[str] = mapped_column(String(64), primary_key=True)
    roi_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    vector: Mapped[bytes] = mapped_column(LargeBinary)


class Prediction(Base):
    """A stored classification: published state changes and frames flagged for review."""

    __tablename__ = "prediction"

    id: Mapped[int] = mapped_column(primary_key=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensor.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    state_key: Mapped[str] = mapped_column(String(64))  # top prediction
    published_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    probs: Mapped[dict] = mapped_column(JSON, default=dict)
    frame: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_change: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)


class ModelInfo(Base):
    __tablename__ = "model_info"

    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensor.id", ondelete="CASCADE"), primary_key=True)
    backbone: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=0)
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    n_samples: Mapped[int] = mapped_column(Integer, default=0)
    accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    confusion: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    train_seconds: Mapped[float] = mapped_column(Float, default=0.0)


class Setting(Base):
    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)


class Database:
    def __init__(self, path: Path):
        self.engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(self.engine, "connect")
        def _pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        self._factory = sessionmaker(self.engine, expire_on_commit=False)

    def init(self) -> None:
        fresh = not inspect(self.engine).has_table("sensor")
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            version = conn.execute(text("PRAGMA user_version")).scalar() or 0
            if not fresh:
                for target in sorted(v for v in MIGRATIONS if v > version):
                    for table, column, sql_type in MIGRATIONS[target]:
                        columns = {c["name"] for c in inspect(conn).get_columns(table)}
                        if column not in columns:
                            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
            conn.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION}"))

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_setting(self, key: str, default=None):
        with self.session() as s:
            row = s.get(Setting, key)
            return default if row is None else row.value

    def set_setting(self, key: str, value) -> None:
        with self.session() as s:
            row = s.get(Setting, key)
            if row is None:
                s.add(Setting(key=key, value=value))
            else:
                row.value = value
