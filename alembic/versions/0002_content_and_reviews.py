"""Add content hashes, reference assets, and review decisions.

Revision ID: 0002_content_and_reviews
Revises: 0001_postgresql
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_content_and_reviews"
down_revision: str | Sequence[str] | None = "0001_postgresql"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "cat_video"


def _select_schema() -> None:
    global SCHEMA
    SCHEMA = op.get_context().config.attributes.get("schema", "cat_video")


def upgrade() -> None:
    _select_schema()
    op.drop_constraint(
        "uq_episode_variants_episode_id",
        "episode_variants",
        schema=SCHEMA,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_episode_variants_slot_episode",
        "episode_variants",
        ["daily_slot_id", "episode_id"],
        schema=SCHEMA,
    )
    op.add_column(
        "daily_life_packs",
        sa.Column(
            "content_hash",
            sa.String(length=64),
            nullable=False,
            server_default="0" * 64,
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "daily_life_packs",
        sa.Column(
            "source_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema=SCHEMA,
    )
    op.alter_column(
        "daily_life_packs",
        "content_hash",
        server_default=None,
        schema=SCHEMA,
    )
    op.alter_column(
        "daily_life_packs",
        "source_json",
        server_default=None,
        schema=SCHEMA,
    )
    op.add_column(
        "generation_jobs",
        sa.Column(
            "response_snapshot_json",
            postgresql.JSONB(),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "media_assets",
        sa.Column(
            "review_status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "media_assets",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_media_assets_review_status",
        "media_assets",
        "review_status IN ('pending', 'approved', 'rejected')",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "ck_media_assets_duration",
        "media_assets",
        schema=SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        "ck_media_assets_duration",
        "media_assets",
        "duration_ms IS NULL OR duration_ms >= 0",
        schema=SCHEMA,
    )

    op.create_table(
        "reference_assets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("asset_id", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("version_id", sa.String(length=160), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('person', 'cat', 'style')",
            name="ck_reference_assets_role",
        ),
        sa.CheckConstraint(
            "status IN ('candidate', 'approved', 'rejected', 'retired')",
            name="ck_reference_assets_status",
        ),
        sa.CheckConstraint(
            "byte_size >= 1",
            name="ck_reference_assets_byte_size",
        ),
        sa.CheckConstraint("width >= 1", name="ck_reference_assets_width"),
        sa.CheckConstraint("height >= 1", name="ck_reference_assets_height"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "asset_id",
            name="uq_reference_assets_asset_id",
        ),
        sa.UniqueConstraint(
            "storage_path",
            name="uq_reference_assets_storage_path",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_reference_assets_sha256",
        "reference_assets",
        ["sha256"],
        schema=SCHEMA,
    )

    op.create_table(
        "review_decisions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("reference_asset_id", sa.UUID()),
        sa.Column("media_asset_id", sa.UUID()),
        sa.Column("review_type", sa.String(length=16), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(reference_asset_id IS NOT NULL AND media_asset_id IS NULL) OR "
            "(reference_asset_id IS NULL AND media_asset_id IS NOT NULL)",
            name="ck_review_decisions_exactly_one_subject",
        ),
        sa.CheckConstraint(
            "review_type IN ('canon', 'keyframe', 'content')",
            name="ck_review_decisions_type",
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')",
            name="ck_review_decisions_decision",
        ),
        sa.ForeignKeyConstraint(
            ["reference_asset_id"],
            [f"{SCHEMA}.reference_assets.id"],
        ),
        sa.ForeignKeyConstraint(
            ["media_asset_id"],
            [f"{SCHEMA}.media_assets.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_review_decisions_reference_asset",
        "review_decisions",
        ["reference_asset_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_review_decisions_media_asset",
        "review_decisions",
        ["media_asset_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    _select_schema()
    op.drop_table("review_decisions", schema=SCHEMA)
    op.drop_table("reference_assets", schema=SCHEMA)
    op.drop_constraint(
        "ck_media_assets_review_status",
        "media_assets",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_column("media_assets", "approved_at", schema=SCHEMA)
    op.drop_column("media_assets", "review_status", schema=SCHEMA)
    op.drop_column("generation_jobs", "response_snapshot_json", schema=SCHEMA)
    op.drop_constraint(
        "ck_media_assets_duration",
        "media_assets",
        schema=SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        "ck_media_assets_duration",
        "media_assets",
        "duration_ms IS NULL OR duration_ms BETWEEN 8000 AND 15000",
        schema=SCHEMA,
    )
    op.drop_column("daily_life_packs", "source_json", schema=SCHEMA)
    op.drop_column("daily_life_packs", "content_hash", schema=SCHEMA)
    op.drop_constraint(
        "uq_episode_variants_slot_episode",
        "episode_variants",
        schema=SCHEMA,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_episode_variants_episode_id",
        "episode_variants",
        ["episode_id"],
        schema=SCHEMA,
    )
