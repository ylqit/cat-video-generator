from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..ark_provider import ArkMediaProvider
from ..config import RuntimeSettings
from ..models import DailyLifePack, DailySlot, EpisodeVariant
from ..render_plan import compile_render_plan, required_visual_mode
from ..state import (
    transition_pack,
    transition_slot,
    transition_variant,
)
from ..visual_policy import VisualInputMode
from .continuity import select_variant
from .errors import OrchestrationError
from .gateway import ArkGateway
from .jobs import ArkJobExecutor, file_sha256
from .visual_assets import VisualAssetService


class PackGenerationService:
    """Own LifePack/Slot sequencing and delegate external job lifecycles."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: RuntimeSettings,
        *,
        gateway: ArkGateway | None = None,
    ) -> None:
        settings.validate_for_ark_access()
        self._session_factory = session_factory
        self._settings = settings
        ark_gateway = gateway or ArkMediaProvider(settings)
        self._jobs = ArkJobExecutor(session_factory, settings, ark_gateway)
        self._visual_assets = VisualAssetService(
            session_factory,
            settings,
            self._jobs,
        )

    def run_pack(
        self,
        life_pack_id: str,
        *,
        slot_name: str | None = None,
    ) -> dict[str, Any]:
        if slot_name is not None and slot_name not in {
            "morning",
            "noon",
            "evening",
        }:
            raise OrchestrationError("--slot must be morning, noon, or evening.")
        pack_id, slots = self._prepare_pack(life_pack_id, slot_name)
        results: list[dict[str, Any]] = []
        for slot_id in slots:
            try:
                results.append(self._process_slot(pack_id, slot_id))
            except OrchestrationError as exc:
                with self._session_factory() as session:
                    result = _slot_result(
                        session.get_one(DailySlot, slot_id),
                        "error",
                    )
                    result["error"] = str(exc)
                    results.append(result)
        with self._session_factory.begin() as session:
            pack = session.get_one(DailyLifePack, pack_id)
            all_slots = session.execute(
                select(DailySlot).where(
                    DailySlot.daily_life_pack_id == pack.id
                )
            ).scalars().all()
            if (
                all(slot.status == "ready" for slot in all_slots)
                and pack.status == "rendering"
            ):
                transition_pack(pack, "ready")
                pack.ready_at = datetime.now(UTC)
            elif (
                all(slot.status in {"ready", "failed"} for slot in all_slots)
                and any(slot.status == "failed" for slot in all_slots)
                and pack.status == "rendering"
            ):
                transition_pack(pack, "failed")
        return {"lifePackId": life_pack_id, "slots": results}

    def resume(self, life_pack_id: str | None = None) -> dict[str, Any]:
        with self._session_factory() as session:
            statement = (
                select(DailyLifePack)
                .where(DailyLifePack.status == "rendering")
                .order_by(DailyLifePack.date, DailyLifePack.created_at)
            )
            if life_pack_id is not None:
                statement = statement.where(
                    DailyLifePack.life_pack_id == life_pack_id
                )
            pack = session.execute(statement.limit(1)).scalar_one_or_none()
            if pack is None:
                return {"lifePackId": life_pack_id, "resumed": False, "slots": []}
            selected_id = pack.life_pack_id
            slots = session.execute(
                select(DailySlot)
                .where(
                    DailySlot.daily_life_pack_id == pack.id,
                    DailySlot.status.in_(
                        (
                            "keyframe_generating",
                            "keyframe_review",
                            "video_generating",
                            "media_qc",
                            "content_review",
                        )
                    ),
                )
                .order_by(DailySlot.sort_order)
            ).scalars().all()
            pack_id = pack.id
            slot_ids = [slot.id for slot in slots]
        results: list[dict[str, Any]] = []
        for slot_id in slot_ids:
            try:
                results.append(
                    self._process_slot(
                        pack_id,
                        slot_id,
                        resume_only=True,
                    )
                )
            except OrchestrationError as exc:
                with self._session_factory() as session:
                    result = _slot_result(
                        session.get_one(DailySlot, slot_id),
                        "error",
                    )
                    result["error"] = str(exc)
                    results.append(result)
        return {
            "lifePackId": selected_id,
            "resumed": True,
            "slots": results,
        }

    def _prepare_pack(
        self,
        life_pack_id: str,
        slot_name: str | None,
    ) -> tuple[uuid.UUID, list[uuid.UUID]]:
        with self._session_factory.begin() as session:
            pack = session.execute(
                select(DailyLifePack)
                .where(DailyLifePack.life_pack_id == life_pack_id)
                .order_by(DailyLifePack.plan_revision.desc())
                .with_for_update()
                .limit(1)
            ).scalar_one_or_none()
            if pack is None:
                raise OrchestrationError(
                    f"LifePack {life_pack_id!r} does not exist."
                )
            if pack.status == "approved":
                transition_pack(pack, "frozen")
                pack.frozen_at = datetime.now(UTC)
            if pack.status == "frozen":
                transition_pack(pack, "rendering")
            if pack.status not in {"rendering", "ready"}:
                raise OrchestrationError(
                    f"LifePack cannot run from status {pack.status!r}."
                )
            statement = (
                select(DailySlot)
                .where(DailySlot.daily_life_pack_id == pack.id)
                .order_by(DailySlot.sort_order)
            )
            if slot_name is not None:
                statement = statement.where(DailySlot.slot == slot_name)
            slots = session.execute(statement).scalars().all()
            return pack.id, [slot.id for slot in slots]

    def _process_slot(
        self,
        pack_id: uuid.UUID,
        slot_id: uuid.UUID,
        *,
        resume_only: bool = False,
    ) -> dict[str, Any]:
        with self._session_factory.begin() as session:
            slot = session.get_one(DailySlot, slot_id)
            if slot.status == "ready":
                return _slot_result(slot, "already_ready")
            if slot.status == "failed":
                return _slot_result(slot, "failed")
            variant = select_variant(session, pack_id, slot)
            if variant is None:
                return _slot_result(slot, "waiting_for_dependency_review")
            references = self._visual_assets.load_approved_references(
                session,
                variant,
            )
            mode = required_visual_mode(
                variant.episode_spec_json,
                retry_after_identity_or_composition_drift=(
                    variant.last_error
                    in {"identity_drift", "composition_drift"}
                ),
            )
            variant_id = variant.id
            episode = variant.episode_spec_json
            plan_revision = session.get_one(
                DailyLifePack, pack_id
            ).plan_revision
            render_revision = variant.active_render_revision

        if mode is not VisualInputMode.DIRECT_REFERENCES:
            all_approved = self._visual_assets.ensure_keyframes(
                slot_id=slot_id,
                variant_id=variant_id,
                episode=episode,
                render_revision=render_revision,
                reference_paths=references.paths,
                mode=mode,
                resume_only=resume_only,
            )
            if not all_approved:
                with self._session_factory() as session:
                    return _slot_result(
                        session.get_one(DailySlot, slot_id),
                        "awaiting_keyframe_review",
                    )

        with self._session_factory.begin() as session:
            slot = session.get_one(DailySlot, slot_id)
            variant = session.get_one(EpisodeVariant, variant_id)
            scene_assets = self._visual_assets.approved_keyframes(
                session,
                variant_id,
                render_revision,
            )
            ordered_scene_assets = self._visual_assets.ordered_keyframes(
                scene_assets,
                mode,
            )
            plan = compile_render_plan(
                episode,
                plan_revision=plan_revision,
                render_revision=render_revision,
                references=references.plan,
                scene_keyframe_asset_ids=tuple(
                    str(asset.id) for asset in ordered_scene_assets
                ),
                retry_after_identity_or_composition_drift=(
                    variant.last_error
                    in {"identity_drift", "composition_drift"}
                ),
            )
            variant.render_plan_json = plan
            if variant.status == "planned":
                transition_variant(variant, "active")
            if slot.status in {"planned", "keyframe_review"}:
                transition_slot(slot, "video_generating")
            input_paths = (
                references.paths
                if mode is VisualInputMode.DIRECT_REFERENCES
                else tuple(
                    Path(asset.storage_path) for asset in ordered_scene_assets
                )
            )
            request_snapshot = {
                **self._settings.request_profile_snapshot(),
                "model": self._settings.ark_video_model,
                "visualInputMode": plan["visualInputMode"],
                "durationMs": plan["durationMs"],
                "resolution": "720p",
                "ratio": "9:16",
                "generateAudio": True,
                "watermark": False,
                "inputAssetSha256": [
                    file_sha256(path) for path in input_paths
                ],
                "videoPrompt": plan["videoPrompt"],
            }

        asset = self._jobs.ensure_video_asset(
            slot_id=slot_id,
            variant_id=variant_id,
            episode_id=episode["episodeId"],
            render_revision=render_revision,
            prompt=plan["videoPrompt"],
            duration_ms=plan["durationMs"],
            visual_input_mode=plan["visualInputMode"],
            input_paths=input_paths,
            request_snapshot=request_snapshot,
            resume_only=resume_only,
        )
        with self._session_factory() as session:
            slot = session.get_one(DailySlot, slot_id)
            return _slot_result(
                slot,
                (
                    "awaiting_content_review"
                    if asset.review_status == "pending"
                    else "ready"
                ),
            )


def _slot_result(slot: DailySlot, action: str) -> dict[str, Any]:
    return {
        "slot": slot.slot,
        "sortOrder": slot.sort_order,
        "status": slot.status,
        "action": action,
        "selectedVariantId": (
            None
            if slot.selected_variant_id is None
            else str(slot.selected_variant_id)
        ),
        "lastError": slot.last_error,
    }
