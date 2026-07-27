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
    ForeignKeyConstraint,
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

SCHEMA_NAME = "cat_video"

LIFE_PACK_STATUSES = (
    "candidate",
    "approved",
    "frozen",
    "rendering",
    "ready",
    "delivered",
    "failed",
)
SLOT_STATUSES = (
    "planned",
    "keyframe_generating",
    "keyframe_review",
    "video_generating",
    "media_qc",
    "content_review",
    "ready",
    "failed",
)
VARIANT_STATUSES = ("planned", "active", "ready", "rejected", "failed")
JOB_STATUSES = (
    "submitting",
    "submission_unknown",
    "queued",
    "running",
    "succeeded",
    "failed",
    "expired",
    "cancelled",
)
ACTIVE_JOB_STATUSES = ("submitting", "submission_unknown", "queued", "running")
QC_STATUSES = ("pending", "passed", "failed")
MEDIA_REVIEW_STATUSES = ("pending", "approved", "rejected")
DELIVERY_STATUSES = ("building", "delivered", "failed")
CONTINUITY_STATUSES = ("proposed", "approved", "applied", "rejected")
REFERENCE_ASSET_ROLES = ("person", "cat", "style")
REFERENCE_ASSET_STATUSES = ("candidate", "approved", "rejected", "retired")
REVIEW_TYPES = ("canon", "keyframe", "content")
REVIEW_DECISIONS = ("approved", "rejected")


