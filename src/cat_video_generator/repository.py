from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import DailyLifePack, GenerationJob
from .state import transition_pack


def claim_next_life_pack(
    session: Session,
    *,
    target_date: date | None = None,
) -> DailyLifePack | None:
    """Claim one approved/frozen pack without holding a long external-work transaction."""
    statement = (
        select(DailyLifePack)
        .where(DailyLifePack.status.in_(("approved", "frozen")))
        .order_by(DailyLifePack.date, DailyLifePack.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if target_date is not None:
        statement = statement.where(DailyLifePack.date == target_date)
    pack = session.execute(statement).scalar_one_or_none()
    if pack is None:
        return None
    if pack.status == "approved":
        transition_pack(pack, "frozen")
        pack.frozen_at = datetime.now(UTC)
    if pack.status == "frozen":
        transition_pack(pack, "rendering")
    pack.updated_at = datetime.now(UTC)
    session.flush()
    return pack


def get_or_create_generation_job(
    session: Session,
    *,
    episode_variant_id: uuid.UUID,
    render_revision: int,
    job_type: str,
    clip_index: int,
    normalized_input_hash: str,
    idempotency_key: str,
    provider: str,
    request_snapshot_json: dict[str, Any],
    attempt_no: int = 1,
) -> tuple[GenerationJob, bool]:
    """Insert one billable job intent or return the existing idempotent record."""
    job_id = uuid.uuid4()
    inserted_id = session.execute(
        insert(GenerationJob)
        .values(
            id=job_id,
            episode_variant_id=episode_variant_id,
            render_revision=render_revision,
            job_type=job_type,
            clip_index=clip_index,
            normalized_input_hash=normalized_input_hash,
            idempotency_key=idempotency_key,
            provider=provider,
            status="submitting",
            attempt_no=attempt_no,
            request_snapshot_json=request_snapshot_json,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(GenerationJob.id)
    ).scalar_one_or_none()
    if inserted_id is not None:
        return session.get_one(GenerationJob, inserted_id), True
    existing = session.execute(
        select(GenerationJob).where(
            GenerationJob.idempotency_key == idempotency_key
        )
    ).scalar_one()
    return existing, False
