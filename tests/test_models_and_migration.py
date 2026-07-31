from __future__ import annotations

import uuid

from cat_video_generator.infrastructure.db.models import Base, Episode
from cat_video_generator.infrastructure.db.records import episode_dict
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
        "0003_asset_semantic_key.py",
        "0004_planning_review.py",
    ]
    assert 'revision: str = "0001_compact_workflow"' in versions[0].read_text(
        encoding="utf-8"
    )
    assert 'revision: str = "0002_multimodal_input"' in versions[1].read_text(
        encoding="utf-8"
    )
    assert 'revision: str = "0003_asset_semantic_key"' in versions[2].read_text(
        encoding="utf-8"
    )
    assert 'revision: str = "0004_planning_review"' in versions[3].read_text(
        encoding="utf-8"
    )
    # 真实旧库曾把 metadata_json 建成 TEXT；显式转 jsonb 后兼容两种历史表型。
    assert "metadata_json::jsonb" in versions[2].read_text(encoding="utf-8")
    assert ALEMBIC_HEAD == "0004_planning_review"


def test_episode_uses_video_input_mode_column() -> None:
    episodes = Base.metadata.tables["cat_video.episodes"]
    assert "video_input_mode" in episodes.c
    assert "visual_strategy" not in episodes.c


def test_assets_have_indexed_semantic_key() -> None:
    assets = Base.metadata.tables["cat_video.assets"]
    assert "semantic_key" in assets.c
    assert "ix_assets_semantic_selection" in {
        index.name for index in assets.indexes
    }


def test_episode_status_projection_exposes_world_risk(daily_plan) -> None:
    plan = daily_plan.episodes[0]
    row = Episode(
        id=uuid.uuid4(),
        production_run_id=uuid.uuid4(),
        slot=plan.slot.value,
        sort_order=plan.slot.sort_order,
        title=plan.title,
        script_json=plan.model_dump(mode="json"),
        video_input_mode=plan.video_input_mode.value,
        status="planned",
    )

    payload = episode_dict(row)

    assert payload["worldConsistencyStatus"] == "consistent"
    assert payload["contradictions"] == []
    assert payload["renderRiskLevel"] in {"low", "medium", "high"}
    assert isinstance(payload["multiClipRecommended"], bool)
