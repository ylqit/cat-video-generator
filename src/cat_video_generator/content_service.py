from __future__ import annotations

import hashlib
import io
import os
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from PIL import Image
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .contracts import (
    ContentConflictError,
    ContentValidationError,
    canonical_content_hash,
    validate_daily_life_pack,
)
from .models import (
    DailyLifePack,
    DailySlot,
    EpisodeVariant,
    GenerationJob,
    MediaAsset,
    ReferenceAsset,
    ReviewDecision,
    SlotRetryEvent,
)
from .state import (
    transition_pack,
    transition_slot,
    transition_variant,
)

_SLOT_ORDER = {"morning": 1, "noon": 2, "evening": 3}
_IMAGE_EXTENSIONS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}


class ContentNotFoundError(LookupError):
    """Raised when a requested pack or asset does not exist."""


def import_daily_life_pack(
    session: Session,
    value: dict[str, Any],
) -> tuple[DailyLifePack, bool]:
    validate_daily_life_pack(value)
    content_hash = canonical_content_hash(value)
    existing = session.execute(
        select(DailyLifePack).where(
            DailyLifePack.life_pack_id == value["lifePackId"],
            DailyLifePack.plan_revision == value["planRevision"],
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.content_hash == content_hash:
            return existing, False
        raise ContentConflictError(
            "The same lifePackId and planRevision already exist with "
            "different normalized content."
        )

    pack = DailyLifePack(
        life_pack_id=value["lifePackId"],
        date=date.fromisoformat(value["date"]),
        plan_revision=value["planRevision"],
        content_hash=content_hash,
        source_json=value,
        day_context_json=value["dayContext"],
        status="candidate",
        is_degraded=False,
    )
    session.add(pack)
    session.flush()

    fallbacks = {
        episode["episodeId"]: episode for episode in value["fallbackEpisodes"]
    }
    for slot_name, sort_order in _SLOT_ORDER.items():
        episode = value["slots"][slot_name]
        slot = DailySlot(
            daily_life_pack_id=pack.id,
            slot=slot_name,
            sort_order=sort_order,
            status="planned",
        )
        session.add(slot)
        session.flush()
        session.add(
            EpisodeVariant(
                daily_slot_id=slot.id,
                episode_id=episode["episodeId"],
                role="primary",
                episode_spec_json=episode,
                active_render_revision=1,
                status="planned",
            )
        )
        fallback_id = episode["fallbackEpisodeId"]
        if fallback_id is not None:
            fallback = fallbacks[fallback_id]
            session.add(
                EpisodeVariant(
                    daily_slot_id=slot.id,
                    episode_id=fallback_id,
                    role="content_fallback",
                    episode_spec_json=fallback,
                    active_render_revision=1,
                    status="planned",
                )
            )
    session.flush()
    return pack, True


def latest_pack(
    session: Session,
    life_pack_id: str,
    *,
    for_update: bool = False,
) -> DailyLifePack:
    statement = (
        select(DailyLifePack)
        .where(DailyLifePack.life_pack_id == life_pack_id)
        .order_by(desc(DailyLifePack.plan_revision))
        .limit(1)
    )
    if for_update:
        statement = statement.with_for_update()
    pack = session.execute(statement).scalar_one_or_none()
    if pack is None:
        raise ContentNotFoundError(f"LifePack {life_pack_id!r} does not exist.")
    return pack


def approve_daily_life_pack(session: Session, life_pack_id: str) -> DailyLifePack:
    pack = latest_pack(session, life_pack_id, for_update=True)
    if pack.status == "approved":
        return pack
    if pack.status != "candidate":
        raise ContentConflictError(
            f"LifePack {life_pack_id!r} cannot be approved from {pack.status!r}."
        )
    validate_daily_life_pack(pack.source_json)
    if canonical_content_hash(pack.source_json) != pack.content_hash:
        raise ContentConflictError("Stored LifePack content hash no longer matches.")
    transition_pack(pack, "approved")
    pack.approved_at = datetime.now(UTC)
    session.flush()
    return pack


def retry_failed_slot(
    session: Session,
    *,
    life_pack_id: str,
    slot_name: str,
    reason: str,
) -> SlotRetryEvent:
    if slot_name not in _SLOT_ORDER:
        raise ContentValidationError(
            "slot must be morning, noon, or evening."
        )
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise ContentValidationError("Retry reason cannot be empty.")

    pack = latest_pack(session, life_pack_id, for_update=True)
    slot = session.execute(
        select(DailySlot)
        .where(
            DailySlot.daily_life_pack_id == pack.id,
            DailySlot.slot == slot_name,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if slot is None:
        raise ContentNotFoundError(
            f"Slot {slot_name!r} does not exist in LifePack {life_pack_id!r}."
        )
    if slot.status != "failed":
        raise ContentConflictError(
            f"Only a failed Slot can be retried; current status is "
            f"{slot.status!r}."
        )

    failed_variants = session.execute(
        select(EpisodeVariant)
        .where(
            EpisodeVariant.daily_slot_id == slot.id,
            EpisodeVariant.status == "failed",
        )
        .order_by(EpisodeVariant.updated_at.desc())
        .with_for_update()
    ).scalars().all()
    if len(failed_variants) != 1:
        raise ContentConflictError(
            "Retry requires exactly one failed EpisodeVariant in the Slot."
        )
    variant = failed_variants[0]
    terminal_job = session.execute(
        select(GenerationJob)
        .where(
            GenerationJob.episode_variant_id == variant.id,
            GenerationJob.status.in_(("failed", "expired", "cancelled")),
        )
        .order_by(GenerationJob.created_at.desc())
        .with_for_update()
        .limit(1)
    ).scalar_one_or_none()
    if terminal_job is None:
        raise ContentConflictError(
            "Retry requires a failed, expired, or cancelled provider job. "
            "Use reconcile-job for submission_unknown tasks."
        )

    from_revision = variant.active_render_revision
    to_revision = from_revision + 1
    event = SlotRetryEvent(
        daily_slot_id=slot.id,
        episode_variant_id=variant.id,
        generation_job_id=terminal_job.id,
        from_render_revision=from_revision,
        to_render_revision=to_revision,
        reason=normalized_reason,
    )
    session.add(event)
    variant.active_render_revision = to_revision
    variant.render_plan_json = None
    variant.last_error = None
    slot.last_error = None
    transition_variant(variant, "planned")
    transition_slot(slot, "planned")
    if pack.status == "failed":
        transition_pack(pack, "rendering")
    session.flush()
    return event


def import_reference_asset(
    session: Session,
    *,
    asset_root: Path,
    role: str,
    asset_id: str,
    source_path: Path,
    crop_box: tuple[int, int, int, int] | None = None,
) -> tuple[ReferenceAsset, bool]:
    if role not in {"person", "cat", "style"}:
        raise ContentValidationError("Canon role must be person, cat, or style.")
    if not asset_id.strip():
        raise ContentValidationError("asset-id cannot be empty.")
    source = source_path.expanduser().resolve()
    if not source.is_file():
        raise ContentValidationError(f"Canon source file does not exist: {source}")

    with Image.open(source) as image:
        image.load()
        source_width, source_height = image.size
        if crop_box is not None:
            left, top, right, bottom = crop_box
            if not (
                0 <= left < right <= source_width
                and 0 <= top < bottom <= source_height
            ):
                raise ContentValidationError(
                    "crop-box must stay inside the source image and have "
                    "positive width and height."
                )
            normalized = image.crop(crop_box)
            output = io.BytesIO()
            normalized.save(output, format="PNG")
            payload = output.getvalue()
            width, height = normalized.size
            image_format = "PNG"
        else:
            payload = source.read_bytes()
            width, height = image.size
            image_format = image.format or ""
    mime_type = Image.MIME.get(image_format)
    extension = _IMAGE_EXTENSIONS.get(image_format)
    if extension is None or mime_type is None:
        raise ContentValidationError(
            "Canon images must be PNG, JPEG, or WEBP."
        )

    byte_size = len(payload)
    sha256 = hashlib.sha256(payload).hexdigest()
    existing = session.execute(
        select(ReferenceAsset).where(ReferenceAsset.asset_id == asset_id)
    ).scalar_one_or_none()
    if existing is not None:
        if (
            existing.role == role
            and existing.sha256 == sha256
            and existing.version_id == asset_id
        ):
            return existing, False
        raise ContentConflictError(
            f"Reference asset {asset_id!r} already exists with different content."
        )

    destination = (
        asset_root.expanduser().resolve()
        / "reference"
        / "sha256"
        / sha256[:2]
        / f"{sha256}{extension}"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        temporary = destination.with_name(
            f".{destination.name}.part-{uuid.uuid4().hex}"
        )
        try:
            with temporary.open("xb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()

    asset = ReferenceAsset(
        asset_id=asset_id,
        role=role,
        version_id=asset_id,
        storage_path=str(destination),
        sha256=sha256,
        byte_size=byte_size,
        mime_type=mime_type,
        width=width,
        height=height,
        status="candidate",
    )
    session.add(asset)
    session.flush()
    return asset, True


def review_reference_asset(
    session: Session,
    *,
    asset_id: str,
    approve: bool,
    reason: str,
) -> ReferenceAsset:
    asset = session.execute(
        select(ReferenceAsset)
        .where(ReferenceAsset.asset_id == asset_id)
        .with_for_update()
    ).scalar_one_or_none()
    if asset is None:
        raise ContentNotFoundError(f"Reference asset {asset_id!r} does not exist.")
    if asset.status == "retired":
        raise ContentConflictError("A retired reference asset cannot be reviewed.")
    decision = "approved" if approve else "rejected"
    asset.status = decision
    asset.approved_at = datetime.now(UTC) if approve else None
    session.add(
        ReviewDecision(
            reference_asset_id=asset.id,
            review_type="canon",
            decision=decision,
            reason=reason,
        )
    )
    session.flush()
    return asset


def review_media_asset(
    session: Session,
    *,
    asset_id: str,
    approve: bool,
    reason: str,
) -> MediaAsset:
    try:
        media_id = uuid.UUID(asset_id)
    except ValueError as exc:
        raise ContentNotFoundError(
            f"Media asset {asset_id!r} does not exist."
        ) from exc
    asset = session.execute(
        select(MediaAsset)
        .where(MediaAsset.id == media_id)
        .with_for_update()
    ).scalar_one_or_none()
    if asset is None:
        raise ContentNotFoundError(
            f"Media asset {asset_id!r} does not exist."
        )
    if asset.qc_status != "passed":
        raise ContentConflictError(
            "Only a media asset that passed QC can be reviewed."
        )
    target = "approved" if approve else "rejected"
    if asset.review_status == target:
        return asset
    if asset.review_status != "pending":
        raise ContentConflictError(
            f"Media asset was already {asset.review_status!r}."
        )

    variant = session.get_one(EpisodeVariant, asset.episode_variant_id)
    slot = session.get_one(DailySlot, variant.daily_slot_id)
    review_type = (
        "keyframe"
        if asset.asset_kind.startswith("scene_keyframe_")
        else "content"
    )
    asset.review_status = target
    asset.approved_at = datetime.now(UTC) if approve else None
    session.add(
        ReviewDecision(
            media_asset_id=asset.id,
            review_type=review_type,
            decision=target,
            reason=reason,
        )
    )
    if approve and review_type == "content":
        if variant.status == "planned":
            transition_variant(variant, "active")
        if variant.status == "active":
            transition_variant(variant, "ready")
        if slot.status != "content_review":
            raise ContentConflictError(
                f"Final video cannot be approved while Slot is {slot.status!r}."
            )
        transition_slot(slot, "ready")
        slot.selected_variant_id = variant.id
        slot.last_error = None
        variant.last_error = None
        pack = session.get_one(DailyLifePack, slot.daily_life_pack_id)
        session.flush()
        other_slots = session.execute(
            select(DailySlot).where(
                DailySlot.daily_life_pack_id == pack.id
            )
        ).scalars().all()
        if (
            all(item.status == "ready" for item in other_slots)
            and pack.status == "rendering"
        ):
            transition_pack(pack, "ready")
            pack.ready_at = datetime.now(UTC)
        elif (
            all(item.status in {"ready", "failed"} for item in other_slots)
            and any(item.status == "failed" for item in other_slots)
            and pack.status == "rendering"
        ):
            transition_pack(pack, "failed")
    elif not approve:
        variant.active_render_revision += 1
        variant.render_plan_json = None
        variant.last_error = _review_retry_code(reason)
        if slot.status in {"keyframe_review", "content_review"}:
            transition_slot(slot, "planned")
        slot.last_error = variant.last_error
    session.flush()
    return asset


def _review_retry_code(reason: str) -> str:
    normalized = reason.strip().lower().replace("-", "_").replace(" ", "_")
    if "identity" in normalized and "drift" in normalized:
        return "identity_drift"
    if "composition" in normalized and "drift" in normalized:
        return "composition_drift"
    return "review_rejected"
