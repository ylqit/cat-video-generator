from __future__ import annotations

import hashlib
import random
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .ark_provider import (
    ArkImageResult,
    ArkMediaProvider,
    ArkProviderError,
    ArkVideoSubmission,
    ArkVideoTask,
)
from .config import RuntimeSettings
from .contracts import canonical_content_hash
from .media import (
    MediaProcessingError,
    download_to_content_address,
    inspect_image,
    probe_video,
)
from .models import (
    DailyLifePack,
    DailySlot,
    EpisodeVariant,
    GenerationJob,
    MediaAsset,
    ReferenceAsset,
)
from .render_plan import (
    VisualReferences,
    compile_keyframe_prompt,
    compile_render_plan,
    required_visual_mode,
)
from .repository import get_or_create_generation_job
from .state import (
    transition_job,
    transition_pack,
    transition_slot,
    transition_variant,
)
from .visual_policy import VisualInputMode


class ProviderBoundary(Protocol):
    def generate_keyframe(
        self,
        *,
        prompt: str,
        reference_paths: tuple[Path, ...],
    ) -> ArkImageResult: ...

    def create_video(
        self,
        *,
        prompt: str,
        duration_ms: int,
        visual_input_mode: str,
        input_paths: tuple[Path, ...],
    ) -> ArkVideoSubmission: ...

    def get_video_task(self, task_id: str) -> ArkVideoTask: ...


class OrchestrationError(RuntimeError):
    """Raised when a pack cannot safely advance through the real Ark workflow."""


