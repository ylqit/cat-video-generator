from __future__ import annotations

import hashlib
import random
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..ark_provider import (
    ArkImageResult,
    ArkProviderError,
    ArkVideoSubmission,
    ArkVideoTask,
)
from ..config import RuntimeSettings
from ..contracts import canonical_content_hash
from ..media import (
    MediaProcessingError,
    download_to_content_address,
    inspect_image,
    probe_video,
)
from ..models import DailySlot, EpisodeVariant, GenerationJob, MediaAsset
from ..repository import get_or_create_generation_job
from ..state import transition_job, transition_slot, transition_variant
from .errors import OrchestrationError
from .gateway import ArkGateway


class ArkJobExecutor:
    """Own Ark job intent, retry, polling, landing, QC, and failure state."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: RuntimeSettings,
        gateway: ArkGateway,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._gateway = gateway

    def ensure_keyframe_asset(
        self,
        *,
        slot_id: uuid.UUID,
        variant_id: uuid.UUID,
        episode_id: str,
        render_revision: int,
        frame_role: str,
        clip_index: int,
        prompt: str,
        reference_paths: tuple[Path, ...],
        request_snapshot: dict[str, Any],
        resume_only: bool,
    ) -> MediaAsset | None:
        normalized_hash = canonical_content_hash(request_snapshot)
        idempotency_key = _job_key(
            episode_id,
            render_revision,
            f"keyframe_{frame_role}",
            clip_index,
            normalized_hash,
        )
        with self._session_factory.begin() as session:
            if resume_only:
                job = session.execute(
                    select(GenerationJob).where(
                        GenerationJob.idempotency_key == idempotency_key
                    )
                ).scalar_one_or_none()
                if job is None:
                    raise OrchestrationError(
                        "Resume found no existing Ark keyframe job and will "
                        "not create a new paid submission."
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
                    provider=self._settings.provider_profile,
                    request_snapshot_json=request_snapshot,
                )
            job_id = job.id
        if not created:
            return None

        try:
            result = self._submit_keyframe_with_retry(
                job_id=job_id,
                prompt=prompt,
                reference_paths=reference_paths,
            )
        except OrchestrationError:
            self.fail_slot_for_terminal_job(
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
            self.fail_slot_for_terminal_job(
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
            asset = MediaAsset(
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
            session.add(asset)
            slot = session.get_one(DailySlot, slot_id)
            if slot.status == "keyframe_generating":
                transition_slot(slot, "keyframe_review")
            session.flush()
            return asset

    def ensure_video_asset(
        self,
        *,
        slot_id: uuid.UUID,
        variant_id: uuid.UUID,
        episode_id: str,
        render_revision: int,
        prompt: str,
        duration_ms: int,
        visual_input_mode: str,
        input_paths: tuple[Path, ...],
        request_snapshot: dict[str, Any],
        resume_only: bool,
    ) -> MediaAsset:
        normalized_hash = canonical_content_hash(request_snapshot)
        idempotency_key = _job_key(
            episode_id,
            render_revision,
            "video",
            0,
            normalized_hash,
        )
        with self._session_factory.begin() as session:
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
                    episode_variant_id=variant_id,
                    render_revision=render_revision,
                    job_type="video",
                    clip_index=0,
                    normalized_input_hash=normalized_hash,
                    idempotency_key=idempotency_key,
                    provider=self._settings.provider_profile,
                    request_snapshot_json=request_snapshot,
                )
            job_id = job.id
            job_status = job.status

        if created:
            try:
                submission = self._submit_video_with_retry(
                    job_id=job_id,
                    prompt=prompt,
                    duration_ms=duration_ms,
                    visual_input_mode=visual_input_mode,
                    input_paths=input_paths,
                )
            except OrchestrationError:
                self.fail_slot_for_terminal_job(
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
            return self._finish_video_job(
                job_id=job_id,
                variant_id=variant_id,
                slot_id=slot_id,
                expected_duration_ms=duration_ms,
            )
        except MediaProcessingError as exc:
            raise OrchestrationError(str(exc)) from exc

    def reconcile_video_job(
        self,
        job_id: uuid.UUID,
        *,
        provider_task_id: str | None = None,
    ) -> dict[str, Any]:
        """List safe candidates or bind one explicit Ark task to an unknown POST."""
        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                raise OrchestrationError(f"GenerationJob {job_id} does not exist.")
            if job.job_type != "video":
                raise OrchestrationError(
                    "Only asynchronous video jobs can be reconciled from "
                    "Ark's task list."
                )
            if job.status != "submission_unknown":
                raise OrchestrationError(
                    "Only submission_unknown jobs require task-list reconciliation."
                )
            if job.provider != self._settings.provider_profile:
                raise OrchestrationError(
                    "GenerationJob belongs to a different Ark access mode; "
                    "reconcile it with the original endpoint profile."
                )
            variant_id = job.episode_variant_id
            variant = session.get_one(EpisodeVariant, variant_id)
            slot_id = variant.daily_slot_id

        try:
            tasks = self._gateway.list_video_tasks(
                model=self._settings.ark_video_model,
                page_size=100,
            )
        except ArkProviderError as exc:
            raise OrchestrationError(str(exc)) from exc
        candidates = sorted(
            tasks,
            key=lambda task: task.created_at or 0,
            reverse=True,
        )
        candidate_payload = [_reconciliation_candidate(task) for task in candidates]
        if provider_task_id is None:
            return {
                "jobId": str(job_id),
                "bound": False,
                "candidates": candidate_payload,
                "nextAction": (
                    "Inspect the Ark console and rerun with "
                    "--provider-task-id only after an exact manual match."
                ),
            }

        matched = next(
            (task for task in candidates if task.task_id == provider_task_id),
            None,
        )
        if matched is None:
            raise OrchestrationError(
                "The selected provider task is not present in the current "
                "Ark task-list result for the configured video model."
            )
        allowed_statuses = {
            "queued",
            "running",
            "succeeded",
            "failed",
            "expired",
            "cancelled",
        }
        if matched.status not in allowed_statuses:
            raise OrchestrationError(
                f"Ark task has unsupported status {matched.status!r}."
            )

        with self._session_factory.begin() as session:
            job = session.execute(
                select(GenerationJob)
                .where(GenerationJob.id == job_id)
                .with_for_update()
            ).scalar_one()
            if job.status != "submission_unknown":
                raise OrchestrationError(
                    "GenerationJob changed while reconciliation was in "
                    "progress; reload status before retrying."
                )
            job.provider_task_id = matched.task_id
            transition_job(job, matched.status)
            job.submitted_at = _provider_created_at(matched)
            job.response_snapshot_json = _task_snapshot(matched)
            job.error_code = matched.error_code
            job.error_message = matched.error_message
            if matched.status in {
                "succeeded",
                "failed",
                "expired",
                "cancelled",
            }:
                job.completed_at = datetime.now(UTC)

        if matched.status in {"failed", "expired", "cancelled"}:
            self.fail_slot_for_terminal_job(
                slot_id=slot_id,
                variant_id=variant_id,
                job_id=job_id,
            )
        return {
            "jobId": str(job_id),
            "bound": True,
            "providerTask": _reconciliation_candidate(matched),
            "nextAction": (
                "create a new render revision"
                if matched.status in {"failed", "expired", "cancelled"}
                else "cvg resume --allow-paid-generation"
            ),
        }

    def fail_slot_for_terminal_job(
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

    def _finish_video_job(
        self,
        *,
        job_id: uuid.UUID,
        variant_id: uuid.UUID,
        slot_id: uuid.UUID,
        expected_duration_ms: int,
    ) -> MediaAsset:
        with self._session_factory() as session:
            existing = (
                session.execute(
                    select(MediaAsset)
                    .where(
                        MediaAsset.generation_job_id == job_id,
                        MediaAsset.asset_kind == "final_video",
                    )
                    .order_by(MediaAsset.created_at.desc())
                )
                .scalars()
                .first()
            )
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
                    task = self._gateway.get_video_task(task_id)
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
        if task is None or (task.status == "succeeded" and not task.video_url):
            task = self._gateway.get_video_task(task_id)
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
            expected_resolution=self._settings.ark_video_resolution,
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
                return self._gateway.create_video(
                    prompt=prompt,
                    duration_ms=duration_ms,
                    visual_input_mode=visual_input_mode,
                    input_paths=input_paths,
                )
            except ArkProviderError as exc:
                if exc.retryable and not exc.submission_unknown and attempt_no < 2:
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
                return self._gateway.generate_keyframe(
                    prompt=prompt,
                    reference_paths=reference_paths,
                )
            except ArkProviderError as exc:
                if exc.retryable and not exc.submission_unknown and attempt_no < 2:
                    time.sleep((2 ** (attempt_no - 1)) + random.uniform(0, 0.5))
                    continue
                self._record_provider_failure(job_id, exc)
                raise OrchestrationError(str(exc)) from exc
        raise AssertionError("The bounded Ark keyframe submission loop must return.")


def file_sha256(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as exc:
        raise OrchestrationError(f"Cannot read visual input asset: {path}") from exc


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
        "createdAt": task.created_at,
    }


def _reconciliation_candidate(task: ArkVideoTask) -> dict[str, Any]:
    return {
        "taskId": task.task_id,
        "status": task.status,
        "model": task.model,
        "createdAt": task.created_at,
        "durationSeconds": task.duration_seconds,
        "resolution": task.resolution,
        "ratio": task.ratio,
        "generateAudio": task.generate_audio,
    }


def _provider_created_at(task: ArkVideoTask) -> datetime:
    if task.created_at is None:
        return datetime.now(UTC)
    try:
        return datetime.fromtimestamp(task.created_at, UTC)
    except (OverflowError, OSError, ValueError):
        return datetime.now(UTC)
