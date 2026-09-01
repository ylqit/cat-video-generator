"""Creator-only baseline.

Revision ID: 0001_creator_core_baseline
Revises:
Create Date: 2026-09-01
"""

from __future__ import annotations

from alembic import op
from cat_video_generator.infrastructure.db.models import Base

revision = "0001_creator_core_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=False)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
