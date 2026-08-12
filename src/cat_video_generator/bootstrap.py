"""Composition root for the V5 video-clip workflow monolith."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, text

from .application.canon import CanonRepairService
from .application.shot_queue import ProjectEditingService, SequenceService, ShotProductionService
from .config import DatabaseOperation, DatabaseSettings, RuntimeSettings, load_local_env
from .infrastructure.ark.gateway import ArkGateway
from .infrastructure.db.repositories import SqlAlchemyWorkflowRepository
from .infrastructure.db.session import (
    ALEMBIC_HEAD,
    create_database_engine,
    create_session_factory,
    ensure_database_ready,
)
from .infrastructure.media.qc import FfmpegFrameExtractor, FfprobeMediaProbe
from .infrastructure.media.storage import LocalAssetStore


@dataclass(slots=True)
class RuntimeContainer:
    engine: Engine
    repository: SqlAlchemyWorkflowRepository
    editing: ProjectEditingService
    production: ShotProductionService
    sequences: SequenceService
    canon: CanonRepairService
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
    """Build the studio without issuing any provider request.

    Missing Ark credentials do not prevent local project editing.  Paid
    endpoints fail at their natural boundary until the environment is fixed.
    """

    load_local_env()
    database = DatabaseSettings.from_env()
    runtime = RuntimeSettings.from_env()
    engine = _ready_engine(database)
    repository = SqlAlchemyWorkflowRepository(
        create_session_factory(engine),
        asset_root=runtime.asset_root,
    )
    gateway = _optional_gateway(runtime)
    store = LocalAssetStore(
        work_root=runtime.work_root,
        asset_root=runtime.asset_root,
        ffmpeg_path=runtime.ffmpeg_path,
    )
    probe = FfprobeMediaProbe(runtime.ffprobe_path)
    extractor = (
        None
        if runtime.ffmpeg_path is None
        else FfmpegFrameExtractor(ffmpeg_path=runtime.ffmpeg_path, work_root=runtime.work_root)
    )
    return RuntimeContainer(
        engine=engine,
        repository=repository,
        editing=ProjectEditingService(
            repository=repository,
            director=gateway,
            provider_name=runtime.provider_profile,
        ),
        production=ShotProductionService(
            repository=repository,
            gateway=gateway,
            asset_store=store,
            media_probe=probe,
            frame_extractor=extractor,
            provider_name=runtime.provider_profile,
            resolution=runtime.ark_video_resolution,
            runtime_preflight=runtime,
            enable_video_advice=runtime.video_semantic_review_mode == "diagnostic",
            poll_interval_seconds=runtime.ark_poll_interval_seconds,
            task_timeout_seconds=runtime.ark_task_timeout_seconds,
        ),
        sequences=SequenceService(
            repository=repository,
            asset_store=store,
            media_probe=probe,
            resolution=runtime.ark_video_resolution,
            runtime_preflight=runtime,
        ),
        canon=CanonRepairService(repository=repository, asset_store=store),
        runtime_settings=runtime,
        alembic_revision=ALEMBIC_HEAD,
    )


def build_diagnostic_container() -> DiagnosticContainer:
    load_local_env()
    database = DatabaseSettings.from_env()
    runtime = RuntimeSettings.from_env()
    engine = create_database_engine(
        database,
        DatabaseOperation.READ_ONLY_SMOKE,
        pool_size=1,
        max_overflow=0,
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


def _optional_gateway(runtime: RuntimeSettings) -> ArkGateway | None:
    try:
        runtime.validate_for_ark_access()
    except ValueError:
        return None
    return ArkGateway(runtime)
