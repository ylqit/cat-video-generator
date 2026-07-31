from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from cat_video_generator.config import (
    DatabaseOperation,
    DatabaseSettings,
    load_local_env,
)
from cat_video_generator.domain.workflow import (
    EpisodeStatus,
    RunStatus,
    StepKind,
    StepStatus,
    WorkflowTransitionError,
)
from cat_video_generator.infrastructure.db.models import (
    Asset,
    Base,
    Episode,
    ProductionRun,
    Review,
    WorkflowStep,
)
from cat_video_generator.infrastructure.db.repositories import (
    SqlAlchemyWorkflowRepository,
)


@pytest.fixture(scope="module")
def postgres_sessions():
    """默认使用容器；显式remote-schema模式只操作唯一隔离Schema。"""

    container = None
    use_remote = os.environ.get("CAT_VIDEO_POSTGRES_TEST_MODE") == "remote-schema"
    if use_remote:
        load_local_env()
        settings = DatabaseSettings.from_env()
        settings.validate_for(DatabaseOperation.TEST)
        engine = create_engine(
            settings.url,
            connect_args={
                "sslmode": settings.sslmode,
                "connect_timeout": 5,
                "application_name": "cat-video-generator-test",
            },
            pool_size=4,
            max_overflow=2,
            pool_pre_ping=True,
        )
        with engine.connect() as connection:
            database, version = connection.execute(
                text(
                    "SELECT current_database(), "
                    "current_setting('server_version_num')::int"
                )
            ).one()
        if database != settings.database or version < settings.minimum_server_version:
            engine.dispose()
            raise RuntimeError("远程测试数据库身份或版本不符合配置")
    else:
        from testcontainers.community.postgres import PostgresContainer

        try:
            container = PostgresContainer("postgres:16-alpine")
            container.start()
        except Exception as exc:  # pragma: no cover - 取决于本机Docker状态
            pytest.skip(
                f"Docker不可用，跳过一次性PostgreSQL测试: {type(exc).__name__}"
            )
        url = container.get_connection_url().replace(
            "postgresql+psycopg2://",
            "postgresql+psycopg://",
        )
        engine = create_engine(url, pool_size=4, max_overflow=2)
    schema = f"cat_video_test_{uuid.uuid4().hex[:12]}"
    translated = engine.execution_options(
        schema_translate_map={"cat_video": schema}
    )
    schema_created = False
    try:
        with translated.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            schema_created = True
        Base.metadata.create_all(translated)
        yield sessionmaker(bind=translated, expire_on_commit=False)
    finally:
        if schema_created:
            # schema由本Fixture使用不可预测UUID创建，删除目标不会触碰正式cat_video。
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()
        if container is not None:
            container.stop()


@dataclass(frozen=True)
class ReviewFixture:
    run_id: uuid.UUID
    episode_id: uuid.UUID
    step_id: uuid.UUID
    asset_id: uuid.UUID


def _seed_review_case(
    sessions,
    daily_plan,
    *,
    step_status: StepStatus,
    episode_status: EpisodeStatus,
) -> ReviewFixture:
    run_id = uuid.uuid4()
    episode_id = uuid.uuid4()
    step_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    with sessions.begin() as session:
        session.add(
            ProductionRun(
                id=run_id,
                content_date=daily_plan.content_date,
                status=RunStatus.GENERATING.value,
            )
        )
        # 测试模型刻意不声明 ORM relationship；逐层 flush 才能真实模拟
        # 生产代码的短事务顺序，并让 PostgreSQL 严格外键检查得到父记录。
        session.flush()
        plan = daily_plan.episodes[0]
        session.add(
            Episode(
                id=episode_id,
                production_run_id=run_id,
                slot=plan.slot.value,
                sort_order=plan.slot.sort_order,
                title=plan.title,
                script_json=plan.model_dump(mode="json"),
                video_input_mode=plan.video_input_mode.value,
                status=episode_status.value,
            )
        )
        session.flush()
        session.add(
            WorkflowStep(
                id=step_id,
                production_run_id=run_id,
                episode_id=episode_id,
                kind=StepKind.VIDEO.value,
                status=step_status.value,
                attempt=1,
                idempotency_key=uuid.uuid4().hex * 2,
                input_hash=uuid.uuid4().hex * 2,
            )
        )
        session.flush()
        session.add(
            Asset(
                id=asset_id,
                production_run_id=run_id,
                episode_id=episode_id,
                producing_step_id=step_id,
                role="video",
                semantic_key=f"video:{episode_id}-single-pass",
                scope="episode",
                status="candidate",
                media_type="video",
                local_path=f"C:/tmp/{asset_id}.mp4",
                sha256=uuid.uuid4().hex * 2,
                metadata_json={"durationMs": 10000},
            )
        )
    return ReviewFixture(run_id, episode_id, step_id, asset_id)


