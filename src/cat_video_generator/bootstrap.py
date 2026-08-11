"""应用唯一组合根。

该模块只装配配置、连接池、Repository、Gateway和Application Service，
不包含内容判断或状态转换。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine

from .application.assets import AssetService
from .application.delivery import DeliveryService
from .application.event_seeds import EventSeedCatalog
from .application.planning import PlanningService
from .application.production import ProductionService
from .application.queries import QueryService
from .application.regeneration import RegenerationService
from .application.retry import RetryService
from .application.studio_editing import StudioEditingService
from .application.video_editing import VideoEditingService
from .application.video_execution import VideoExecutionService
from .application.visual_preparation import VisualPreparationService
from .config import (
    DatabaseOperation,
    DatabaseSettings,
    RuntimeSettings,
    load_local_env,
)
from .domain.visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
)
from .infrastructure.ark.gateway import ArkGateway
from .infrastructure.db.repositories import SqlAlchemyWorkflowRepository
from .infrastructure.db.session import (
    create_database_engine,
    create_session_factory,
    ensure_database_ready,
)
from .infrastructure.media.qc import FfmpegFrameExtractor, FfprobeMediaProbe
from .infrastructure.media.storage import LocalAssetStore


@dataclass(slots=True)
class QueryContainer:
    """只读接口所需对象和数据库生命周期。"""

    engine: Engine
    queries: QueryService

    def close(self) -> None:
        self.engine.dispose()


@dataclass(slots=True)
class RuntimeContainer(QueryContainer):
    """规划、生成、审核和交付所需完整对象。"""

    assets: AssetService
    delivery: DeliveryService
    planning: PlanningService
    production: ProductionService
    retry: RetryService
    regeneration: RegenerationService
    video_editing: VideoEditingService
    studio_editing: StudioEditingService
    runtime_settings: RuntimeSettings


def build_query_container() -> QueryContainer:
    """装配只读查询，不要求Ark Key或ffprobe。"""

    load_local_env()
    database = DatabaseSettings.from_env()
    runtime = RuntimeSettings.from_env()
    engine = _ready_engine(database)
    repository = SqlAlchemyWorkflowRepository(create_session_factory(engine))
    return QueryContainer(
        engine=engine,
        queries=QueryService(
            repository,
            video_resolution=runtime.ark_video_resolution,
        ),
    )


def build_diagnostic_container() -> QueryContainer:
    """装配只读数据库诊断，允许在迁移落后时报告真实revision。

    该入口只供doctor使用，不执行``ensure_database_ready``。生产、查询和媒体
    命令仍必须通过最新迁移门槛，避免旧表结构被业务代码误写。
    """

    load_local_env()
    database = DatabaseSettings.from_env()
    runtime = RuntimeSettings.from_env()
    engine = create_database_engine(
        database,
        DatabaseOperation.READ_ONLY_SMOKE,
        pool_size=1,
        max_overflow=0,
    )
    repository = SqlAlchemyWorkflowRepository(create_session_factory(engine))
    return QueryContainer(
        engine=engine,
        queries=QueryService(
            repository,
            video_resolution=runtime.ark_video_resolution,
        ),
    )


def build_runtime_container(
    *,
    allow_paid_generation: bool,
    require_paid_permission: bool = True,
    pool_size: int = 3,
    max_overflow: int = 2,
) -> RuntimeContainer:
    """装配完整生产运行时并执行付费准入检查。"""

    load_local_env()
    database = DatabaseSettings.from_env()
    runtime = RuntimeSettings.from_env()
    if require_paid_permission:
        runtime.validate_for_generation(allow_paid_generation=allow_paid_generation)
    else:
        runtime.validate_for_ark_access()
        if runtime.ffprobe_path is None:
            raise ValueError("恢复任务要求ffprobe可用")
    assert runtime.ffprobe_path is not None
    if runtime.ffmpeg_path is None:
        raise ValueError("完整生产服务要求ffmpeg可用，以支持边界帧与非破坏性区间替换")
    engine = _ready_engine(
        database,
        pool_size=pool_size,
        max_overflow=max_overflow,
    )
    repository = SqlAlchemyWorkflowRepository(create_session_factory(engine))
    gateway = ArkGateway(runtime)
    store = LocalAssetStore(
        work_root=runtime.work_root,
        asset_root=runtime.asset_root,
        delivery_root=runtime.delivery_root,
        ffmpeg_path=runtime.ffmpeg_path,
    )
    probe = FfprobeMediaProbe(runtime.ffprobe_path)
    frame_extractor = FfmpegFrameExtractor(
        ffmpeg_path=runtime.ffmpeg_path,
        work_root=runtime.work_root,
    )
    visual_preparation = VisualPreparationService(
        repository=repository,
        media_gateway=gateway,
        visual_review_gateway=gateway,
        asset_store=store,
        media_probe=probe,
        provider_name=runtime.provider_profile,
        image_review_mode=runtime.image_review_mode.value,
        image_request_timeout_seconds=runtime.ark_image_request_timeout_seconds,
        image_timeout_auto_retries=runtime.ark_image_timeout_auto_retries,
        image_retry_delay_seconds=runtime.ark_image_retry_delay_seconds,
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
    )
    video_execution = VideoExecutionService(
        repository=repository,
        media_gateway=gateway,
        asset_store=store,
        media_probe=probe,
        provider_name=runtime.provider_profile,
        resolution=runtime.ark_video_resolution,
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        review_gateway=gateway,
        frame_extractor=frame_extractor,
        diagnostic_mode=runtime.video_semantic_review_mode,
        api_timeout_seconds=runtime.ark_video_api_timeout_seconds,
        poll_interval_seconds=runtime.ark_poll_interval_seconds,
        task_timeout_seconds=runtime.ark_task_timeout_seconds,
        style_profile=DEFAULT_STYLE_PROFILE,
    )
    planning = PlanningService(
        repository=repository,
        director=gateway,
        provider_name=runtime.provider_profile,
        event_seed_catalog=EventSeedCatalog(runtime.event_seed_root),
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
        video_resolution=runtime.ark_video_resolution,
    )
    regeneration = RegenerationService(
        repository=repository,
        planning=planning,
        visual_preparation=visual_preparation,
        video_execution=video_execution,
    )
    video_editing = VideoEditingService(
        repository=repository,
        media_gateway=gateway,
        asset_store=store,
        media_probe=probe,
        frame_extractor=frame_extractor,
        video_execution=video_execution,
        provider_name=runtime.provider_profile,
        resolution=runtime.ark_video_resolution,
        poll_interval_seconds=runtime.ark_poll_interval_seconds,
        task_timeout_seconds=runtime.ark_task_timeout_seconds,
        api_timeout_seconds=runtime.ark_video_api_timeout_seconds,
    )
    retry = RetryService(
        repository=repository,
        visual_preparation=visual_preparation,
        video_execution=video_execution,
        video_editing=video_editing,
    )
    return RuntimeContainer(
        engine=engine,
        queries=QueryService(
            repository,
            video_resolution=runtime.ark_video_resolution,
        ),
        assets=AssetService(
            repository=repository,
            asset_store=store,
            media_probe=probe,
        ),
        delivery=DeliveryService(repository=repository, asset_store=store),
        planning=planning,
        production=ProductionService(
            repository=repository,
            visual_preparation=visual_preparation,
            video_execution=video_execution,
        ),
        retry=retry,
        regeneration=regeneration,
        video_editing=video_editing,
        studio_editing=StudioEditingService(
            repository=repository,
            series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
            style_profile=DEFAULT_STYLE_PROFILE,
            video_resolution=runtime.ark_video_resolution,
        ),
        runtime_settings=runtime,
    )


@dataclass(slots=True)
class LocalContainer(QueryContainer):
    """不调用Ark的Canon、人工审核和交付对象。"""

    assets: AssetService
    delivery: DeliveryService
    runtime_settings: RuntimeSettings


def build_local_container() -> LocalContainer:
    """装配不需要Ark Key或付费许可的本地用例。"""

    load_local_env()
    database = DatabaseSettings.from_env()
    runtime = RuntimeSettings.from_env()
    engine = _ready_engine(database)
    repository = SqlAlchemyWorkflowRepository(create_session_factory(engine))
    store = LocalAssetStore(
        work_root=runtime.work_root,
        asset_root=runtime.asset_root,
        delivery_root=runtime.delivery_root,
        ffmpeg_path=runtime.ffmpeg_path,
    )
    probe = FfprobeMediaProbe(runtime.ffprobe_path)
    return LocalContainer(
        engine=engine,
        queries=QueryService(
            repository,
            video_resolution=runtime.ark_video_resolution,
        ),
        assets=AssetService(
            repository=repository,
            asset_store=store,
            media_probe=probe,
        ),
        delivery=DeliveryService(repository=repository, asset_store=store),
        runtime_settings=runtime,
    )


def _ready_engine(
    database: DatabaseSettings,
    *,
    pool_size: int = 3,
    max_overflow: int = 2,
) -> Engine:
    """创建并验证 Engine；预检失败时立即释放连接池。"""

    engine = create_database_engine(
        database,
        DatabaseOperation.RUNTIME,
        pool_size=pool_size,
        max_overflow=max_overflow,
    )
    try:
        ensure_database_ready(engine, database)
    except Exception:
        engine.dispose()
        raise
    return engine
