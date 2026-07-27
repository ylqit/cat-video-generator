from __future__ import annotations

from dataclasses import replace

import typer
from sqlalchemy.exc import SQLAlchemyError

from ..config import ConfigurationError, DatabaseOperation, RuntimeSettings
from ..db import create_database_engine
from ..doctor import DatabasePreflightError, inspect_database
from ..migration import (
    MigrationTargetError,
    expected_alembic_head,
    upgrade_database,
)
from ..remote_validation import RemoteValidationError, validate_remote_database
from ..runtime_validation import (
    RuntimeValidationError,
    validate_runtime_database,
)
from .common import abort_operation, database_context, echo_json, load_settings


def doctor(
    allow_insecure_readonly_smoke: bool = typer.Option(
        False,
        "--allow-insecure-readonly-smoke",
        help="Permit connectivity-only diagnostics over an unencrypted connection.",
    ),
) -> None:
    settings = load_settings()
    runtime_settings = RuntimeSettings.from_env()
    if allow_insecure_readonly_smoke:
        settings = replace(settings, allow_insecure_readonly_smoke=True)
    try:
        engine = create_database_engine(
            settings,
            DatabaseOperation.READ_ONLY_SMOKE,
        )
        report = inspect_database(
            engine,
            settings,
            expected_revision=expected_alembic_head(),
        )
    except (ConfigurationError, DatabasePreflightError, SQLAlchemyError) as exc:
        abort_operation(exc)
    finally:
        if "engine" in locals():
            engine.dispose()
    payload = report.to_dict()
    payload["runtime"] = runtime_settings.preflight_report()
    echo_json(payload)


def db_upgrade(revision: str = "head") -> None:
    settings = load_settings()
    try:
        current_revision = upgrade_database(settings, revision)
    except (ConfigurationError, MigrationTargetError, SQLAlchemyError) as exc:
        abort_operation(exc)
    echo_json(
        {
            "database": settings.database,
            "schema": settings.schema,
            "alembicRevision": current_revision,
        }
    )


def db_current() -> None:
    try:
        with database_context(require_current_revision=False) as context:
            report = inspect_database(
                context.engine,
                context.settings,
                expected_revision=expected_alembic_head(),
            )
    except (ConfigurationError, DatabasePreflightError, SQLAlchemyError) as exc:
        abort_operation(exc)
    echo_json(report.to_dict())


def db_validate_remote(
    allow_insecure_write_test: bool = typer.Option(
        False,
        "--allow-insecure-write-test",
        help=(
            "Acknowledge one isolated unencrypted write validation. This never "
            "enables normal migrations or runtime commands."
        ),
    ),
) -> None:
    if not allow_insecure_write_test:
        raise typer.BadParameter(
            "Remote write validation requires --allow-insecure-write-test."
        )
    settings = load_settings()
    if settings.sslmode != "disable":
        raise typer.BadParameter(
            "This command is only for a temporary sslmode=disable validation. "
            "Use normal PostgreSQL tests for secure connections."
        )
    try:
        report = validate_remote_database(settings)
    except (ConfigurationError, RemoteValidationError, SQLAlchemyError) as exc:
        abort_operation(exc)
    echo_json(report.to_dict())


def db_validate_runtime() -> None:
    """Validate the formal schema and clean only this run's UUID records."""
    try:
        with database_context() as context:
            report = validate_runtime_database(
                context.engine,
                context.settings,
            )
    except (
        ConfigurationError,
        DatabasePreflightError,
        RuntimeValidationError,
        SQLAlchemyError,
    ) as exc:
        abort_operation(exc)
    echo_json(report.to_dict())
