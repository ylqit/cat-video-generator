from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
