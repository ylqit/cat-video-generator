from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import Engine, text

from .config import DatabaseSettings


class DatabasePreflightError(RuntimeError):
    """Raised when the database cannot safely run production work."""


@dataclass(frozen=True, slots=True)
class DatabaseDoctorReport:
    connected: bool
    pool_healthy: bool
    database: str
    user: str
    server_version_num: int
    server_version: str
    ssl_in_use: bool
    transport_security: str
    insecure_runtime_authorized: bool
    schema_exists: bool
    schema_usage_allowed: bool
    alembic_revision: str | None
    expected_database: str
    minimum_server_version: int
    ready_for_migrations: bool
    ready_for_runtime: bool
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["warnings"] = list(self.warnings)
        return result


def inspect_database(
    engine: Engine,
    settings: DatabaseSettings,
    *,
    expected_revision: str | None = None,
) -> DatabaseDoctorReport:
    with engine.connect() as connection:
        identity = connection.execute(
            text(
                "SELECT current_database(), current_user, "
                "current_setting('server_version_num')::integer, version(), "
                "COALESCE(("
                "  SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()"
                "), false)"
            )
        ).one()
        schema_exists = bool(
            connection.execute(
                text(
                    "SELECT EXISTS ("
                    "  SELECT 1 FROM information_schema.schemata "
                    "  WHERE schema_name = :schema_name"
                    ")"
                ),
                {"schema_name": settings.schema},
            ).scalar_one()
        )
        schema_usage_allowed = False
        alembic_revision = None
        if schema_exists:
            schema_usage_allowed = bool(
                connection.execute(
                    text(
                        "SELECT has_schema_privilege("
                        "  current_user, :schema_name, 'USAGE'"
                        ")"
                    ),
                    {"schema_name": settings.schema},
                ).scalar_one()
            )
            version_table_exists = bool(
                connection.execute(
                    text("SELECT to_regclass(:qualified_name) IS NOT NULL"),
                    {"qualified_name": f"{settings.schema}.alembic_version"},
                ).scalar_one()
            )
            if version_table_exists:
                alembic_revision = connection.execute(
                    text(
                        f'SELECT version_num FROM "{settings.schema}".alembic_version'
                    )
                ).scalar_one_or_none()

    with engine.connect() as pooled_connection:
        pool_healthy = pooled_connection.execute(text("SELECT 1")).scalar_one() == 1

    warnings: list[str] = []
    database_matches = identity[0] == settings.database
    version_supported = identity[2] >= settings.minimum_server_version
    if not database_matches:
        warnings.append(
            f"Connected database {identity[0]!r} does not match "
            f"configured database {settings.database!r}."
        )
    if not version_supported:
        warnings.append(
            f"PostgreSQL server_version_num={identity[2]} is below "
            f"{settings.minimum_server_version}."
        )
    if not identity[4]:
        if settings.insecure_runtime_allowed:
            warnings.append(
                "The current PostgreSQL session is plaintext and production "
                "runtime is explicitly authorized as temporary architecture debt."
            )
        else:
            warnings.append("The current PostgreSQL session is not encrypted.")
    if not schema_exists:
        warnings.append(f"Schema {settings.schema!r} does not exist yet.")
    elif not schema_usage_allowed:
        warnings.append(
            f"Current user cannot use schema {settings.schema!r}."
        )
    if expected_revision is not None and alembic_revision != expected_revision:
        warnings.append(
            f"Alembic revision is {alembic_revision!r}; "
            f"expected {expected_revision!r}."
        )

    transport_allowed = (
        bool(identity[4])
        or settings.insecure_local_test_allowed
        or settings.insecure_remote_write_test_allowed
        or settings.insecure_runtime_allowed
    )
    ready_for_migrations = database_matches and version_supported and transport_allowed
    ready_for_runtime = (
        ready_for_migrations
        and pool_healthy
        and schema_exists
        and schema_usage_allowed
        and expected_revision is not None
        and alembic_revision == expected_revision
    )
    return DatabaseDoctorReport(
        connected=True,
        pool_healthy=pool_healthy,
        database=identity[0],
        user=identity[1],
        server_version_num=identity[2],
        server_version=identity[3],
        ssl_in_use=bool(identity[4]),
        transport_security="tls" if identity[4] else "plaintext",
        insecure_runtime_authorized=settings.insecure_runtime_allowed,
        schema_exists=schema_exists,
        schema_usage_allowed=schema_usage_allowed,
        alembic_revision=alembic_revision,
        expected_database=settings.database,
        minimum_server_version=settings.minimum_server_version,
        ready_for_migrations=ready_for_migrations,
        ready_for_runtime=ready_for_runtime,
        warnings=tuple(warnings),
    )


def require_runtime_database(
    engine: Engine,
    settings: DatabaseSettings,
    expected_revision: str,
) -> DatabaseDoctorReport:
    report = inspect_database(
        engine,
        settings,
        expected_revision=expected_revision,
    )
    if not report.ready_for_runtime:
        detail = "; ".join(report.warnings) or "unknown database preflight failure"
        raise DatabasePreflightError(detail)
    return report
