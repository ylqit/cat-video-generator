from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from cat_video_generator.infrastructure.db.models import SCHEMA_NAME, Base
from cat_video_generator.infrastructure.db.session import ALEMBIC_HEAD

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_migration() -> ModuleType:
    path = PROJECT_ROOT / "alembic" / "versions" / "0001_creator_core_baseline.py"
    spec = importlib.util.spec_from_file_location("creator_core_baseline", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load migration: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_creator_baseline_is_the_only_migration() -> None:
    migration = _load_migration()
    versions = sorted((PROJECT_ROOT / "alembic" / "versions").glob("*.py"))
    assert [path.name for path in versions] == ["0001_creator_core_baseline.py"]
    assert migration.revision == "0001_creator_core_baseline"  # type: ignore[attr-defined]
    assert migration.down_revision is None  # type: ignore[attr-defined]
    assert ALEMBIC_HEAD == "0001_creator_core_baseline"


def test_creator_baseline_exposes_exactly_the_allowed_business_tables() -> None:
    table_names = {table.name for table in Base.metadata.tables.values()}
    assert table_names == {
        "creator_projects",
        "creator_shots",
        "media_assets",
        "generation_snapshots",
        "generation_tasks",
        "generation_task_events",
        "creator_timelines",
    }
    project_columns = Base.metadata.tables[f"{SCHEMA_NAME}.creator_projects"].columns
    shot_columns = Base.metadata.tables[f"{SCHEMA_NAME}.creator_shots"].columns
    snapshot_columns = Base.metadata.tables[f"{SCHEMA_NAME}.generation_snapshots"].columns
    assert {"id", "version", "brief_body", "current_story_json", "reference_bindings_json"} <= set(
        project_columns.keys()
    )
    assert {"id", "project_id", "sort_order", "direction", "selected_video_asset_id"} <= set(
        shot_columns.keys()
    )
    assert {
        "id",
        "project_id",
        "creator_shot_id",
        "prompt_text",
        "input_hash",
        "confirmed_at",
    } <= set(snapshot_columns.keys())
