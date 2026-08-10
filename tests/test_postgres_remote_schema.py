"""远程PostgreSQL一次性隔离Schema测试；绝不触碰正式cat_video。"""

from __future__ import annotations

import os
import uuid
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from alembic.config import Config
from conftest import episode_for
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from alembic import command
from cat_video_generator.config import DatabaseOperation, DatabaseSettings, load_local_env
from cat_video_generator.domain.contracts import Slot
from cat_video_generator.domain.snapshots import DirectorInputSnapshot, ImageInputSnapshot
from cat_video_generator.domain.workflow import PromptPurpose, StepKind, StepStatus
from cat_video_generator.infrastructure.db.repositories import SqlAlchemyWorkflowRepository
from cat_video_generator.infrastructure.db.session import (
    create_database_engine,
    create_session_factory,
)


@pytest.mark.postgres
def test_remote_0012_migration_atomic_intent_and_review(monkeypatch) -> None:
    if os.environ.get("CAT_VIDEO_POSTGRES_TEST_MODE") != "remote-schema":
        pytest.skip("需要显式CAT_VIDEO_POSTGRES_TEST_MODE=remote-schema")
    load_local_env()
    base = DatabaseSettings.from_env()
    schema = f"cat_video_test_{uuid.uuid4().hex[:12]}"
    settings = replace(base, schema=schema)
    engine = create_database_engine(settings, DatabaseOperation.TEST, pool_size=1, max_overflow=0)
    monkeypatch.setenv("CAT_VIDEO_DB_SCHEMA", schema)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))

    try:
        with engine.connect() as connection:
            quoted = connection.dialect.identifier_preparer.quote_schema(schema)
            connection.execute(text(f"CREATE SCHEMA {quoted}"))
            connection.commit()
            config.attributes["connection"] = connection
            config.attributes["schema"] = schema
            command.upgrade(config, "0010_look_prompt_purposes")

            old_run = uuid.uuid4()
            connection.execute(
                text(
                    f"INSERT INTO {quoted}.production_runs "
                    "(id, content_date, planning_json, pipeline_settings_json, status) "
                    "VALUES (:id, DATE '2026-08-10', '{}'::jsonb, '{}'::jsonb, 'draft')"
                ),
                {"id": old_run},
            )
            connection.commit()
            with pytest.raises(RuntimeError, match="清理全部历史 Run"):
                command.upgrade(config, "head")
            connection.rollback()
            connection.execute(
                text(f"DELETE FROM {quoted}.production_runs WHERE id = :id"),
                {"id": old_run},
            )
            connection.commit()
            command.upgrade(config, "head")
            revision = connection.execute(
                text(f"SELECT version_num FROM {quoted}.alembic_version")
            ).scalar_one()
            assert revision == "0012_minimal_director_contract"

        repository = SqlAlchemyWorkflowRepository(create_session_factory(engine))
        run_id = repository.create_draft_run(date(2026, 8, 10))
        director_snapshot = DirectorInputSnapshot(
            phase="day",
            prompt_sha256="a" * 64,
            output_contract="DayBrief",
        ).model_dump(mode="json")
        first, first_prompt = repository.create_step_with_prompt_intent(
            run_id=run_id,
            episode_id=None,
            parent_step_id=None,
            kind=StepKind.DIRECTOR,
            attempt=1,
            operation_key="director:day",
            provider="test",
            model="planning-model",
            input_hash="b" * 64,
            input_snapshot=director_snapshot,
            prompt_purpose=PromptPurpose.DIRECTOR,
            prompt_model="planning-model",
            prompt_text="全天总导演实际Prompt",
            parent_prompt_id=None,
        )
        repeated, repeated_prompt = repository.create_step_with_prompt_intent(
            run_id=run_id,
            episode_id=None,
            parent_step_id=None,
            kind=StepKind.DIRECTOR,
            attempt=1,
            operation_key="director:day",
            provider="test",
            model="planning-model",
            input_hash="b" * 64,
            input_snapshot=director_snapshot,
            prompt_purpose=PromptPurpose.DIRECTOR,
            prompt_model="planning-model",
            prompt_text="全天总导演实际Prompt",
            parent_prompt_id=None,
        )
        assert repeated.id == first.id
        assert repeated_prompt == first_prompt

        repository.set_step_status(first.id, StepStatus.SUBMITTING)
        repository.finish_director_step(
            step_id=first.id,
            response_id="resp-day-1",
            request_hash="c" * 64,
            provider_output={"theme": "原始导演对象", "slot_briefs": []},
            normalized_output={"theme": "标准化导演对象", "slot_briefs": []},
            normalization_warnings=("机械归一化示例",),
        )
        trace = repository.step_trace(first.id)
        assert trace["inputSummary"]["output_contract"] == "DayBrief"
        assert "provider_output" not in trace["inputSummary"]
        assert trace["providerOutput"]["theme"] == "原始导演对象"
        assert trace["normalizedOutput"]["theme"] == "标准化导演对象"
        assert trace["normalizationWarnings"] == ["机械归一化示例"]
        assert trace["actualPrompts"][0]["text"] == "全天总导演实际Prompt"

        with pytest.raises(ValueError, match="director步骤不允许purpose=review"):
            repository.create_step_with_prompt_intent(
                run_id=run_id,
                episode_id=None,
                parent_step_id=None,
                kind=StepKind.DIRECTOR,
                attempt=2,
                operation_key="director:day",
                provider="test",
                model="planning-model",
                input_hash="c" * 64,
                input_snapshot=director_snapshot,
                prompt_purpose=PromptPurpose.REVIEW,
                prompt_model="planning-model",
                prompt_text="非法用途",
                parent_prompt_id=None,
            )

        episode_id = uuid.uuid4()
        episode = episode_for(Slot.MORNING)
        with engine.begin() as connection:
            quoted = connection.dialect.identifier_preparer.quote_schema(schema)
            connection.execute(
                text(
                    f"INSERT INTO {quoted}.episodes "
                    "(id, production_run_id, slot, sort_order, script_json, "
                    "prompt_overrides_json, status) VALUES "
                    "(:id, :run, 'morning', 1, CAST(:script AS jsonb), "
                    "'{}'::jsonb, 'preparing_visuals')"
                ),
                {
                    "id": episode_id,
                    "run": run_id,
                    "script": episode.script.model_dump_json(),
                },
            )
        repository.save_prompt_overrides(
            episode_id=episode_id,
            overrides={"video": "人工确认的视频Prompt"},
            enabled=True,
        )
        override_state = repository.get_prompt_override_state(episode_id)
        assert override_state["enabled"] is True
        assert override_state["stale"] is False
        assert repository.get_prompt_overrides(episode_id) == {
            "video": "人工确认的视频Prompt"
        }
        image_step, _ = repository.create_step_with_prompt_intent(
            run_id=run_id,
            episode_id=episode_id,
            parent_step_id=None,
            kind=StepKind.IMAGE,
            attempt=1,
            operation_key="image:look",
            provider="test",
            model="image-model",
            input_hash="d" * 64,
            input_snapshot=ImageInputSnapshot(
                target="look",
                prompt_sha256="e" * 64,
                reference_asset_ids=(),
                reference_sha256=(),
            ).model_dump(mode="json"),
            prompt_purpose=PromptPurpose.IMAGE,
            prompt_model="image-model",
            prompt_text="定妆图Prompt",
            parent_prompt_id=None,
        )
        repository.set_step_status(image_step.id, StepStatus.SUBMITTING)
        repository.set_step_status(image_step.id, StepStatus.AWAITING_REVIEW)
        asset_id = uuid.uuid4()
        with engine.begin() as connection:
            quoted = connection.dialect.identifier_preparer.quote_schema(schema)
            connection.execute(
                text(
                    f"INSERT INTO {quoted}.assets "
                    "(id, production_run_id, episode_id, producing_step_id, role, semantic_key, "
                    "scope, status, media_type, local_path, sha256, metadata_json) "
                    "VALUES (:id, :run, :episode, :step, 'look_reference', 'look:test', "
                    "'episode', 'candidate', 'image', 'C:/tmp/look.png', :sha, '{}'::jsonb)"
                ),
                {
                    "id": asset_id,
                    "run": run_id,
                    "episode": episode_id,
                    "step": image_step.id,
                    "sha": "f" * 64,
                },
            )
        first_review = repository.commit_asset_review(
            asset_id=asset_id,
            source="ark_visual",
            decision="approved",
            reason="语义审核通过",
            warnings=[],
            evidence={"confidence": 0.95},
        )
        repeated_review = repository.commit_asset_review(
            asset_id=asset_id,
            source="ark_visual",
            decision="approved",
            reason="重复提交同一决定",
            warnings=[],
            evidence={"confidence": 0.95},
        )
        assert first_review.idempotent is False
        assert repeated_review.idempotent is True
        with pytest.raises(ValueError, match="不能覆盖"):
            repository.commit_asset_review(
                asset_id=asset_id,
                source="ark_visual",
                decision="rejected",
                reason="相反决定",
                warnings=[],
                evidence={},
            )

        with engine.connect() as connection:
            quoted = connection.dialect.identifier_preparer.quote_schema(schema)
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        f"INSERT INTO {quoted}.prompt_records "
                        "(id, step_id, purpose, model, prompt_text, sha256) "
                        "VALUES (:id, :step, 'storyboard', 'x', 'old', :sha)"
                    ),
                    {"id": uuid.uuid4(), "step": first.id, "sha": uuid.uuid4().hex.ljust(64, "0")},
                )
                connection.commit()
            connection.rollback()
    finally:
        with engine.begin() as connection:
            quoted = connection.dialect.identifier_preparer.quote_schema(schema)
            connection.execute(text(f"DROP SCHEMA IF EXISTS {quoted} CASCADE"))
        engine.dispose()
