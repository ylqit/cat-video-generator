"""Add audited operator recovery for terminal generation jobs.

Revision ID: 0003_slot_retry_events
Revises: 0002_content_and_reviews
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_slot_retry_events"
down_revision: str | Sequence[str] | None = "0002_content_and_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "cat_video"


def _select_schema() -> None:
    global SCHEMA
    SCHEMA = op.get_context().config.attributes.get("schema", "cat_video")


def upgrade() -> None:
    _select_schema()
    op.create_table(
        "slot_retry_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("daily_slot_id", sa.UUID(), nullable=False),
        sa.Column("episode_variant_id", sa.UUID(), nullable=False),
        sa.Column("generation_job_id", sa.UUID(), nullable=False),
        sa.Column("from_render_revision", sa.SmallInteger(), nullable=False),
        sa.Column("to_render_revision", sa.SmallInteger(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "from_render_revision >= 1",
            name="ck_slot_retry_events_from_revision",
        ),
        sa.CheckConstraint(
            "to_render_revision = from_render_revision + 1",
            name="ck_slot_retry_events_revision_step",
        ),
        sa.CheckConstraint(
            "length(btrim(reason)) > 0",
            name="ck_slot_retry_events_reason",
        ),
        sa.ForeignKeyConstraint(
            ["daily_slot_id", "episode_variant_id"],
            [
                f"{SCHEMA}.episode_variants.daily_slot_id",
                f"{SCHEMA}.episode_variants.id",
            ],
            name="fk_slot_retry_events_slot_variant",
        ),
        sa.ForeignKeyConstraint(
            ["generation_job_id"],
            [f"{SCHEMA}.generation_jobs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "generation_job_id",
            name="uq_slot_retry_events_generation_job",
        ),
        sa.UniqueConstraint(
            "episode_variant_id",
            "to_render_revision",
            name="uq_slot_retry_events_variant_revision",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_slot_retry_events_slot_created",
        "slot_retry_events",
        ["daily_slot_id", "created_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    _select_schema()
    op.drop_table("slot_retry_events", schema=SCHEMA)
