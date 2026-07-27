from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DailySlot, EpisodeVariant
from .errors import OrchestrationError


def select_variant(
    session: Session,
    pack_id: uuid.UUID,
    slot: DailySlot,
) -> EpisodeVariant | None:
    variants = session.execute(
        select(EpisodeVariant).where(EpisodeVariant.daily_slot_id == slot.id)
    ).scalars().all()
    primary = next(variant for variant in variants if variant.role == "primary")
    episode = primary.episode_spec_json
    if episode["continuityMode"] != "follows_previous":
        return primary

    dependencies = set(episode["dependsOnEpisodeIds"])
    dependency_rows = session.execute(
        select(EpisodeVariant.episode_id, DailySlot.status)
        .join(DailySlot, EpisodeVariant.daily_slot_id == DailySlot.id)
        .where(
            DailySlot.daily_life_pack_id == pack_id,
            EpisodeVariant.episode_id.in_(dependencies),
        )
    ).all()
    resolution = resolve_continuation(
        episode,
        {
            episode_id: status
            for episode_id, status in dependency_rows
        },
    )
    if resolution == "primary":
        return primary
    if resolution == "wait":
        return None
    fallback = next(
        (
            variant
            for variant in variants
            if variant.role == "content_fallback"
        ),
        None,
    )
    if fallback is None:
        raise OrchestrationError(
            f"Continuation {episode['episodeId']!r} has no persisted fallback."
        )
    return fallback


def resolve_continuation(
    episode: dict[str, Any],
    dependency_slot_statuses: dict[str, str],
) -> str:
    """Choose primary, wait, or fallback from reviewed dependency state."""
    if episode["continuityMode"] != "follows_previous":
        return "primary"
    dependencies = set(episode["dependsOnEpisodeIds"])
    ready = {
        episode_id
        for episode_id, status in dependency_slot_statuses.items()
        if status == "ready"
    }
    if dependencies <= ready:
        return "primary"
    pending_statuses = {
        "planned",
        "keyframe_generating",
        "keyframe_review",
        "video_generating",
        "media_qc",
        "content_review",
    }
    if any(
        dependency_slot_statuses.get(episode_id) in pending_statuses
        for episode_id in dependencies
    ):
        return "wait"
    return "content_fallback"
