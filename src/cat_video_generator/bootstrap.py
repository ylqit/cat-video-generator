"""应用唯一组合根。

该模块只装配配置、连接池、Repository、Gateway和Application Service，
不包含内容判断或状态转换。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine

from .application.assets import AssetService
from .application.delivery import DeliveryService
from .application.planning import PlanningService
from .application.production import ProductionService
from .application.queries import QueryService
from .config import (
    DatabaseOperation,
    DatabaseSettings,
    RuntimeSettings,
    load_local_env,
)
from .infrastructure.ark.gateway import ArkGateway
from .infrastructure.db.repositories import SqlAlchemyWorkflowRepository
from .infrastructure.db.session import (
    create_database_engine,
    create_session_factory,
    ensure_database_ready,
)
from .infrastructure.media.qc import FfprobeMediaProbe
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
    runtime_settings: RuntimeSettings


def build_query_container() -> QueryContainer:
    """装配只读查询，不要求Ark Key或ffprobe。"""

    load_local_env()
    database = DatabaseSettings.from_env()
    engine = _ready_engine(database)
    repository = SqlAlchemyWorkflowRepository(create_session_factory(engine))
    return QueryContainer(engine=engine, queries=QueryService(repository))


def build_diagnostic_container() -> QueryContainer:
    """装配只读数据库诊断，允许在迁移落后时报告真实revision。

    该入口只供doctor使用，不执行``ensure_database_ready``。生产、查询和媒体
    命令仍必须通过最新迁移门槛，避免旧表结构被业务代码误写。
    """

    load_local_env()
    database = DatabaseSettings.from_env()
    engine = create_database_engine(
        database,
        DatabaseOperation.READ_ONLY_SMOKE,
        pool_size=1,
        max_overflow=0,
    )
    repository = SqlAlchemyWorkflowRepository(create_session_factory(engine))
    return QueryContainer(engine=engine, queries=QueryService(repository))


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
    )
    probe = FfprobeMediaProbe(runtime.ffprobe_path)
    return RuntimeContainer(
        engine=engine,
        queries=QueryService(repository),
        assets=AssetService(
            repository=repository,
            asset_store=store,
            media_probe=probe,
        ),
        delivery=DeliveryService(repository=repository, asset_store=store),
        planning=PlanningService(
            repository=repository,
            director=gateway,
            provider_name=runtime.provider_profile,
        ),
        production=ProductionService(
            repository=repository,
            media_gateway=gateway,
            asset_store=store,
            media_probe=probe,
            provider_name=runtime.provider_profile,
            resolution=runtime.ark_video_resolution,
            keyframe_review_mode=runtime.keyframe_review_mode,
            poll_interval_seconds=runtime.ark_poll_interval_seconds,
            task_timeout_seconds=runtime.ark_task_timeout_seconds,
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
    )
    probe = FfprobeMediaProbe(runtime.ffprobe_path)
    return LocalContainer(
        engine=engine,
        queries=QueryService(repository),
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
