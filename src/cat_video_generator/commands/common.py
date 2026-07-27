from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import typer
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import (
    ConfigurationError,
    DatabaseOperation,
    DatabaseSettings,
)
from ..content_service import ContentNotFoundError
from ..contracts import ContentConflictError, ContentValidationError
from ..db import create_database_engine, create_session_factory
from ..delivery import DeliveryError
from ..doctor import DatabasePreflightError, require_runtime_database
from ..generation.errors import OrchestrationError
from ..migration import MigrationTargetError, expected_alembic_head
from ..remote_validation import RemoteValidationError
from ..runtime_validation import RuntimeValidationError

SAFE_ERRORS = (
    ConfigurationError,
    DatabasePreflightError,
    RemoteValidationError,
    ContentValidationError,
    ContentConflictError,
    ContentNotFoundError,
    OrchestrationError,
    DeliveryError,
    MigrationTargetError,
    RuntimeValidationError,
)


@dataclass(frozen=True, slots=True)
class DatabaseContext:
    settings: DatabaseSettings
    engine: Engine
    session_factory: sessionmaker[Session]


def load_settings() -> DatabaseSettings:
    try:
        return DatabaseSettings.from_env()
    except ConfigurationError as exc:
        raise typer.BadParameter(str(exc)) from exc


@contextmanager
def database_context(
    operation: DatabaseOperation = DatabaseOperation.RUNTIME,
    *,
    require_current_revision: bool = True,
) -> Iterator[DatabaseContext]:
    """Own guarded engine construction, migration preflight, and disposal."""
    settings = load_settings()
    engine = create_database_engine(settings, operation)
    try:
        if require_current_revision:
            require_runtime_database(
                engine,
                settings,
                expected_alembic_head(),
            )
        yield DatabaseContext(
            settings=settings,
            engine=engine,
            session_factory=create_session_factory(engine),
        )
    finally:
        engine.dispose()


def abort_operation(exc: Exception) -> None:
    """Return a secret-safe failure without rendering connection details."""
    typer.echo(
        json.dumps(
            {
                "ok": False,
                "errorType": type(exc).__name__,
                "error": (
                    str(exc)
                    if isinstance(exc, SAFE_ERRORS)
                    else "Database operation failed; inspect server-side logs."
                ),
            },
            ensure_ascii=False,
        )
    )
    raise typer.Exit(code=2) from exc


def echo_json(value: object) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))
