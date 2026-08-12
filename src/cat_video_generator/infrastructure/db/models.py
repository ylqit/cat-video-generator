"""V5场景、视频片段、造型与相对资产存储的PostgreSQL模型。"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
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
    PromptPurpose,
    RunStatus,
    SceneStatus,
    ShotStatus,
    StepKind,
    StepStatus,
)

SCHEMA_NAME = "cat_video"


def _values(items: type) -> tuple[str, ...]:
    return tuple(item.value for item in items)


def _check(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class Base(DeclarativeBase):
    """所有数据库表的元数据根。"""


class ProductionRun(Base):
    __tablename__ = "production_runs"
    __table_args__ = (
        CheckConstraint(_check("status", _values(RunStatus)), name="ck_production_runs_status"),
        CheckConstraint("contract_version = 5", name="ck_production_runs_contract_version"),
        Index("ix_production_runs_queue", "status", "content_date", "created_at"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    content_date: Mapped[date] = mapped_column(Date, nullable=False)
    contract_version: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=CURRENT_CONTRACT_VERSION,
        server_default=text(str(CURRENT_CONTRACT_VERSION)),
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=RunStatus.ACTIVE.value)
    default_reference_bindings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    current_visual_profile_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA_NAME}.visual_profile_revisions.id",
            name="fk_production_runs_visual_profile_revision",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    selected_sequence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA_NAME}.video_sequences.id",
            name="fk_production_runs_selected_sequence",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class VisualProfileRevision(Base):
    __tablename__ = "visual_profile_revisions"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_visual_profile_revisions_revision"),
        UniqueConstraint(
            "production_run_id",
            "revision",
            name="uq_visual_profile_revisions_run_revision",
        ),
        UniqueConstraint(
            "production_run_id",
            "profile_hash",
            name="uq_visual_profile_revisions_run_hash",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_profile_id: Mapped[str] = mapped_column(String(80), nullable=False)
    person_identity: Mapped[str] = mapped_column(Text, nullable=False)
    person_hair: Mapped[str] = mapped_column(Text, nullable=False)
    person_body: Mapped[str] = mapped_column(Text, nullable=False)
    cat_identity: Mapped[str] = mapped_column(Text, nullable=False)
    style_positive_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    style_negative_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    reference_bindings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    reference_snapshot_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Scene(Base):
    __tablename__ = "scenes"
    __table_args__ = (
        CheckConstraint(_check("status", _values(SceneStatus)), name="ck_scenes_status"),
        CheckConstraint("sort_order >= 1", name="ck_scenes_sort_order"),
        CheckConstraint(
            "(story_mode = 'single' AND target_shot_count = 1) OR "
            "(story_mode = 'multi' AND target_shot_count BETWEEN 2 AND 6)",
            name="ck_scenes_story_shape",
        ),
        UniqueConstraint("production_run_id", "sort_order", name="uq_scenes_run_order"),
        Index("ix_scenes_run_status", "production_run_id", "status", "sort_order"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    chapter_label: Mapped[str | None] = mapped_column(String(80))
    context_note: Mapped[str | None] = mapped_column(Text)
    story_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="single", server_default="single"
    )
    target_shot_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    look_plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    look_draft_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    look_draft_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    selected_look_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA_NAME}.assets.id",
            name="fk_scenes_selected_look_asset",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=SceneStatus.DRAFT.value)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ShotCard(Base):
    __tablename__ = "shot_cards"
    __table_args__ = (
        CheckConstraint(_check("status", _values(ShotStatus)), name="ck_shot_cards_status"),
        CheckConstraint("sort_order >= 1", name="ck_shot_cards_sort_order"),
        CheckConstraint("duration_seconds BETWEEN 8 AND 15", name="ck_shot_cards_duration"),
        CheckConstraint(
            "anchor_mode IN ('text_only', 'existing', 'generate')",
            name="ck_shot_cards_anchor_mode",
        ),
        UniqueConstraint("scene_id", "sort_order", name="uq_shot_cards_scene_order"),
        Index("ix_shot_cards_scene_status", "scene_id", "status", "sort_order"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scene_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.scenes.id", ondelete="CASCADE"),
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=8)
    anchor_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="text_only")
    reference_bindings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    inherit_project_references: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    use_scene_look: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    selected_anchor_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA_NAME}.assets.id",
            name="fk_shot_cards_selected_anchor",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    selected_video_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA_NAME}.assets.id",
            name="fk_shot_cards_selected_video",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ShotStatus.READY.value)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
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
        CheckConstraint(_check("kind", _values(StepKind)), name="ck_workflow_steps_kind"),
        CheckConstraint(_check("status", _values(StepStatus)), name="ck_workflow_steps_status"),
        CheckConstraint("attempt >= 1", name="ck_workflow_steps_attempt"),
        UniqueConstraint("idempotency_key", name="uq_workflow_steps_idempotency"),
        Index(
            "uq_workflow_steps_provider_task",
            "provider_task_id",
            unique=True,
            postgresql_where=text("provider_task_id IS NOT NULL"),
        ),
        Index("ix_workflow_steps_resume", "status", "kind", "created_at"),
        Index(
            "uq_workflow_steps_shot_attempt",
            "shot_card_id",
            "operation_key",
            "attempt",
            unique=True,
            postgresql_where=text("shot_card_id IS NOT NULL"),
        ),
        Index(
            "uq_workflow_steps_scene_attempt",
            "scene_id",
            "operation_key",
            "attempt",
            unique=True,
            postgresql_where=text("scene_id IS NOT NULL AND shot_card_id IS NULL"),
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    scene_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.scenes.id", ondelete="CASCADE")
    )
    shot_card_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.shot_cards.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=StepStatus.PENDING.value
    )
    attempt: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    operation_key: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64))
    provider_task_id: Mapped[str | None] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(200))
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
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

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.workflow_steps.id", ondelete="CASCADE"),
        nullable=False,
    )
    purpose: Mapped[str] = mapped_column(String(24), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint(
            "scope IN ('canon', 'project', 'scene', 'shot')",
            name="ck_assets_scope",
        ),
        CheckConstraint(
            "status IN ('candidate', 'approved', 'rejected', 'ready')",
            name="ck_assets_status",
        ),
        Index("ix_assets_sha256_role", "sha256", "role"),
        Index("ix_assets_shot_role", "shot_card_id", "role", "created_at"),
        Index("ix_assets_semantic_selection", "scope", "semantic_key", "status", "created_at"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE")
    )
    scene_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.scenes.id", ondelete="CASCADE")
    )
    shot_card_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.shot_cards.id", ondelete="CASCADE")
    )
    producing_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.workflow_steps.id", ondelete="SET NULL")
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    semantic_key: Mapped[str | None] = mapped_column(String(160))
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    media_type: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class VideoSequence(Base):
    __tablename__ = "video_sequences"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_video_sequences_revision"),
        CheckConstraint(
            "status IN ('content_review', 'approved', 'rejected')",
            name="ck_video_sequences_status",
        ),
        CheckConstraint("duration_ms > 0", name="ck_video_sequences_duration"),
        CheckConstraint("audio_policy = 'native_fades'", name="ck_video_sequences_audio_policy"),
        UniqueConstraint("production_run_id", "revision", name="uq_video_sequences_run_revision"),
        Index("ix_video_sequences_run_status", "production_run_id", "status", "revision"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.production_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_sequence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.video_sequences.id", ondelete="SET NULL")
    )
    rendered_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.assets.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    audio_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="native_fades", server_default="native_fades"
    )
    clips_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("source IN ('human', 'ark_visual', 'technical')", name="ck_reviews_source"),
        CheckConstraint(
            "decision IN ('pending', 'approved', 'rejected')", name="ck_reviews_decision"
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.workflow_steps.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.assets.id", ondelete="CASCADE")
    )
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    warnings_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
