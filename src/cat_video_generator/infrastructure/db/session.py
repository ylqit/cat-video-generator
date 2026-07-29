"""数据库Engine和Session生命周期。

连接参数集中在此处，避免CLI、HTTP或Repository各自建立不同的连接池。
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from ...config import DatabaseOperation, DatabaseSettings
from .models import SCHEMA_NAME

ALEMBIC_HEAD = "0002_multimodal_input"


def create_database_engine(
    settings: DatabaseSettings,
    operation: DatabaseOperation,
    *,
    pool_size: int = 3,
    max_overflow: int = 2,
) -> Engine:
    """创建带明文安全门、超时和健康检查的共享Engine。

    后台生成线程与前端轮询并发的API模式应显式调大连接池。
    """

    settings.validate_for(operation)
    engine = create_engine(
        settings.url,
        connect_args={
            "sslmode": settings.sslmode,
            "connect_timeout": 5,
            "application_name": "cat-video-generator",
            "options": (
                "-c statement_timeout=30000 "
                "-c lock_timeout=5000 "
                "-c idle_in_transaction_session_timeout=60000"
            ),
        },
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_recycle=900,
    )
    return engine.execution_options(schema_translate_map={SCHEMA_NAME: settings.schema})


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """创建短事务Session工厂；外部Ark调用期间不持有Session。"""

    return sessionmaker(bind=engine, expire_on_commit=False)


def ensure_database_ready(
    engine: Engine,
    settings: DatabaseSettings,
) -> None:
    """在运行用例前校验数据库身份、版本和迁移版本。

    该检查位于组合根创建 Service 之前，因此配置误连或迁移落后时不会产生任何
    Ark 收费意图，也不会让 Repository 在未知表结构上写入。
    """

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT current_database(), current_setting('server_version_num')::int"
            )
        ).one()
        if row[0] != settings.database:
            raise RuntimeError(f"实际数据库{row[0]!r}与配置{settings.database!r}不一致")
        if row[1] < settings.minimum_server_version:
            raise RuntimeError("PostgreSQL版本必须不低于14")
        revision = connection.execute(
            text(f"SELECT version_num FROM {settings.schema}.alembic_version")
        ).scalar_one_or_none()
        if revision != ALEMBIC_HEAD:
            raise RuntimeError(f"数据库迁移版本为{revision!r}，期望{ALEMBIC_HEAD!r}")
