from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    DailyLifePack,
    DailySlot,
    EpisodeVariant,
    GenerationJob,
    MediaAsset,
)


class PromptQueryError(LookupError):
    """Raised when no unambiguous persisted prompt matches the query."""


def query_prompts(
    session: Session,
    *,
    life_pack_id: str,
    slot_name: str,
    plan_revision: int | None = None,
) -> dict[str, object]:
    pack_statement = (
        select(DailyLifePack)
        .where(DailyLifePack.life_pack_id == life_pack_id)
        .order_by(DailyLifePack.plan_revision.desc())
    )
    if plan_revision is not None:
        pack_statement = pack_statement.where(
            DailyLifePack.plan_revision == plan_revision
        )
    pack = session.execute(pack_statement.limit(1)).scalar_one_or_none()
    if pack is None:
        raise PromptQueryError("The requested LifePack revision does not exist.")
    slot = session.execute(
        select(DailySlot).where(
            DailySlot.daily_life_pack_id == pack.id,
            DailySlot.slot == slot_name,
        )
    ).scalar_one_or_none()
    if slot is None:
        raise PromptQueryError(
            f"Slot {slot_name!r} does not exist in the requested LifePack."
        )
    variants = session.execute(
        select(EpisodeVariant)
        .where(EpisodeVariant.daily_slot_id == slot.id)
        .order_by(EpisodeVariant.role, EpisodeVariant.created_at)
    ).scalars().all()
    if not variants:
        raise PromptQueryError("The requested Slot has no Episode variant.")
    variant = next(
        (
            item
            for item in variants
            if item.id == slot.selected_variant_id
        ),
        next(
            (item for item in variants if item.role == "primary"),
            variants[0],
        ),
    )
    jobs = session.execute(
        select(GenerationJob)
        .where(GenerationJob.episode_variant_id == variant.id)
        .order_by(GenerationJob.created_at)
    ).scalars().all()
    persisted_prompts = []
    for job in jobs:
        snapshot = job.request_snapshot_json
        prompt = snapshot.get("videoPrompt") or snapshot.get("prompt")
        if not isinstance(prompt, str):
            continue
        persisted_prompts.append(
            {
                "jobId": str(job.id),
                "jobType": job.job_type,
                "renderRevision": job.render_revision,
                "providerTaskId": job.provider_task_id,
                "status": job.status,
                "resolution": snapshot.get("resolution"),
                "prompt": prompt,
            }
        )
    render_plan = variant.render_plan_json or {}
    return {
        "lifePackId": pack.life_pack_id,
        "planRevision": pack.plan_revision,
        "slot": slot.slot,
        "episodeId": variant.episode_id,
        "activeRenderRevision": variant.active_render_revision,
        "visualInputMode": render_plan.get("visualInputMode"),
        "finalVideoPrompt": render_plan.get("videoPrompt"),
        "persistedGenerationPrompts": persisted_prompts,
    }


def query_status(
    session: Session,
    life_pack_id: str | None = None,
) -> list[dict[str, object]]:
    statement = select(DailyLifePack).order_by(
        DailyLifePack.date,
        DailyLifePack.created_at,
    )
    if life_pack_id:
        statement = statement.where(
            DailyLifePack.life_pack_id == life_pack_id
        )
    packs = session.execute(statement).scalars().all()
    payload: list[dict[str, object]] = []
    for pack in packs:
        slots = session.execute(
            select(DailySlot)
            .where(DailySlot.daily_life_pack_id == pack.id)
            .order_by(DailySlot.sort_order)
        ).scalars().all()
        slot_payload: list[dict[str, object]] = []
        for slot in slots:
            variants = session.execute(
                select(EpisodeVariant).where(
                    EpisodeVariant.daily_slot_id == slot.id
                )
            ).scalars().all()
            variant_ids = [variant.id for variant in variants]
            jobs = (
                []
                if not variant_ids
                else session.execute(
                    select(GenerationJob)
                    .where(
                        GenerationJob.episode_variant_id.in_(variant_ids)
                    )
                    .order_by(GenerationJob.created_at.desc())
                ).scalars().all()
            )
            assets = (
                []
                if not variant_ids
                else session.execute(
                    select(MediaAsset)
                    .where(MediaAsset.episode_variant_id.in_(variant_ids))
                    .order_by(MediaAsset.created_at.desc())
                ).scalars().all()
            )
            slot_payload.append(
                {
                    "slot": slot.slot,
                    "sortOrder": slot.sort_order,
                    "status": slot.status,
                    "selectedVariantId": (
                        None
                        if slot.selected_variant_id is None
                        else str(slot.selected_variant_id)
                    ),
                    "lastError": slot.last_error,
                    "variants": [
                        {
                            "episodeId": variant.episode_id,
                            "role": variant.role,
                            "status": variant.status,
                            "renderRevision": variant.active_render_revision,
                        }
                        for variant in variants
                    ],
                    "latestJob": (
                        None
                        if not jobs
                        else {
                            "jobType": jobs[0].job_type,
                            "status": jobs[0].status,
                            "providerTaskId": jobs[0].provider_task_id,
                            "errorCode": jobs[0].error_code,
                        }
                    ),
                    "reviewAssets": [
                        {
                            "assetId": str(asset.id),
                            "kind": asset.asset_kind,
                            "qcStatus": asset.qc_status,
                            "reviewStatus": asset.review_status,
                            "path": asset.storage_path,
                        }
                        for asset in assets
                        if asset.review_status == "pending"
                    ],
                    "nextAction": _next_action(
                        pack.life_pack_id,
                        slot,
                        assets,
                    ),
                }
            )
        payload.append(
            {
                "lifePackId": pack.life_pack_id,
                "date": pack.date.isoformat(),
                "planRevision": pack.plan_revision,
                "status": pack.status,
                "degraded": pack.is_degraded,
                "slots": slot_payload,
            }
        )
    return payload


def _next_action(
    life_pack_id: str,
    slot: DailySlot,
    assets: list[MediaAsset],
) -> str:
    if slot.status in {"keyframe_review", "content_review"}:
        pending = next(
            (
                asset
                for asset in assets
                if asset.review_status == "pending"
            ),
            None,
        )
        if pending is not None:
            return (
                f"cvg review {pending.id} --approve "
                '--reason "approved after visual inspection"'
            )
    if slot.status == "ready":
        return "none"
    if slot.status == "failed":
        return "inspect lastError and create a new render revision"
    if slot.status == "planned":
        return (
            f"cvg run-pack {life_pack_id} --slot {slot.slot} "
            "--allow-paid-generation"
        )
    return f"cvg resume {life_pack_id} --allow-paid-generation"
