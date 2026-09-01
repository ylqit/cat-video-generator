"""Creator-only PostgreSQL schema.

These seven tables are the complete online business model. Creative text is
kept flexible, while paid execution inputs and task state remain strict and
auditable.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA_NAME = "cat_video"


class Base(DeclarativeBase):
    pass


class CreatorProject(Base):
    __tablename__ = "creator_projects"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_creator_projects_version"),
        CheckConstraint(
            "target_duration_seconds BETWEEN 1 AND 360", name="ck_creator_projects_duration"
        ),
        CheckConstraint(
            "aspect_ratio IN ('9:16', '16:9', '1:1')", name="ck_creator_projects_aspect_ratio"
        ),
        CheckConstraint(
            "quality_tier IN ('quick', 'standard', 'quality')",
            name="ck_creator_projects_quality_tier",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    content_date: Mapped[date] = mapped_column(Date, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    brief_body: Mapped[str] = mapped_column(Text, nullable=False)
    story_candidates_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    current_story_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    target_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(8), nullable=False)
    quality_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    reference_bindings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CreatorShot(Base):
    __tablename__ = "creator_shots"
    __table_args__ = (
        UniqueConstraint("project_id", "sort_order", name="uq_creator_shots_order"),
        CheckConstraint("sort_order BETWEEN 1 AND 6", name="ck_creator_shots_order"),
        CheckConstraint("version >= 1", name="ck_creator_shots_version"),
        CheckConstraint("duration_seconds BETWEEN 1 AND 60", name="ck_creator_shots_duration"),
        Index("ix_creator_shots_project", "project_id"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.creator_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    scene_label: Mapped[str | None] = mapped_column(String(160))
    reference_bindings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    prompt_draft: Mapped[str | None] = mapped_column(Text)
    selected_video_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class GenerationSnapshot(Base):
    __tablename__ = "generation_snapshots"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('story_text', 'image', 'video', 'video_edit', 'composition')",
            name="ck_generation_snapshots_kind",
        ),
        Index("ix_generation_snapshots_project", "project_id", "created_at"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.creator_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    creator_shot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.creator_shots.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    ordered_references_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    provider_config_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    estimated_cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationTask(Base):
    __tablename__ = "generation_tasks"
    __table_args__ = (
        UniqueConstraint("generation_snapshot_id", name="uq_generation_tasks_snapshot"),
        UniqueConstraint("idempotency_key", name="uq_generation_tasks_idempotency"),
        CheckConstraint("attempt >= 1", name="ck_generation_tasks_attempt"),
        Index("ix_generation_tasks_claim", "status", "next_attempt_at", "created_at"),
        Index("ix_generation_tasks_project", "project_id", "created_at"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.creator_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    creator_shot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.creator_shots.id", ondelete="CASCADE")
    )
    generation_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.generation_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="local_queued")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    provider_task_id: Mapped[str | None] = mapped_column(String(240))
    provider_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_submitted"
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(160))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class GenerationTaskEvent(Base):
    __tablename__ = "generation_task_events"
    __table_args__ = (
        Index("ix_generation_task_events_task", "task_id", "id"),
        Index("ix_generation_task_events_project", "project_id", "id"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.generation_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.creator_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        CheckConstraint("media_type IN ('image', 'video', 'audio')", name="ck_media_assets_type"),
        CheckConstraint(
            "status IN ('candidate', 'approved', 'rejected', 'ready')",
            name="ck_media_assets_status",
        ),
        UniqueConstraint("sha256", "role", "project_id", name="uq_media_assets_content_role"),
        Index("ix_media_assets_project", "project_id", "created_at"),
        Index("ix_media_assets_shot", "creator_shot_id", "created_at"),
        Index("ix_media_assets_semantic", "semantic_key"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.creator_projects.id", ondelete="CASCADE")
    )
    creator_shot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.creator_shots.id", ondelete="CASCADE")
    )
    generation_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.generation_snapshots.id", ondelete="SET NULL"),
    )
    generation_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.generation_tasks.id", ondelete="SET NULL")
    )
    role: Mapped[str] = mapped_column(String(80), nullable=False)
    semantic_key: Mapped[str | None] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="candidate")
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CreatorTimeline(Base):
    __tablename__ = "creator_timelines"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_creator_timelines_project"),
        CheckConstraint("version >= 1", name="ck_creator_timelines_version"),
        CheckConstraint(
            "status IN ('draft', 'rendering', 'awaiting_selection', 'approved', 'failed')",
            name="ck_creator_timelines_status",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.creator_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    clips_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    final_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA_NAME}.media_assets.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
