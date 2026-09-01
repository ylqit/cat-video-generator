"""Composition root for the Creator-only application."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, text

from .application.creator_core import CreatorCoreService
from .application.creator_worker import CreatorSnapshotExecutor
from .config import DatabaseOperation, DatabaseSettings, RuntimeSettings, load_local_env
from .infrastructure.ark.gateway import ArkGateway
from .infrastructure.db.creator_repository import SqlAlchemyCreatorRepository
from .infrastructure.db.generation_queue import CreatorTaskQueue
from .infrastructure.db.session import (
    ALEMBIC_HEAD,
    create_database_engine,
    create_session_factory,
    ensure_database_ready,
)
from .infrastructure.media.storage import LocalAssetStore


@dataclass(slots=True)
class RuntimeContainer:
    engine: Engine
    creator: CreatorCoreService
    creator_repository: SqlAlchemyCreatorRepository
    creator_executor: CreatorSnapshotExecutor
    task_queue: CreatorTaskQueue
    runtime_settings: RuntimeSettings
    alembic_revision: str

    def close(self) -> None:
        self.engine.dispose()


@dataclass(slots=True)
class DiagnosticContainer:
    engine: Engine
    runtime_settings: RuntimeSettings
    database_name: str
    alembic_revision: str | None

    def close(self) -> None:
        self.engine.dispose()


def build_runtime_container() -> RuntimeContainer:
    """Build services without issuing any Provider request."""

    load_local_env()
    database = DatabaseSettings.from_env()
    runtime = RuntimeSettings.from_env()
    engine = _ready_engine(database)
    sessions = create_session_factory(engine)
    repository = SqlAlchemyCreatorRepository(sessions, asset_root=runtime.asset_root)
    gateway = ArkGateway(runtime)
    store = LocalAssetStore(
        work_root=runtime.work_root,
        asset_root=runtime.asset_root,
        ffmpeg_path=runtime.ffmpeg_path,
    )
    creator = CreatorCoreService(
        repository,
        provider_configs={
            "story_text": {
                "provider": runtime.provider_profile,
                "model": runtime.ark_planning_model,
            },
            "image": {
                "provider": runtime.provider_profile,
                "model": runtime.ark_image_model,
            },
            "video": {
                "provider": runtime.provider_profile,
                "model": runtime.ark_video_model,
                "resolution": runtime.ark_video_resolution,
            },
        },
    )
    return RuntimeContainer(
        engine=engine,
        creator=creator,
        creator_repository=repository,
        creator_executor=CreatorSnapshotExecutor(
            repository=repository,
            gateway=gateway,
            asset_store=store,
            provider_poll_interval_seconds=runtime.ark_poll_interval_seconds,
        ),
        task_queue=CreatorTaskQueue(sessions, gateway=gateway),
        runtime_settings=runtime,
        alembic_revision=ALEMBIC_HEAD,
    )


def build_diagnostic_container() -> DiagnosticContainer:
    load_local_env()
    database = DatabaseSettings.from_env()
    runtime = RuntimeSettings.from_env()
    engine = create_database_engine(
        database, DatabaseOperation.READ_ONLY_SMOKE, pool_size=1, max_overflow=0
    )
    try:
        with engine.connect() as connection:
            database_name = str(connection.execute(text("SELECT current_database()")).scalar_one())
            revision = connection.execute(
                text(f"SELECT version_num FROM {database.schema}.alembic_version")
            ).scalar_one_or_none()
    except Exception:
        engine.dispose()
        raise
    return DiagnosticContainer(
        engine=engine,
        runtime_settings=runtime,
        database_name=database_name,
        alembic_revision=revision,
    )


def _ready_engine(database: DatabaseSettings) -> Engine:
    engine = create_database_engine(database, DatabaseOperation.RUNTIME)
    try:
        ensure_database_ready(engine, database)
    except Exception:
        engine.dispose()
        raise
    return engine