@pytest.mark.postgres
def test_invalid_review_state_writes_nothing(
    postgres_sessions,
    daily_plan,
) -> None:
    case = _seed_review_case(
        postgres_sessions,
        daily_plan,
        step_status=StepStatus.FAILED,
        episode_status=EpisodeStatus.CONTENT_REVIEW,
    )
    repository = SqlAlchemyWorkflowRepository(postgres_sessions)

    with pytest.raises(ValueError, match="等待审核"):
        repository.commit_asset_review(
            asset_id=case.asset_id,
            source="human",
            decision="approved",
            reason="确认内容可用",
            warnings=[],
            evidence={},
        )

    with postgres_sessions() as session:
        assert session.scalar(
            select(func.count()).select_from(Review).where(
                Review.asset_id == case.asset_id
            )
        ) == 0
        assert session.get(Asset, case.asset_id).status == "candidate"
        assert session.get(Episode, case.episode_id).status == "content_review"


@pytest.mark.postgres
def test_review_exception_rolls_back_all_tables(
    postgres_sessions,
    daily_plan,
) -> None:
    case = _seed_review_case(
        postgres_sessions,
        daily_plan,
        step_status=StepStatus.AWAITING_REVIEW,
        episode_status=EpisodeStatus.PLANNED,
    )
    repository = SqlAlchemyWorkflowRepository(postgres_sessions)

    with pytest.raises(WorkflowTransitionError):
        repository.commit_asset_review(
            asset_id=case.asset_id,
            source="human",
            decision="approved",
            reason="故意触发Episode非法状态转换",
            warnings=[],
            evidence={},
        )

    with postgres_sessions() as session:
        assert session.scalar(
            select(func.count()).select_from(Review).where(
                Review.asset_id == case.asset_id
            )
        ) == 0
        assert session.get(Asset, case.asset_id).status == "candidate"
        assert session.get(WorkflowStep, case.step_id).status == "awaiting_review"
        assert session.get(Episode, case.episode_id).status == "planned"


@pytest.mark.postgres
def test_concurrent_same_review_decision_is_idempotent(
    postgres_sessions,
    daily_plan,
) -> None:
    case = _seed_review_case(
        postgres_sessions,
        daily_plan,
        step_status=StepStatus.AWAITING_REVIEW,
        episode_status=EpisodeStatus.CONTENT_REVIEW,
    )
    repository = SqlAlchemyWorkflowRepository(postgres_sessions)

    def approve():
        return repository.commit_asset_review(
            asset_id=case.asset_id,
            source="human",
            decision="approved",
            reason="并发审核使用同一决定",
            warnings=[],
            evidence={},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: approve(), range(2)))

    assert {item.idempotent for item in results} == {False, True}
    with postgres_sessions() as session:
        assert session.scalar(
            select(func.count()).select_from(Review).where(
                Review.asset_id == case.asset_id,
                Review.decision == "approved",
            )
        ) == 1
        assert session.get(Asset, case.asset_id).status == "ready"
        assert session.get(WorkflowStep, case.step_id).status == "succeeded"
        assert session.get(Episode, case.episode_id).status == "ready"

    with pytest.raises(ValueError, match="相反最终审核决定"):
        repository.commit_asset_review(
            asset_id=case.asset_id,
            source="human",
            decision="rejected",
            reason="不得覆盖已经提交的批准结论",
            warnings=[],
            evidence={},
        )


@pytest.mark.postgres
def test_operation_key_is_part_of_step_idempotency(
    postgres_sessions,
    daily_plan,
) -> None:
    case = _seed_review_case(
        postgres_sessions,
        daily_plan,
        step_status=StepStatus.AWAITING_REVIEW,
        episode_status=EpisodeStatus.CONTENT_REVIEW,
    )
    repository = SqlAlchemyWorkflowRepository(postgres_sessions)
    input_hash = "a" * 64

    first = repository.create_step_intent(
        run_id=case.run_id,
        episode_id=case.episode_id,
        parent_step_id=None,
        kind=StepKind.QC,
        attempt=1,
        provider=None,
        model=None,
        input_hash=input_hash,
        request_summary={"operationKey": "qc:first"},
    )
    repeated = repository.create_step_intent(
        run_id=case.run_id,
        episode_id=case.episode_id,
        parent_step_id=None,
        kind=StepKind.QC,
        attempt=1,
        provider=None,
        model=None,
        input_hash=input_hash,
        request_summary={"operationKey": "qc:first"},
    )
    second_operation = repository.create_step_intent(
        run_id=case.run_id,
        episode_id=case.episode_id,
        parent_step_id=None,
        kind=StepKind.QC,
        attempt=1,
        provider=None,
        model=None,
        input_hash=input_hash,
        request_summary={"operationKey": "qc:second"},
    )

    assert repeated.id == first.id
    assert second_operation.id != first.id
