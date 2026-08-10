"""紧凑PostgreSQL工作流模型。

表结构只保存稳定关系和查询字段；导演脚本、供应商扩展元数据及审核证据使用
JSONB。视频和图片二进制始终保存在本地文件系统。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ...domain.contracts import CURRENT_CONTRACT_VERSION
from ...domain.workflow import (
    EpisodeStatus,
    PromptPurpose,
    RunStatus,
    StepKind,
    StepStatus,
)

SCHEMA_NAME = "cat_video"


def _values(
    items: type[RunStatus] | type[EpisodeStatus] | type[StepStatus] | type[StepKind],
) -> tuple[str, ...]:
    return tuple(item.value for item in items)


def _check(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class Base(DeclarativeBase):
    """所有数据库表的SQLAlchemy元数据根。"""


class ProductionRun(Base):
    __tablename__ = "production_runs"
    __table_args__ = (
        CheckConstraint(
            _check("status", _values(RunStatus)),
            name="ck_production_runs_status",
        ),
        CheckConstraint(
            f"contract_version = {CURRENT_CONTRACT_VERSION}",
            name="ck_production_runs_contract_version",
        ),
        Index("ix_production_runs_queue", "status", "content_date", "created_at"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    content_date: Mapped[date] = mapped_column(Date, nullable=False)
    contract_version: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
    )
    planning_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    pipeline_settings_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=RunStatus.DRAFT.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (
        CheckConstraint(
            _check("status", _values(EpisodeStatus)),
            name="ck_episodes_status",
        ),
        CheckConstraint(
            "(slot = 'morning' AND sort_order = 1) OR "
            "(slot = 'noon' AND sort_order = 2) OR "
            "(slot = 'evening' AND sort_order = 3)",
            name="ck_episodes_slot_order",
        ),
        UniqueConstraint("production_run_id", "slot", name="uq_episodes_run_slot"),
        UniqueConstraint(
            "production_run_id",
            "sort_order",
            name="uq_episodes_run_order",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA_NAME}.production_runs.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    slot: Mapped[str] = mapped_column(String(16), nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    script_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    prompt_overrides_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=EpisodeStatus.PLANNED.value,
    )
    selected_video_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA_NAME}.assets.id",
            name="fk_episodes_selected_video_asset",
            use_alter=True,
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        CheckConstraint(
            _check("kind", _values(StepKind)),
            name="ck_workflow_steps_kind",
        ),
        CheckConstraint(
            _check("status", _values(StepStatus)),
            name="ck_workflow_steps_status",
        ),
        CheckConstraint("attempt >= 1", name="ck_workflow_steps_attempt"),
        UniqueConstraint("idempotency_key", name="uq_workflow_steps_idempotency"),
        Index(
            "uq_workflow_steps_provider_task",
            "provider_task_id",
            unique=True,
            postgresql_where=text("provider_task_id IS NOT NULL"),
        ),
        Index(
            "ix_workflow_steps_resume",
            "status",
            "kind",
            "created_at",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA_NAME}.production_runs.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    episode_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.episodes.id", ondelete="CASCADE"),
    )
    parent_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.workflow_steps.id", ondelete="SET NULL"),
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=StepStatus.PENDING.value,
    )
    attempt: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    operation_key: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64))
    provider_task_id: Mapped[str | None] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(200))
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class PromptRecord(Base):
    __tablename__ = "prompt_records"
    __table_args__ = (
        CheckConstraint(
            _check("purpose", tuple(item.value for item in PromptPurpose)),
            name="ck_prompt_records_purpose",
        ),
        UniqueConstraint("step_id", "sha256", name="uq_prompt_records_step_hash"),
        Index("ix_prompt_records_sha256", "sha256"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA_NAME}.workflow_steps.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    parent_prompt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.prompt_records.id", ondelete="SET NULL"),
    )
    purpose: Mapped[str] = mapped_column(String(24), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint(
            "scope IN ('canon', 'run', 'episode', 'delivery')",
            name="ck_assets_scope",
        ),
        CheckConstraint(
            "status IN ('candidate', 'approved', 'rejected', 'ready')",
            name="ck_assets_status",
        ),
        Index("ix_assets_sha256_role", "sha256", "role"),
        Index("ix_assets_run_episode_role", "production_run_id", "episode_id", "role"),
        Index(
            "ix_assets_semantic_selection",
            "scope",
            "semantic_key",
            "status",
            "created_at",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    production_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
    )
    episode_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.episodes.id", ondelete="CASCADE"),
    )
    producing_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.workflow_steps.id", ondelete="SET NULL"),
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    semantic_key: Mapped[str | None] = mapped_column(String(160))
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    media_type: Mapped[str] = mapped_column(String(32), nullable=False)
    local_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint(
            "source IN ('human', 'ark_visual', 'technical')",
            name="ck_reviews_source",
        ),
        CheckConstraint(
            "decision IN ('pending', 'approved', 'rejected')",
            name="ck_reviews_decision",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA_NAME}.workflow_steps.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.assets.id", ondelete="CASCADE"),
    )
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    warnings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    evidence_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class DeliveryPackage(Base):
    __tablename__ = "delivery_packages"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_delivery_packages_revision"),
        CheckConstraint(
            "status IN ('building', 'delivered', 'failed')",
            name="ck_delivery_packages_status",
        ),
        UniqueConstraint(
            "production_run_id",
            "revision",
            name="uq_delivery_packages_run_revision",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA_NAME}.production_runs.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    local_path: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class DeliveryItem(Base):
    __tablename__ = "delivery_items"
    __table_args__ = (
        CheckConstraint(
            "(slot = 'morning' AND sort_order = 1) OR "
            "(slot = 'noon' AND sort_order = 2) OR "
            "(slot = 'evening' AND sort_order = 3)",
            name="ck_delivery_items_slot_order",
        ),
        UniqueConstraint(
            "delivery_package_id",
            "sort_order",
            name="uq_delivery_items_package_order",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    delivery_package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA_NAME}.delivery_packages.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    episode_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.episodes.id"),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.assets.id"),
        nullable=False,
    )
    slot: Mapped[str] = mapped_column(String(16), nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    filename: Mapped[str] = mapped_column(String(160), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
