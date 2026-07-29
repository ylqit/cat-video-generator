from __future__ import annotations

from cat_video_generator.infrastructure.db.models import Base
from cat_video_generator.infrastructure.db.session import ALEMBIC_HEAD


def test_compact_metadata_has_exactly_eight_business_tables() -> None:
    assert {table.name for table in Base.metadata.sorted_tables} == {
        "production_runs",
        "episodes",
        "workflow_steps",
        "prompt_records",
        "assets",
        "reviews",
        "delivery_packages",
        "delivery_items",
    }


def test_compact_migration_has_multimodal_upgrade() -> None:
    from pathlib import Path

    versions = sorted(Path("alembic/versions").glob("*.py"))
    assert [path.name for path in versions] == [
        "0001_compact_workflow.py",
        "0002_multimodal_input.py",
    ]
    assert 'revision: str = "0001_compact_workflow"' in versions[0].read_text(
        encoding="utf-8"
    )
    assert 'revision: str = "0002_multimodal_input"' in versions[1].read_text(
        encoding="utf-8"
    )
    assert ALEMBIC_HEAD == "0002_multimodal_input"


def test_episode_uses_video_input_mode_column() -> None:
    episodes = Base.metadata.tables["cat_video.episodes"]
    assert "video_input_mode" in episodes.c
    assert "visual_strategy" not in episodes.c