def _status_check(column_name: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({quoted})"


class Base(DeclarativeBase):
    pass


class DailyLifePack(Base):
    __tablename__ = "daily_life_packs"
    __table_args__ = (
        CheckConstraint(
            _status_check("status", LIFE_PACK_STATUSES),
            name="ck_daily_life_packs_status",
        ),
        CheckConstraint("plan_revision >= 1", name="ck_daily_life_packs_plan_revision"),
        UniqueConstraint(
            "life_pack_id",
            "plan_revision",
            name="uq_daily_life_packs_life_pack_revision",
        ),
        Index(
            "ix_daily_life_packs_run_next",
            "status",
            "date",
            "created_at",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    life_pack_id: Mapped[str] = mapped_column(String(160), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    plan_revision: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    day_context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="candidate",
    )
    is_degraded: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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


class DailySlot(Base):
    __tablename__ = "daily_slots"
    __table_args__ = (
        CheckConstraint(
            _status_check("status", SLOT_STATUSES),
            name="ck_daily_slots_status",
        ),
        CheckConstraint(
            "sort_order BETWEEN 1 AND 3",
            name="ck_daily_slots_sort_order",
        ),
        CheckConstraint(
            "(slot = 'morning' AND sort_order = 1) OR "
            "(slot = 'noon' AND sort_order = 2) OR "
            "(slot = 'evening' AND sort_order = 3)",
            name="ck_daily_slots_slot_sort_mapping",
        ),
        UniqueConstraint(
            "daily_life_pack_id",
            "slot",
            name="uq_daily_slots_pack_slot",
        ),
        UniqueConstraint(
            "daily_life_pack_id",
            "sort_order",
            name="uq_daily_slots_pack_sort",
        ),
        ForeignKeyConstraint(
            ["id", "selected_variant_id"],
            [
                f"{SCHEMA_NAME}.episode_variants.daily_slot_id",
                f"{SCHEMA_NAME}.episode_variants.id",
            ],
            name="fk_daily_slots_selected_variant_same_slot",
            use_alter=True,
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    daily_life_pack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.daily_life_packs.id"),
        nullable=False,
    )
    slot: Mapped[str] = mapped_column(String(16), nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="planned",
    )
    selected_variant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True)
    )
    last_error: Mapped[str | None] = mapped_column(Text)
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


class EpisodeVariant(Base):
    __tablename__ = "episode_variants"
    __table_args__ = (
        CheckConstraint(
            "role IN ('primary', 'content_fallback')",
            name="ck_episode_variants_role",
        ),
        CheckConstraint(
            _status_check("status", VARIANT_STATUSES),
            name="ck_episode_variants_status",
        ),
        CheckConstraint(
            "active_render_revision >= 1",
            name="ck_episode_variants_render_revision",
        ),
        UniqueConstraint(
            "daily_slot_id",
            "id",
            name="uq_episode_variants_slot_id",
        ),
        UniqueConstraint(
            "daily_slot_id",
            "role",
            name="uq_episode_variants_slot_role",
        ),
        UniqueConstraint(
            "daily_slot_id",
            "episode_id",
            name="uq_episode_variants_slot_episode",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    daily_slot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.daily_slots.id"),
        nullable=False,
    )
    episode_id: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    episode_spec_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    render_plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    active_render_revision: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=1,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="planned",
    )
    last_error: Mapped[str | None] = mapped_column(Text)
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


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        CheckConstraint(
            "render_revision >= 1",
            name="ck_generation_jobs_render_revision",
        ),
        CheckConstraint("clip_index >= 0", name="ck_generation_jobs_clip_index"),
        CheckConstraint("attempt_no >= 1", name="ck_generation_jobs_attempt_no"),
        CheckConstraint(
            _status_check("status", JOB_STATUSES),
            name="ck_generation_jobs_status",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_generation_jobs_idempotency_key",
        ),
        Index(
            "uq_generation_jobs_provider_task",
            "provider_task_id",
            unique=True,
            postgresql_where=text("provider_task_id IS NOT NULL"),
        ),
        Index(
            "ix_generation_jobs_active",
            "status",
            "created_at",
            postgresql_where=text(
                "status IN ('submitting', 'submission_unknown', 'queued', 'running')"
            ),
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    episode_variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.episode_variants.id"),
        nullable=False,
    )
    render_revision: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    clip_index: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    normalized_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_task_id: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="submitting",
    )
    attempt_no: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    request_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    response_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SlotRetryEvent(Base):
    __tablename__ = "slot_retry_events"
    __table_args__ = (
        CheckConstraint(
            "from_render_revision >= 1",
            name="ck_slot_retry_events_from_revision",
        ),
        CheckConstraint(
            "to_render_revision = from_render_revision + 1",
            name="ck_slot_retry_events_revision_step",
        ),
        CheckConstraint(
            "length(btrim(reason)) > 0",
            name="ck_slot_retry_events_reason",
        ),
        ForeignKeyConstraint(
            ["daily_slot_id", "episode_variant_id"],
            [
                f"{SCHEMA_NAME}.episode_variants.daily_slot_id",
                f"{SCHEMA_NAME}.episode_variants.id",
            ],
            name="fk_slot_retry_events_slot_variant",
        ),
        UniqueConstraint(
            "generation_job_id",
            name="uq_slot_retry_events_generation_job",
        ),
        UniqueConstraint(
            "episode_variant_id",
            "to_render_revision",
            name="uq_slot_retry_events_variant_revision",
        ),
        Index(
            "ix_slot_retry_events_slot_created",
            "daily_slot_id",
            "created_at",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    daily_slot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    episode_variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.generation_jobs.id"),
        nullable=False,
    )
    from_render_revision: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
    )
    to_render_revision: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        CheckConstraint(
            _status_check("qc_status", QC_STATUSES),
            name="ck_media_assets_qc_status",
        ),
        CheckConstraint(
            _status_check("review_status", MEDIA_REVIEW_STATUSES),
            name="ck_media_assets_review_status",
        ),
        CheckConstraint("byte_size >= 1", name="ck_media_assets_byte_size"),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_media_assets_duration",
        ),
        UniqueConstraint("storage_path", name="uq_media_assets_storage_path"),
        Index("ix_media_assets_sha256", "sha256"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    episode_variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.episode_variants.id"),
        nullable=False,
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.generation_jobs.id"),
        nullable=False,
    )
    asset_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    container: Mapped[str | None] = mapped_column(String(32))
    video_codec: Mapped[str | None] = mapped_column(String(32))
    audio_codec: Mapped[str | None] = mapped_column(String(32))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    has_audio: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    qc_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
    )
    review_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
    )
    qc_report_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class DeliveryPackage(Base):
    __tablename__ = "delivery_packages"
    __table_args__ = (
        CheckConstraint(
            "delivery_revision >= 1",
            name="ck_delivery_packages_revision",
        ),
        CheckConstraint(
            _status_check("status", DELIVERY_STATUSES),
            name="ck_delivery_packages_status",
        ),
        UniqueConstraint(
            "daily_life_pack_id",
            "delivery_revision",
            name="uq_delivery_packages_pack_revision",
        ),
        UniqueConstraint(
            "manifest_path",
            name="uq_delivery_packages_manifest_path",
        ),
        Index(
            "ix_delivery_packages_revision",
            "delivery_revision",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    daily_life_pack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.daily_life_packs.id"),
        nullable=False,
    )
    delivery_revision: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="building")
    root_path: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_path: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeliveryItem(Base):
    __tablename__ = "delivery_items"
    __table_args__ = (
        CheckConstraint(
            "sort_order BETWEEN 1 AND 3",
            name="ck_delivery_items_sort_order",
        ),
        CheckConstraint(
            "(slot = 'morning' AND sort_order = 1) OR "
            "(slot = 'noon' AND sort_order = 2) OR "
            "(slot = 'evening' AND sort_order = 3)",
            name="ck_delivery_items_slot_sort_mapping",
        ),
        UniqueConstraint(
            "delivery_package_id",
            "slot",
            name="uq_delivery_items_package_slot",
        ),
        {"schema": SCHEMA_NAME},
    )

    delivery_package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.delivery_packages.id"),
        primary_key=True,
    )
    sort_order: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    slot: Mapped[str] = mapped_column(String(16), nullable=False)
    episode_variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.episode_variants.id"),
        nullable=False,
    )
    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.media_assets.id"),
        nullable=False,
    )
    file_name: Mapped[str] = mapped_column(String(128), nullable=False)


