"""Create the PostgreSQL persistence model.

Revision ID: 0001_postgresql
Revises:
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_postgresql"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "cat_video"


def _select_schema() -> None:
    global SCHEMA
    SCHEMA = op.get_context().config.attributes.get("schema", "cat_video")


def upgrade() -> None:
    _select_schema()
    op.create_table(
        "daily_life_packs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("life_pack_id", sa.String(length=160), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("plan_revision", sa.SmallInteger(), nullable=False),
        sa.Column("day_context_json", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("is_degraded", sa.Boolean(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("frozen_at", sa.DateTime(timezone=True)),
        sa.Column("ready_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('candidate', 'approved', 'frozen', 'rendering', "
            "'ready', 'delivered', 'failed')",
            name="ck_daily_life_packs_status",
        ),
        sa.CheckConstraint(
            "plan_revision >= 1",
            name="ck_daily_life_packs_plan_revision",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "life_pack_id",
            "plan_revision",
            name="uq_daily_life_packs_life_pack_revision",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_daily_life_packs_run_next",
        "daily_life_packs",
        ["status", "date", "created_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "daily_slots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("daily_life_pack_id", sa.UUID(), nullable=False),
        sa.Column("slot", sa.String(length=16), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("selected_variant_id", sa.UUID()),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(slot = 'morning' AND sort_order = 1) OR "
            "(slot = 'noon' AND sort_order = 2) OR "
            "(slot = 'evening' AND sort_order = 3)",
            name="ck_daily_slots_slot_sort_mapping",
        ),
        sa.CheckConstraint(
            "status IN ('planned', 'keyframe_generating', 'keyframe_review', "
            "'video_generating', 'media_qc', 'content_review', 'ready', 'failed')",
            name="ck_daily_slots_status",
        ),
        sa.CheckConstraint(
            "sort_order BETWEEN 1 AND 3",
            name="ck_daily_slots_sort_order",
        ),
        sa.ForeignKeyConstraint(
            ["daily_life_pack_id"],
            [f"{SCHEMA}.daily_life_packs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "daily_life_pack_id",
            "slot",
            name="uq_daily_slots_pack_slot",
        ),
        sa.UniqueConstraint(
            "daily_life_pack_id",
            "sort_order",
            name="uq_daily_slots_pack_sort",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "delivery_packages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("daily_life_pack_id", sa.UUID(), nullable=False),
        sa.Column("delivery_revision", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("root_path", sa.Text(), nullable=False),
        sa.Column("manifest_path", sa.Text(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('building', 'delivered', 'failed')",
            name="ck_delivery_packages_status",
        ),
        sa.CheckConstraint(
            "delivery_revision >= 1",
            name="ck_delivery_packages_revision",
        ),
        sa.ForeignKeyConstraint(
            ["daily_life_pack_id"],
            [f"{SCHEMA}.daily_life_packs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "daily_life_pack_id",
            "delivery_revision",
            name="uq_delivery_packages_pack_revision",
        ),
        sa.UniqueConstraint(
            "manifest_path",
            name="uq_delivery_packages_manifest_path",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_delivery_packages_revision",
        "delivery_packages",
        ["delivery_revision"],
        schema=SCHEMA,
    )

    op.create_table(
        "episode_variants",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("daily_slot_id", sa.UUID(), nullable=False),
        sa.Column("episode_id", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("episode_spec_json", postgresql.JSONB(), nullable=False),
        sa.Column("render_plan_json", postgresql.JSONB()),
        sa.Column("active_render_revision", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('primary', 'content_fallback')",
            name="ck_episode_variants_role",
        ),
        sa.CheckConstraint(
            "status IN ('planned', 'active', 'ready', 'rejected', 'failed')",
            name="ck_episode_variants_status",
        ),
        sa.CheckConstraint(
            "active_render_revision >= 1",
            name="ck_episode_variants_render_revision",
        ),
        sa.ForeignKeyConstraint(
            ["daily_slot_id"],
            [f"{SCHEMA}.daily_slots.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "daily_slot_id",
            "id",
            name="uq_episode_variants_slot_id",
        ),
        sa.UniqueConstraint(
            "daily_slot_id",
            "role",
            name="uq_episode_variants_slot_role",
        ),
        sa.UniqueConstraint(
            "episode_id",
            name="uq_episode_variants_episode_id",
        ),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_daily_slots_selected_variant_same_slot",
        "daily_slots",
        "episode_variants",
        ["id", "selected_variant_id"],
        ["daily_slot_id", "id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )

    op.create_table(
        "continuity_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.String(length=200), nullable=False),
        sa.Column("delivery_package_id", sa.UUID(), nullable=False),
        sa.Column("episode_variant_id", sa.UUID(), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("entity_id", sa.String(length=160), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("path", sa.String(length=240), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=240), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "operation IN ('add', 'replace', 'retire')",
            name="ck_continuity_events_operation",
        ),
        sa.CheckConstraint(
            "scope IN ('day', 'canon')",
            name="ck_continuity_events_scope",
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'approved', 'applied', 'rejected')",
            name="ck_continuity_events_status",
        ),
        sa.CheckConstraint(
            "sort_order BETWEEN 1 AND 3",
            name="ck_continuity_events_sort_order",
        ),
        sa.ForeignKeyConstraint(
            ["delivery_package_id"],
            [f"{SCHEMA}.delivery_packages.id"],
        ),
        sa.ForeignKeyConstraint(
            ["episode_variant_id"],
            [f"{SCHEMA}.episode_variants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id",
            name="uq_continuity_events_event_id",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_continuity_events_delivery_sort",
        "continuity_events",
        ["delivery_package_id", "sort_order"],
        schema=SCHEMA,
    )

    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("episode_variant_id", sa.UUID(), nullable=False),
        sa.Column("render_revision", sa.SmallInteger(), nullable=False),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("clip_index", sa.SmallInteger(), nullable=False),
        sa.Column("normalized_input_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_task_id", sa.String(length=200)),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_no", sa.SmallInteger(), nullable=False),
        sa.Column("request_snapshot_json", postgresql.JSONB(), nullable=False),
        sa.Column("error_code", sa.String(length=120)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("downloaded_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('submitting', 'submission_unknown', 'queued', 'running', "
            "'succeeded', 'failed', 'expired', 'cancelled')",
            name="ck_generation_jobs_status",
        ),
        sa.CheckConstraint(
            "attempt_no >= 1",
            name="ck_generation_jobs_attempt_no",
        ),
        sa.CheckConstraint(
            "clip_index >= 0",
            name="ck_generation_jobs_clip_index",
        ),
        sa.CheckConstraint(
            "render_revision >= 1",
            name="ck_generation_jobs_render_revision",
        ),
        sa.ForeignKeyConstraint(
            ["episode_variant_id"],
            [f"{SCHEMA}.episode_variants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_generation_jobs_idempotency_key",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_generation_jobs_active",
        "generation_jobs",
        ["status", "created_at"],
        schema=SCHEMA,
        postgresql_where=sa.text(
            "status IN ('submitting', 'submission_unknown', 'queued', 'running')"
        ),
    )
    op.create_index(
        "uq_generation_jobs_provider_task",
        "generation_jobs",
        ["provider_task_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("provider_task_id IS NOT NULL"),
    )

    op.create_table(
        "media_assets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("episode_variant_id", sa.UUID(), nullable=False),
        sa.Column("generation_job_id", sa.UUID(), nullable=False),
        sa.Column("asset_kind", sa.String(length=48), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("container", sa.String(length=32)),
        sa.Column("video_codec", sa.String(length=32)),
        sa.Column("audio_codec", sa.String(length=32)),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("has_audio", sa.Boolean(), nullable=False),
        sa.Column("qc_status", sa.String(length=16), nullable=False),
        sa.Column("qc_report_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "qc_status IN ('pending', 'passed', 'failed')",
            name="ck_media_assets_qc_status",
        ),
        sa.CheckConstraint(
            "byte_size >= 1",
            name="ck_media_assets_byte_size",
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms BETWEEN 8000 AND 15000",
            name="ck_media_assets_duration",
        ),
        sa.ForeignKeyConstraint(
            ["episode_variant_id"],
            [f"{SCHEMA}.episode_variants.id"],
        ),
        sa.ForeignKeyConstraint(
            ["generation_job_id"],
            [f"{SCHEMA}.generation_jobs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "storage_path",
            name="uq_media_assets_storage_path",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_media_assets_sha256",
        "media_assets",
        ["sha256"],
        schema=SCHEMA,
    )

    op.create_table(
        "delivery_items",
        sa.Column("delivery_package_id", sa.UUID(), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False),
        sa.Column("slot", sa.String(length=16), nullable=False),
        sa.Column("episode_variant_id", sa.UUID(), nullable=False),
        sa.Column("media_asset_id", sa.UUID(), nullable=False),
        sa.Column("file_name", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "(slot = 'morning' AND sort_order = 1) OR "
            "(slot = 'noon' AND sort_order = 2) OR "
            "(slot = 'evening' AND sort_order = 3)",
            name="ck_delivery_items_slot_sort_mapping",
        ),
        sa.CheckConstraint(
            "sort_order BETWEEN 1 AND 3",
            name="ck_delivery_items_sort_order",
        ),
        sa.ForeignKeyConstraint(
            ["delivery_package_id"],
            [f"{SCHEMA}.delivery_packages.id"],
        ),
        sa.ForeignKeyConstraint(
            ["episode_variant_id"],
            [f"{SCHEMA}.episode_variants.id"],
        ),
        sa.ForeignKeyConstraint(
            ["media_asset_id"],
            [f"{SCHEMA}.media_assets.id"],
        ),
        sa.PrimaryKeyConstraint("delivery_package_id", "sort_order"),
        sa.UniqueConstraint(
            "delivery_package_id",
            "slot",
            name="uq_delivery_items_package_slot",
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    _select_schema()
    op.drop_constraint(
        "fk_daily_slots_selected_variant_same_slot",
        "daily_slots",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_table("delivery_items", schema=SCHEMA)
    op.drop_table("media_assets", schema=SCHEMA)
    op.drop_table("generation_jobs", schema=SCHEMA)
    op.drop_table("continuity_events", schema=SCHEMA)
    op.drop_table("episode_variants", schema=SCHEMA)
    op.drop_table("delivery_packages", schema=SCHEMA)
    op.drop_table("daily_slots", schema=SCHEMA)
    op.drop_table("daily_life_packs", schema=SCHEMA)