class ArkOrchestrator:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: RuntimeSettings,
        *,
        provider: ProviderBoundary | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._provider = provider or ArkMediaProvider(settings)

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
                    slot = session.get_one(DailySlot, slot_id)
                    result = _slot_result(slot, "error")
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
            variant = _select_variant(session, pack_id, slot)
            if variant is None:
                return _slot_result(slot, "waiting_for_dependency_review")
            references = _load_approved_references(session, variant)
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
            reference_paths = references["paths"]
            visual_references = references["plan"]

        if mode is not VisualInputMode.DIRECT_REFERENCES:
            keyframe_result = self._ensure_keyframes(
                slot_id=slot_id,
                variant_id=variant_id,
                episode=episode,
                render_revision=render_revision,
                reference_paths=reference_paths,
                mode=mode,
                resume_only=resume_only,
            )
            if not keyframe_result["allApproved"]:
                with self._session_factory() as session:
                    return _slot_result(
                        session.get_one(DailySlot, slot_id),
                        "awaiting_keyframe_review",
                    )

        with self._session_factory.begin() as session:
            slot = session.get_one(DailySlot, slot_id)
            variant = session.get_one(EpisodeVariant, variant_id)
            scene_assets = _approved_keyframes(
                session, variant_id, render_revision
            )
            scene_ids = tuple(
                str(asset.id)
                for asset in _ordered_keyframes(scene_assets, mode)
            )
            plan = compile_render_plan(
                episode,
                plan_revision=plan_revision,
                render_revision=render_revision,
                references=visual_references,
                scene_keyframe_asset_ids=scene_ids,
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
                reference_paths
                if mode is VisualInputMode.DIRECT_REFERENCES
                else tuple(
                    Path(asset.storage_path)
                    for asset in _ordered_keyframes(scene_assets, mode)
                )
            )
            request_snapshot = {
                "provider": "volcengine-ark",
                "model": self._settings.ark_video_model,
                "visualInputMode": plan["visualInputMode"],
                "durationMs": plan["durationMs"],
                "resolution": "720p",
                "ratio": "9:16",
                "generateAudio": True,
                "watermark": False,
                "inputAssetSha256": [
                    _sha256_file(path) for path in input_paths
                ],
                "videoPrompt": plan["videoPrompt"],
            }
            normalized_hash = canonical_content_hash(request_snapshot)
            idempotency_key = _job_key(
                episode["episodeId"],
                render_revision,
                "video",
                0,
                normalized_hash,
            )
            if resume_only:
                job = session.execute(
                    select(GenerationJob).where(
                        GenerationJob.idempotency_key == idempotency_key
                    )
                ).scalar_one_or_none()
                if job is None:
                    raise OrchestrationError(
                        "Resume found no existing Ark video job and will not "
                        "create a new paid submission."
                    )
                created = False
            else:
                job, created = get_or_create_generation_job(
                    session,
                    episode_variant_id=variant.id,
                    render_revision=render_revision,
                    job_type="video",
                    clip_index=0,
                    normalized_input_hash=normalized_hash,
                    idempotency_key=idempotency_key,
                    provider="volcengine-ark",
                    request_snapshot_json=request_snapshot,
                )
            job_id = job.id
            job_status = job.status

        if created:
            try:
                submission = self._submit_video_with_retry(
                    job_id=job_id,
                    prompt=plan["videoPrompt"],
                    duration_ms=plan["durationMs"],
                    visual_input_mode=plan["visualInputMode"],
                    input_paths=input_paths,
                )
            except OrchestrationError:
                self._fail_slot_for_terminal_job(
                    slot_id=slot_id,
                    variant_id=variant_id,
                    job_id=job_id,
                )
                raise
            with self._session_factory.begin() as session:
                job = session.get_one(GenerationJob, job_id)
                job.provider_task_id = submission.task_id
                job.submitted_at = datetime.now(UTC)
                transition_job(job, "queued")
            job_status = "queued"

        if job_status == "submission_unknown":
            raise OrchestrationError(
                "Ark video submission outcome is unknown. Do not resubmit; "
                "reconcile the task in Ark before continuing."
            )
        if job_status in {"failed", "expired", "cancelled"}:
            raise OrchestrationError(
                f"Existing video job is terminal with status {job_status!r}; "
                "create a new render revision before retrying."
            )

        try:
            asset = self._finish_video_job(
                job_id=job_id,
                variant_id=variant_id,
                slot_id=slot_id,
                expected_duration_ms=plan["durationMs"],
            )
        except MediaProcessingError as exc:
            raise OrchestrationError(str(exc)) from exc
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

    def _ensure_keyframes(
        self,
        *,
        slot_id: uuid.UUID,
        variant_id: uuid.UUID,
        episode: dict[str, Any],
        render_revision: int,
        reference_paths: tuple[Path, ...],
        mode: VisualInputMode,
        resume_only: bool,
    ) -> dict[str, bool]:
        required_roles = (
            ("first", "last")
            if mode is VisualInputMode.GENERATED_FIRST_LAST_FRAMES
            else ("first",)
        )
        for clip_index, frame_role in enumerate(required_roles):
            with self._session_factory.begin() as session:
                existing = session.execute(
                    select(MediaAsset)
                    .join(
                        GenerationJob,
                        MediaAsset.generation_job_id == GenerationJob.id,
                    )
                    .where(
                        MediaAsset.episode_variant_id == variant_id,
                        MediaAsset.asset_kind == f"scene_keyframe_{frame_role}",
                        MediaAsset.qc_status == "passed",
                        GenerationJob.render_revision == render_revision,
                    )
                ).scalars().all()
                approved = next(
                    (asset for asset in existing if asset.review_status == "approved"),
                    None,
                )
                if approved is not None:
                    continue
                if existing:
                    continue
                slot = session.get_one(DailySlot, slot_id)
                if slot.status == "planned":
                    transition_slot(slot, "keyframe_generating")
                prompt = compile_keyframe_prompt(
                    episode,
                    frame_role=frame_role,
                )
                request_snapshot = {
                    "provider": "volcengine-ark",
                    "model": self._settings.ark_image_model,
                    "frameRole": frame_role,
                    "size": "2K",
                    "outputFormat": "png",
                    "watermark": False,
                    "inputAssetSha256": [
                        _sha256_file(path) for path in reference_paths
                    ],
                    "prompt": prompt,
                }
                normalized_hash = canonical_content_hash(request_snapshot)
                idempotency_key = _job_key(
                    episode["episodeId"],
                    render_revision,
                    f"keyframe_{frame_role}",
                    clip_index,
                    normalized_hash,
                )
                if resume_only:
                    job = session.execute(
                        select(GenerationJob).where(
                            GenerationJob.idempotency_key == idempotency_key
                        )
                    ).scalar_one_or_none()
                    if job is None:
                        raise OrchestrationError(
                            "Resume found no existing Ark keyframe job and "
                            "will not create a new paid submission."
                        )
                    created = False
                else:
                    job, created = get_or_create_generation_job(
                        session,
                        episode_variant_id=variant_id,
                        render_revision=render_revision,
                        job_type=f"keyframe_{frame_role}",
                        clip_index=clip_index,
                        normalized_input_hash=normalized_hash,
                        idempotency_key=idempotency_key,
                        provider="volcengine-ark",
                        request_snapshot_json=request_snapshot,
                    )
                job_id = job.id
            if not created:
                continue
            try:
                result = self._submit_keyframe_with_retry(
                    job_id=job_id,
                    prompt=prompt,
                    reference_paths=reference_paths,
                )
            except OrchestrationError:
                self._fail_slot_for_terminal_job(
                    slot_id=slot_id,
                    variant_id=variant_id,
                    job_id=job_id,
                )
                raise
            try:
                landed = download_to_content_address(
                    result.url,
                    work_root=self._settings.work_root,
                    asset_root=self._settings.asset_root,
                    suffix=".png",
                )
                report = inspect_image(landed.path)
            except MediaProcessingError as exc:
                self._record_provider_failure(
                    job_id,
                    ArkProviderError(
                        str(exc),
                        code="keyframe_landing_failed",
                        retryable=False,
                    ),
                )
                self._fail_slot_for_terminal_job(
                    slot_id=slot_id,
                    variant_id=variant_id,
                    job_id=job_id,
                )
                raise OrchestrationError(str(exc)) from exc
            with self._session_factory.begin() as session:
                job = session.get_one(GenerationJob, job_id)
                transition_job(job, "succeeded")
                job.completed_at = datetime.now(UTC)
                job.downloaded_at = datetime.now(UTC)
                job.response_snapshot_json = {
                    "model": result.model,
                    "generatedImages": result.generated_images,
                }
                session.add(
                    MediaAsset(
                        episode_variant_id=variant_id,
                        generation_job_id=job.id,
                        asset_kind=f"scene_keyframe_{frame_role}",
                        storage_path=str(landed.path),
                        sha256=landed.sha256,
                        byte_size=landed.byte_size,
                        container=report["format"],
                        width=report["width"],
                        height=report["height"],
                        has_audio=False,
                        qc_status="passed",
                        review_status="pending",
                        qc_report_json=report,
                    )
                )
                slot = session.get_one(DailySlot, slot_id)
                if slot.status == "keyframe_generating":
                    transition_slot(slot, "keyframe_review")

        with self._session_factory() as session:
            assets = _approved_keyframes(
                session, variant_id, render_revision
            )
            return {
                "allApproved": len(_ordered_keyframes(assets, mode))
                == len(required_roles)
            }

    def _finish_video_job(
        self,
        *,
        job_id: uuid.UUID,
        variant_id: uuid.UUID,
        slot_id: uuid.UUID,
        expected_duration_ms: int,
    ) -> MediaAsset:
        with self._session_factory() as session:
            existing = session.execute(
                select(MediaAsset)
                .where(
                    MediaAsset.generation_job_id == job_id,
                    MediaAsset.asset_kind == "final_video",
                )
                .order_by(MediaAsset.created_at.desc())
            ).scalars().first()
            if existing is not None:
                return existing
            job = session.get_one(GenerationJob, job_id)
            task_id = job.provider_task_id
            status = job.status
        if not task_id:
            raise OrchestrationError("Video job has no Ark task ID.")

        deadline = time.monotonic() + self._settings.ark_task_timeout_seconds
        task: ArkVideoTask | None = None
        if status != "succeeded":
            while time.monotonic() < deadline:
                try:
                    task = self._provider.get_video_task(task_id)
                except ArkProviderError as exc:
                    if exc.retryable:
                        time.sleep(self._settings.ark_poll_interval_seconds)
                        continue
                    raise OrchestrationError(str(exc)) from exc
                if task.status in {"queued", "running"}:
                    with self._session_factory.begin() as session:
                        job = session.get_one(GenerationJob, job_id)
                        if job.status != task.status:
                            transition_job(job, task.status)
                    time.sleep(self._settings.ark_poll_interval_seconds)
                    continue
                if task.status in {
                    "succeeded",
                    "failed",
                    "expired",
                    "cancelled",
                }:
                    with self._session_factory.begin() as session:
                        job = session.get_one(GenerationJob, job_id)
                        transition_job(job, task.status)
                        job.completed_at = datetime.now(UTC)
                        job.error_code = task.error_code
                        job.error_message = task.error_message
                        job.response_snapshot_json = _task_snapshot(task)
                    break
                raise OrchestrationError(
                    f"Ark returned unsupported task status {task.status!r}."
                )
            else:
                raise OrchestrationError(
                    "Ark video task polling timed out; use cvg resume later."
                )
        if task is None or (
            task.status == "succeeded" and not task.video_url
        ):
            task = self._provider.get_video_task(task_id)
        if task.status != "succeeded":
            with self._session_factory.begin() as session:
                slot = session.get_one(DailySlot, slot_id)
                variant = session.get_one(EpisodeVariant, variant_id)
                if slot.status != "failed":
                    transition_slot(slot, "failed")
                if variant.status in {"planned", "active"}:
                    transition_variant(variant, "failed")
                slot.last_error = task.error_code or task.status
                variant.last_error = slot.last_error
            raise OrchestrationError(
                f"Ark video task ended as {task.status!r}: "
                f"{task.error_code or 'unknown_error'}."
            )
        if not task.video_url:
            raise OrchestrationError(
                "Ark succeeded but returned no downloadable video URL."
            )
        landed = download_to_content_address(
            task.video_url,
            work_root=self._settings.work_root,
            asset_root=self._settings.asset_root,
            suffix=".mp4",
        )
        if self._settings.ffprobe_path is None:
            raise OrchestrationError("ffprobe is required for video QC.")
        probe = probe_video(
            landed.path,
            ffprobe_path=self._settings.ffprobe_path,
            expected_duration_ms=expected_duration_ms,
        )
        with self._session_factory.begin() as session:
            job = session.get_one(GenerationJob, job_id)
            job.downloaded_at = datetime.now(UTC)
            slot = session.get_one(DailySlot, slot_id)
            if slot.status == "video_generating":
                transition_slot(slot, "media_qc")
            asset = MediaAsset(
                episode_variant_id=variant_id,
                generation_job_id=job.id,
                asset_kind="final_video",
                storage_path=str(landed.path),
                sha256=landed.sha256,
                byte_size=landed.byte_size,
                container=probe.container,
                video_codec=probe.video_codec,
                audio_codec=probe.audio_codec,
                width=probe.width,
                height=probe.height,
                duration_ms=probe.duration_ms,
                has_audio=probe.has_audio,
                qc_status=probe.qc_status,
                review_status="pending",
                qc_report_json=probe.report,
            )
            session.add(asset)
            session.flush()
            if probe.qc_status == "passed":
                transition_slot(slot, "content_review")
            else:
                transition_slot(slot, "failed")
                slot.last_error = "media_qc_failed"
                variant = session.get_one(EpisodeVariant, variant_id)
                if variant.status in {"planned", "active"}:
                    transition_variant(variant, "failed")
                variant.last_error = slot.last_error
        return asset

    def _record_provider_failure(
        self,
        job_id: uuid.UUID,
        exc: ArkProviderError,
    ) -> None:
        with self._session_factory.begin() as session:
            job = session.get_one(GenerationJob, job_id)
            target = "submission_unknown" if exc.submission_unknown else "failed"
            transition_job(job, target)
            job.error_code = exc.code
            job.error_message = str(exc)
            if target == "failed":
                job.completed_at = datetime.now(UTC)

    def _submit_video_with_retry(
        self,
        *,
        job_id: uuid.UUID,
        prompt: str,
        duration_ms: int,
        visual_input_mode: str,
        input_paths: tuple[Path, ...],
    ) -> ArkVideoSubmission:
        for attempt_no in (1, 2):
            with self._session_factory.begin() as session:
                session.get_one(GenerationJob, job_id).attempt_no = attempt_no
            try:
                return self._provider.create_video(
                    prompt=prompt,
                    duration_ms=duration_ms,
                    visual_input_mode=visual_input_mode,
                    input_paths=input_paths,
                )
            except ArkProviderError as exc:
                if (
                    exc.retryable
                    and not exc.submission_unknown
                    and attempt_no < 2
                ):
                    time.sleep((2 ** (attempt_no - 1)) + random.uniform(0, 0.5))
                    continue
                self._record_provider_failure(job_id, exc)
                raise OrchestrationError(str(exc)) from exc
        raise AssertionError("The bounded Ark video submission loop must return.")

    def _submit_keyframe_with_retry(
        self,
        *,
        job_id: uuid.UUID,
        prompt: str,
        reference_paths: tuple[Path, ...],
    ) -> ArkImageResult:
        for attempt_no in (1, 2):
            with self._session_factory.begin() as session:
                session.get_one(GenerationJob, job_id).attempt_no = attempt_no
            try:
                return self._provider.generate_keyframe(
                    prompt=prompt,
                    reference_paths=reference_paths,
                )
            except ArkProviderError as exc:
                if (
                    exc.retryable
                    and not exc.submission_unknown
                    and attempt_no < 2
                ):
                    time.sleep((2 ** (attempt_no - 1)) + random.uniform(0, 0.5))
                    continue
                self._record_provider_failure(job_id, exc)
                raise OrchestrationError(str(exc)) from exc
        raise AssertionError("The bounded Ark keyframe submission loop must return.")

    def _fail_slot_for_terminal_job(
        self,
        *,
        slot_id: uuid.UUID,
        variant_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> None:
        with self._session_factory.begin() as session:
            job = session.get_one(GenerationJob, job_id)
            if job.status == "submission_unknown":
                return
            slot = session.get_one(DailySlot, slot_id)
            variant = session.get_one(EpisodeVariant, variant_id)
            if slot.status != "failed":
                transition_slot(slot, "failed")
            if variant.status in {"planned", "active"}:
                transition_variant(variant, "failed")
            slot.last_error = job.error_code or "provider_failed"
            variant.last_error = slot.last_error


def _select_variant(
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


def _load_approved_references(
    session: Session,
    variant: EpisodeVariant,
) -> dict[str, Any]:
    episode = variant.episode_spec_json
    person_version, cat_version = episode["requiredSubjectVersionIds"]
    requested = {
        ("person", person_version),
        ("cat", cat_version),
        ("style", episode["styleVersionId"]),
    }
    assets = session.execute(
        select(ReferenceAsset).where(ReferenceAsset.status == "approved")
    ).scalars().all()
    by_role_version = {
        (asset.role, asset.version_id): asset for asset in assets
    }
    missing = sorted(
        f"{role}:{version}"
        for role, version in requested
        if (role, version) not in by_role_version
    )
    if missing:
        raise OrchestrationError(
            "Approved Canon references are missing: " + ", ".join(missing)
        )
    person = by_role_version[("person", person_version)]
    cat = by_role_version[("cat", cat_version)]
    style = by_role_version[("style", episode["styleVersionId"])]
    return {
        "paths": (
            Path(person.storage_path),
            Path(cat.storage_path),
            Path(style.storage_path),
        ),
        "plan": VisualReferences(
            person_asset_id=person.asset_id,
            cat_asset_id=cat.asset_id,
            style_asset_ids=(style.asset_id,),
        ),
    }


def _approved_keyframes(
    session: Session,
    variant_id: uuid.UUID,
    render_revision: int,
) -> list[MediaAsset]:
    return session.execute(
        select(MediaAsset)
        .join(
            GenerationJob,
            MediaAsset.generation_job_id == GenerationJob.id,
        )
        .where(
            MediaAsset.episode_variant_id == variant_id,
            MediaAsset.asset_kind.in_(
                ("scene_keyframe_first", "scene_keyframe_last")
            ),
            MediaAsset.qc_status == "passed",
            MediaAsset.review_status == "approved",
            GenerationJob.render_revision == render_revision,
        )
    ).scalars().all()


def _ordered_keyframes(
    assets: list[MediaAsset],
    mode: VisualInputMode,
) -> list[MediaAsset]:
    if mode is VisualInputMode.DIRECT_REFERENCES:
        return []
    by_kind = {asset.asset_kind: asset for asset in assets}
    kinds = ["scene_keyframe_first"]
    if mode is VisualInputMode.GENERATED_FIRST_LAST_FRAMES:
        kinds.append("scene_keyframe_last")
    return [by_kind[kind] for kind in kinds if kind in by_kind]


def _job_key(
    episode_id: str,
    render_revision: int,
    job_type: str,
    clip_index: int,
    normalized_input_hash: str,
) -> str:
    raw = (
        f"{episode_id}|{render_revision}|{job_type}|{clip_index}|"
        f"{normalized_input_hash}"
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as exc:
        raise OrchestrationError(f"Cannot read visual input asset: {path}") from exc


def _task_snapshot(task: ArkVideoTask) -> dict[str, Any]:
    return {
        "taskId": task.task_id,
        "status": task.status,
        "model": task.model,
        "durationSeconds": task.duration_seconds,
        "resolution": task.resolution,
        "ratio": task.ratio,
        "generateAudio": task.generate_audio,
        "errorCode": task.error_code,
    }


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
