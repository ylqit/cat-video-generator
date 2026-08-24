"""Shared PostgreSQL engine and migration gate."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from ...config import DatabaseOperation, DatabaseSettings
from .models import SCHEMA_NAME

ALEMBIC_HEAD = "0028_story_event_candidates"


def create_database_engine(
    settings: DatabaseSettings,
    operation: DatabaseOperation,
    *,
    pool_size: int = 3,
    max_overflow: int = 2,
) -> Engine:
    settings.validate_for(operation)
    engine = create_engine(
        settings.url,
        connect_args={
            "sslmode": settings.sslmode,
            "connect_timeout": 5,
            "application_name": "cat-video-generator",
            "options": (
                "-c statement_timeout=30000 "
                "-c lock_timeout=5000 "
                "-c idle_in_transaction_session_timeout=60000"
            ),
        },
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_recycle=900,
    )
    return engine.execution_options(schema_translate_map={SCHEMA_NAME: settings.schema})


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def ensure_database_ready(engine: Engine, settings: DatabaseSettings) -> None:
    """Reject writes against the wrong database or an older schema."""

    with engine.connect() as connection:
        database, server_version = connection.execute(
            text("SELECT current_database(), current_setting('server_version_num')::int")
        ).one()
        if database != settings.database:
            raise RuntimeError(
                f"connected database {database!r} does not match {settings.database!r}"
            )
        if server_version < settings.minimum_server_version:
            raise RuntimeError("PostgreSQL 14 or newer is required")
        revision = connection.execute(
            text(f"SELECT version_num FROM {settings.schema}.alembic_version")
        ).scalar_one_or_none()
        if revision != ALEMBIC_HEAD:
            raise RuntimeError(f"database revision is {revision!r}; expected {ALEMBIC_HEAD!r}")
