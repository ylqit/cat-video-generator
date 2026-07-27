from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, text

from alembic import command

from .config import DatabaseOperation, DatabaseSettings
from .db import create_database_engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class MigrationTargetError(RuntimeError):
    """Raised when an existing schema is unsafe for automatic migration."""


def alembic_config(
    *,
    schema: str | None = None,
    connection: Connection | None = None,
) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    if schema is not None:
        config.attributes["schema"] = schema
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def expected_alembic_head() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if head is None:
        raise RuntimeError("Alembic has no head revision")
    return head


def upgrade_database(
    settings: DatabaseSettings,
    revision: str = "head",
) -> str:
    """Upgrade one guarded schema without taking over unknown existing objects."""
    engine = create_database_engine(settings, DatabaseOperation.MIGRATION)
    try:
        with engine.begin() as connection:
            schema_exists = bool(
                connection.scalar(
                    text("SELECT to_regnamespace(:schema_name) IS NOT NULL"),
                    {"schema_name": settings.schema},
                )
            )
            if schema_exists:
                objects = set(
                    connection.execute(
                        text(
                            "SELECT c.relname "
                            "FROM pg_class AS c "
                            "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = :schema_name "
                            "AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')"
                        ),
                        {"schema_name": settings.schema},
                    ).scalars()
                )
                if objects and "alembic_version" not in objects:
                    object_list = ", ".join(sorted(objects))
                    raise MigrationTargetError(
                        f"Schema {settings.schema!r} contains unmanaged objects "
                        f"and has no Alembic version table: {object_list}."
                    )
            else:
                quoted_schema = (
                    connection.dialect.identifier_preparer.quote_schema(
                        settings.schema
                    )
                )
                connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))

            command.upgrade(
                alembic_config(
                    schema=settings.schema,
                    connection=connection,
                ),
                revision,
            )
            current_revision = connection.scalar(
                text(
                    f'SELECT version_num FROM "{settings.schema}".alembic_version'
                )
            )
            if current_revision is None:
                raise MigrationTargetError(
                    "Alembic upgrade completed without a current revision."
                )
            return str(current_revision)
    finally:
        engine.dispose()