class ContinuityEvent(Base):
    __tablename__ = "continuity_events"
    __table_args__ = (
        CheckConstraint(
            "sort_order BETWEEN 1 AND 3",
            name="ck_continuity_events_sort_order",
        ),
        CheckConstraint(
            "scope IN ('day', 'canon')",
            name="ck_continuity_events_scope",
        ),
        CheckConstraint(
            "operation IN ('add', 'replace', 'retire')",
            name="ck_continuity_events_operation",
        ),
        CheckConstraint(
            _status_check("status", CONTINUITY_STATUSES),
            name="ck_continuity_events_status",
        ),
        UniqueConstraint("event_id", name="uq_continuity_events_event_id"),
        Index(
            "ix_continuity_events_delivery_sort",
            "delivery_package_id",
            "sort_order",
        ),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    delivery_package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.delivery_packages.id"),
        nullable=False,
    )
    episode_variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.episode_variants.id"),
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(160), nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str] = mapped_column(String(240), nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="applied")
    reason: Mapped[str] = mapped_column(String(240), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ReferenceAsset(Base):
    __tablename__ = "reference_assets"
    __table_args__ = (
        CheckConstraint(
            _status_check("role", REFERENCE_ASSET_ROLES),
            name="ck_reference_assets_role",
        ),
        CheckConstraint(
            _status_check("status", REFERENCE_ASSET_STATUSES),
            name="ck_reference_assets_status",
        ),
        CheckConstraint("byte_size >= 1", name="ck_reference_assets_byte_size"),
        CheckConstraint("width >= 1", name="ck_reference_assets_width"),
        CheckConstraint("height >= 1", name="ck_reference_assets_height"),
        UniqueConstraint("asset_id", name="uq_reference_assets_asset_id"),
        UniqueConstraint("storage_path", name="uq_reference_assets_storage_path"),
        Index("ix_reference_assets_sha256", "sha256"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    asset_id: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    version_id: Mapped[str] = mapped_column(String(160), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="candidate",
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        CheckConstraint(
            "(reference_asset_id IS NOT NULL AND media_asset_id IS NULL) OR "
            "(reference_asset_id IS NULL AND media_asset_id IS NOT NULL)",
            name="ck_review_decisions_exactly_one_subject",
        ),
        CheckConstraint(
            _status_check("review_type", REVIEW_TYPES),
            name="ck_review_decisions_type",
        ),
        CheckConstraint(
            _status_check("decision", REVIEW_DECISIONS),
            name="ck_review_decisions_decision",
        ),
        Index("ix_review_decisions_reference_asset", "reference_asset_id"),
        Index("ix_review_decisions_media_asset", "media_asset_id"),
        {"schema": SCHEMA_NAME},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    reference_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.reference_assets.id"),
    )
    media_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA_NAME}.media_assets.id"),
    )
    review_type: Mapped[str] = mapped_column(String(16), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
