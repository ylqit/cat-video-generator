"""远程PostgreSQL一次性Schema迁移与约束测试。"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from alembic import command
from cat_video_generator.config import (
    DatabaseOperation,
    DatabaseSettings,
    load_local_env,
)
from cat_video_generator.domain.workflow import PromptPurpose, StepKind
from cat_video_generator.infrastructure.db.repositories import (
    SqlAlchemyWorkflowRepository,
)
from cat_video_generator.infrastructure.db.session import (
    create_database_engine,
    create_session_factory,
)


@pytest.mark.postgres
def test_remote_schema_upgrade_constraints_and_cleanup(monkeypatch) -> None:
    if os.environ.get("CAT_VIDEO_POSTGRES_TEST_MODE") != "remote-schema":
        pytest.skip("需要显式CAT_VIDEO_POSTGRES_TEST_MODE=remote-schema")
    load_local_env()
    base = DatabaseSettings.from_env()
    schema = f"cat_video_test_{uuid.uuid4().hex[:12]}"
    settings = replace(base, schema=schema)
    engine = create_database_engine(
        settings,
        DatabaseOperation.TEST,
        pool_size=1,
        max_overflow=0,
    )
    monkeypatch.setenv("CAT_VIDEO_DB_SCHEMA", schema)
    try:
        with engine.connect() as connection:
            quoted = connection.dialect.identifier_preparer.quote_schema(schema)
            connection.execute(text(f"CREATE SCHEMA {quoted}"))
            connection.commit()
            config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
            config.attributes["connection"] = connection
            config.attributes["schema"] = schema
            command.upgrade(config, "0008_storyboard_first_core")
            revision = connection.execute(
                text(f"SELECT version_num FROM {quoted}.alembic_version")
            ).scalar_one()
            assert revision == "0008_storyboard_first_core"

            # 旧image用途应被迁移为storyboard；0009的五种用途都必须可写。
            legacy_run_id = uuid.uuid4()
            legacy_step_id = uuid.uuid4()
            connection.execute(
                text(
                    f"INSERT INTO {quoted}.production_runs "
                    "(id, content_date, planning_json, pipeline_settings_json, status) "
                    "VALUES (:id, DATE '2026-08-01', '{}'::jsonb, '{}'::jsonb, 'draft')"
                ),
                {"id": legacy_run_id},
            )
            connection.execute(
                text(
                    f"INSERT INTO {quoted}.workflow_steps "
                    "(id, production_run_id, kind, status, attempt, operation_key, "
                    "idempotency_key, input_hash, input_snapshot_json) "
                    "VALUES (:id, :run, 'image', 'pending', 1, 'image:legacy', "
                    ":key, :hash, '{}'::jsonb)"
                ),
                {
                    "id": legacy_step_id,
                    "run": legacy_run_id,
                    "key": uuid.uuid4().hex.ljust(64, "0"),
                    "hash": "9" * 64,
                },
            )
            connection.execute(
                text(
                    f"INSERT INTO {quoted}.prompt_records "
                    "(id, step_id, purpose, model, prompt_text, sha256) "
                    "VALUES (:id, :step, 'image', 'legacy-model', 'legacy image', :hash)"
                ),
                {
                    "id": uuid.uuid4(),
                    "step": legacy_step_id,
                    "hash": "8" * 64,
                },
            )
            connection.commit()
            command.upgrade(config, "head")
            revision = connection.execute(
                text(f"SELECT version_num FROM {quoted}.alembic_version")
            ).scalar_one()
            assert revision == "0009_storyboard_prompt_purposes"
            assert connection.execute(
                text(
                    f"SELECT purpose FROM {quoted}.prompt_records "
                    "WHERE step_id = :step"
                ),
                {"step": legacy_step_id},
            ).scalar_one() == "storyboard"

            purpose_step_ids: dict[str, uuid.UUID] = {}
            for purpose in (
                "director",
                "storyboard",
                "storyboard_review",
                "video",
                "review",
            ):
                step_id = uuid.uuid4()
                purpose_step_ids[purpose] = step_id
                kind = (
                    "director"
                    if purpose == "director"
                    else "image"
                    if purpose.startswith("storyboard")
                    else "video"
                )
                connection.execute(
                    text(
                        f"INSERT INTO {quoted}.workflow_steps "
                        "(id, production_run_id, kind, status, attempt, operation_key, "
                        "idempotency_key, input_hash, input_snapshot_json) "
                        "VALUES (:id, :run, :kind, 'pending', 1, :operation, "
                        ":key, :hash, '{}'::jsonb)"
                    ),
                    {
                        "id": step_id,
                        "run": legacy_run_id,
                        "kind": kind,
                        "operation": f"test:{purpose}",
                        "key": uuid.uuid4().hex.ljust(64, "0"),
                        "hash": uuid.uuid4().hex.ljust(64, "0"),
                    },
                )
                connection.execute(
                    text(
                        f"INSERT INTO {quoted}.prompt_records "
                        "(id, step_id, purpose, model, prompt_text, sha256) "
                        "VALUES (:id, :step, :purpose, 'test-model', :body, :hash)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "step": step_id,
                        "purpose": purpose,
                        "body": f"prompt {purpose}",
                        "hash": uuid.uuid4().hex.ljust(64, "0"),
                    },
                )
            connection.commit()
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        f"INSERT INTO {quoted}.prompt_records "
                        "(id, step_id, purpose, model, prompt_text, sha256) "
                        "VALUES (:id, :step, 'unknown', 'test', 'bad', :hash)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "step": purpose_step_ids["director"],
                        "hash": uuid.uuid4().hex.ljust(64, "0"),
                    },
                )
                connection.commit()
            connection.rollback()

            # 重复升级必须是安全no-op。
            command.upgrade(config, "head")

            run_id = uuid.uuid4()
            connection.execute(
                text(
                    f"INSERT INTO {quoted}.production_runs "
                    "(id, content_date, planning_json, status) "
                    "VALUES (:id, DATE '2026-08-02', '{}'::jsonb, 'draft')"
                ),
                {"id": run_id},
            )
            connection.commit()
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        f"INSERT INTO {quoted}.episodes "
                        "(id, production_run_id, slot, sort_order, script_json, status) "
                        "VALUES (:id, :run, 'morning', 2, '{}'::jsonb, 'planned')"
                    ),
                    {"id": uuid.uuid4(), "run": run_id},
                )
                connection.commit()
            connection.rollback()

        repository = SqlAlchemyWorkflowRepository(create_session_factory(engine))
        first, first_prompt_id = repository.create_step_with_prompt_intent(
            run_id=run_id,
            episode_id=None,
            parent_step_id=None,
            kind=StepKind.DIRECTOR,
            attempt=1,
            operation_key="director:day",
            provider="test",
            model="test-model",
            input_hash="a" * 64,
            input_snapshot={
                "type": "director",
                "phase": "day",
                "prompt_sha256": "b" * 64,
                "output_contract": "DayBrief",
            },
            prompt_purpose=PromptPurpose.DIRECTOR,
            prompt_model="test-model",
            prompt_text="测试总导演Prompt",
            parent_prompt_id=None,
        )
        second, second_prompt_id = repository.create_step_with_prompt_intent(
            run_id=run_id,
            episode_id=None,
            parent_step_id=None,
            kind=StepKind.DIRECTOR,
            attempt=1,
            operation_key="director:day",
            provider="test",
            model="test-model",
            input_hash="a" * 64,
            input_snapshot={
                "type": "director",
                "phase": "day",
                "prompt_sha256": "b" * 64,
                "output_contract": "DayBrief",
            },
            prompt_purpose=PromptPurpose.DIRECTOR,
            prompt_model="test-model",
            prompt_text="测试总导演Prompt",
            parent_prompt_id=None,
        )
        assert first.id == second.id
        assert first_prompt_id == second_prompt_id
        with pytest.raises(ValueError, match="两个不同的生成Prompt"):
            repository.create_step_with_prompt_intent(
                run_id=run_id,
                episode_id=None,
                parent_step_id=None,
                kind=StepKind.DIRECTOR,
                attempt=1,
                operation_key="director:day",
                provider="test",
                model="test-model",
                input_hash="a" * 64,
                input_snapshot={
                    "type": "director",
                    "phase": "day",
                    "prompt_sha256": "b" * 64,
                    "output_contract": "DayBrief",
                },
                prompt_purpose=PromptPurpose.DIRECTOR,
                prompt_model="test-model",
                prompt_text="同一输入哈希下不允许替换正文",
                parent_prompt_id=None,
            )
        with engine.connect() as connection:
            quoted = connection.dialect.identifier_preparer.quote_schema(schema)
            assert connection.execute(
                text(
                    f"SELECT count(*) FROM {quoted}.prompt_records "
                    "WHERE step_id = :step"
                ),
                {"step": first.id},
            ).scalar_one() == 1

        invalid_operation = "director:atomic-rollback"
        with pytest.raises(IntegrityError):
            repository.create_step_with_prompt_intent(
                run_id=run_id,
                episode_id=None,
                parent_step_id=None,
                kind=StepKind.DIRECTOR,
                attempt=1,
                operation_key=invalid_operation,
                provider="test",
                model="test-model",
                input_hash="7" * 64,
                input_snapshot={
                    "type": "director",
                    "phase": "day",
                    "prompt_sha256": "6" * 64,
                    "output_contract": "DayBrief",
                },
                prompt_purpose=PromptPurpose.DIRECTOR,
                prompt_model="test-model",
                prompt_text="必须随Step一起回滚的Prompt",
                parent_prompt_id=uuid.uuid4(),
            )
        with engine.connect() as connection:
            quoted = connection.dialect.identifier_preparer.quote_schema(schema)
            assert connection.execute(
                text(
                    f"SELECT count(*) FROM {quoted}.workflow_steps "
                    "WHERE operation_key = :operation"
                ),
                {"operation": invalid_operation},
            ).scalar_one() == 0

        episode_id = uuid.uuid4()
        review_step_id = uuid.uuid4()
        asset_id = uuid.uuid4()
        with engine.begin() as connection:
            quoted = connection.dialect.identifier_preparer.quote_schema(schema)
            connection.execute(
                text(
                    f"INSERT INTO {quoted}.episodes "
                    "(id, production_run_id, slot, sort_order, script_json, status) "
                    "VALUES (:id, :run, 'morning', 1, '{}'::jsonb, 'content_review')"
                ),
                {"id": episode_id, "run": run_id},
            )
            connection.execute(
                text(
                    f"INSERT INTO {quoted}.workflow_steps "
                    "(id, production_run_id, episode_id, kind, status, attempt, "
                    "operation_key, idempotency_key, input_hash, input_snapshot_json) "
                    "VALUES (:id, :run, :episode, 'video', 'awaiting_review', 1, "
                    "'video:single_pass', :key, :hash, CAST(:snapshot AS jsonb))"
                ),
                {
                    "id": review_step_id,
                    "run": run_id,
                    "episode": episode_id,
                    "key": "c" * 64,
                    "hash": "d" * 64,
                    "snapshot": json.dumps(
                        {
                            "type": "video",
                            "promptSha256": "e" * 64,
                            "inputPlan": {
                                "inputMode": "storyboard_reference",
                                "resolution": "720p",
                                "durationSeconds": 9,
                                "bindings": [],
                            },
                            "inputAssetIds": [],
                        }
                    ),
                },
            )
            connection.execute(
                text(
                    f"INSERT INTO {quoted}.assets "
                    "(id, production_run_id, episode_id, producing_step_id, role, "
                    "semantic_key, scope, status, media_type, local_path, sha256, metadata_json) "
                    "VALUES (:id, :run, :episode, :step, 'video', 'video:morning', "
                    "'episode', 'candidate', 'video', 'test.mp4', :hash, '{}'::jsonb)"
                ),
                {
                    "id": asset_id,
                    "run": run_id,
                    "episode": episode_id,
                    "step": review_step_id,
                    "hash": "f" * 64,
                },
            )

        # Ark视频诊断可以给出approved，但它不是人工最终决定。
        # 最终审核的幂等范围必须包含source，否则Web人工通过会被诊断记录吞掉。
        repository.record_review(
            step_id=review_step_id,
            asset_id=asset_id,
            source="ark_visual",
            decision="approved",
            reason="diagnostic only",
            warnings=[],
            evidence={"confidence": 0.95},
        )
        committed = repository.commit_asset_review(
            asset_id=asset_id,
            source="human",
            decision="approved",
            reason="remote schema transaction test",
            warnings=[],
            evidence={},
        )
        assert committed.idempotent is False
        reused = repository.commit_asset_review(
            asset_id=asset_id,
            source="human",
            decision="approved",
            reason="same decision is idempotent",
            warnings=[],
            evidence={},
        )
        assert reused.review_id == committed.review_id
        assert reused.idempotent is True
        with pytest.raises(ValueError, match="不能覆盖"):
            repository.commit_asset_review(
                asset_id=asset_id,
                source="human",
                decision="rejected",
                reason="opposite decision must roll back",
                warnings=[],
                evidence={},
            )
        with engine.connect() as connection:
            quoted = connection.dialect.identifier_preparer.quote_schema(schema)
            row = connection.execute(
                text(
                    f"SELECT a.status, s.status, e.status, "
                    f"(SELECT count(*) FROM {quoted}.reviews WHERE asset_id = a.id) "
                    f"FROM {quoted}.assets a "
                    f"JOIN {quoted}.workflow_steps s ON s.id = a.producing_step_id "
                    f"JOIN {quoted}.episodes e ON e.id = a.episode_id "
                    "WHERE a.id = :asset"
                ),
                {"asset": asset_id},
            ).one()
            assert row == ("ready", "succeeded", "ready", 2)
    finally:
        with engine.begin() as connection:
            quoted = connection.dialect.identifier_preparer.quote_schema(schema)
            connection.execute(text(f"DROP SCHEMA IF EXISTS {quoted} CASCADE"))
        engine.dispose()
