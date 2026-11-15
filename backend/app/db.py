import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    create_engine,
    event,
    ForeignKey,
    String,
    Text,
    JSON,
    DateTime,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import settings


def uid():
    return str(uuid.uuid4())


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SessionToken(Base):
    __tablename__ = "sessions"
    digest: Mapped[str] = mapped_column(String, primary_key=True)
    key_digest: Mapped[str] = mapped_column(String)
    csrf: Mapped[str] = mapped_column(String)
    expires: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text, default="")
    cases: Mapped[list] = mapped_column(JSON)
    fingerprint: Mapped[str] = mapped_column(String)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    idempotency_key: Mapped[str] = mapped_column(String, unique=True)
    request_digest: Mapped[str] = mapped_column(String)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"))
    dataset_name: Mapped[str] = mapped_column(String)
    fingerprint: Mapped[str] = mapped_column(String)
    snapshot: Mapped[list] = mapped_column(JSON)
    name: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    instruction: Mapped[str] = mapped_column(Text, default="")
    max_tokens: Mapped[int] = mapped_column(default=256)
    input_price: Mapped[float | None]
    output_price: Mapped[float | None]
    status: Mapped[str] = mapped_column(String, index=True, default="queued")
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(String, nullable=True)


class Result(Base):
    __tablename__ = "results"
    __table_args__ = (UniqueConstraint("run_id", "position"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    position: Mapped[int]
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    correct: Mapped[bool] = mapped_column(default=False)
    forbidden_hit: Mapped[bool] = mapped_column(default=False)
    passed: Mapped[bool] = mapped_column(default=False)
    latency_ms: Mapped[float]
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    cost_usd: Mapped[float | None]
    error: Mapped[str | None] = mapped_column(String, nullable=True)


engine = create_engine(
    settings().database_url,
    connect_args=(
        {"check_same_thread": False, "timeout": 30}
        if settings().database_url.startswith("sqlite")
        else {}
    ),
    pool_pre_ping=True,
)
if engine.dialect.name == "sqlite":

    @event.listens_for(engine, "connect")
    def sqlite_setup(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")


Session = sessionmaker(engine, expire_on_commit=False)


def db():
    with Session() as s:
        yield s


def init_db():
    Base.metadata.create_all(engine)
