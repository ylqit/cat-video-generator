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
from cat_video_generator.domain.workflow import StepKind
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
            command.upgrade(config, "head")
            revision = connection.execute(
                text(f"SELECT version_num FROM {quoted}.alembic_version")
            ).scalar_one()
            assert revision == "0007_pipeline_settings"

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
        first = repository.create_step_intent(
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
        )
        second = repository.create_step_intent(
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
        )
        assert first.id == second.id

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
                                "inputMode": "multimodal_reference",
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
            assert row == ("ready", "succeeded", "ready", 1)
    finally:
        with engine.begin() as connection:
            quoted = connection.dialect.identifier_preparer.quote_schema(schema)
            connection.execute(text(f"DROP SCHEMA IF EXISTS {quoted} CASCADE"))
        engine.dispose()
