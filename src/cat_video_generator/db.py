from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import DatabaseOperation, DatabaseSettings
from .models import SCHEMA_NAME

POOL_SIZE = 3
MAX_OVERFLOW = 2
POOL_RECYCLE_SECONDS = 900


def create_database_engine(
    settings: DatabaseSettings,
    operation: DatabaseOperation,
) -> Engine:
    """Create a guarded engine with the product's connection policy."""
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
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_pre_ping=True,
        pool_recycle=POOL_RECYCLE_SECONDS,
    )
    return engine.execution_options(
        schema_translate_map={SCHEMA_NAME: settings.schema}
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
