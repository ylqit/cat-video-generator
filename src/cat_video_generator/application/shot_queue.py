"""V4 project editing, independent shot production and project sequences."""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ..domain.contracts import (
    AnchorMode,
    ReferenceTarget,
    ReferenceUsage,
    ShotCardDraft,
    ShotPromptContext,
    ShotSuggestionOutput,
    StoryProjectInput,
)
from ..domain.prompts import (
    compile_anchor_prompt,
    compile_range_edit_prompt,
    compile_shot_suggestion_prompt,
    compile_shot_video_prompt,
    compile_video_review_prompt,
)
from ..domain.rendering import (
    MediaSource,
    ProjectSequencePlan,
    SequenceClip,
    SequenceStatus,
    build_edit_input_plan,
    build_shot_input_plan,
)
from ..domain.workflow import PromptPurpose, StepKind, StepStatus
from .ports import (
    AssetStore,
    DirectorGateway,
    FrameExtractor,
    GatewayError,
    MediaGateway,
    MediaProbe,
    ShotQueueStore,
    StoredAsset,
    StoredSequence,
    StoredShot,
    StoredStep,
    VideoTaskResult,
)


@dataclass(frozen=True, slots=True)
class SuggestionResult:
    step_id: uuid.UUID
    output: ShotSuggestionOutput


class ProjectEditingService:
    def __init__(
        self,
        *,
        repository: ShotQueueStore,
        director: DirectorGateway | None,
        provider_name: str,
    ) -> None:
        self._repository = repository
        self._director = director
        self._provider_name = provider_name

    def create_project(
        self, source: StoryProjectInput, *, content_date: date | None = None
    ) -> dict[str, Any]:
        project = self._repository.create_project(
            source,
            content_date=content_date or date.today(),
        )
        return {"projectId": str(project.id), "sceneCount": 1}

    def suggest_shots(
        self,
        scene_id: uuid.UUID,
        *,
        allow_paid_generation: bool,
    ) -> SuggestionResult:
        if not allow_paid_generation:
            raise ValueError("AI shot suggestions require explicit paid-generation permission")
        if self._director is None:
            raise RuntimeError("Director gateway is not configured")
        scene = self._repository.get_scene(scene_id)
        project = self._repository.get_project(scene.project_id)
        prior_steps = [
            item
            for item in self._repository.list_steps(
                project_id=project.id,
                scene_id=scene.id,
            )
            if item.shot_card_id is None and item.operation_key == "director:shot-suggestions"
        ]
        unresolved = next(
            (item for item in prior_steps if item.status is StepStatus.SUBMISSION_UNKNOWN),
            None,
        )
        if unresolved is not None:
            raise ValueError(
                f"step {unresolved.id} is submission_unknown; do not repeat the paid request"
            )
        active = next(
            (
                item
                for item in prior_steps
                if item.status
                in {
                    StepStatus.PENDING,
                    StepStatus.SUBMITTING,
                    StepStatus.QUEUED,
                    StepStatus.RUNNING,
                }
            ),
            None,
        )
        if active is not None:
            raise ValueError(f"step {active.id} is still active")
        prompt = compile_shot_suggestion_prompt(
            project_title=project.title,
            scene_title=scene.draft.title,
            source_text=scene.draft.source_text,
            context_note=scene.draft.context_note,
        )
        input_hash = _hash_json(
            {
                "project": str(project.id),
                "scene": scene.draft.model_dump(mode="json", by_alias=True),
            }
        )
        attempt = self._repository.next_scene_attempt(
            scene_id=scene.id,
            operation_key="director:shot-suggestions",
        )
        step, _ = self._repository.create_step_with_prompt(
            project_id=project.id,
            scene_id=scene.id,
            shot_id=None,
            kind=StepKind.DIRECTOR,
            operation_key="director:shot-suggestions",
            attempt=attempt,
            provider=self._provider_name,
            model=self._director.model,
            input_hash=input_hash,
            input_snapshot={"scene": scene.draft.model_dump(mode="json", by_alias=True)},
            purpose=PromptPurpose.DIRECTOR,
            prompt_text=prompt,
        )
        if step.status is StepStatus.SUCCEEDED:
            saved = step.input_snapshot.get("providerOutput")
            return SuggestionResult(step.id, ShotSuggestionOutput.model_validate(saved))
        self._repository.update_step(step.id, status=StepStatus.SUBMITTING)
        try:
            result = self._director.generate_structured(
                prompt=prompt,
                schema=ShotSuggestionOutput.model_json_schema(by_alias=True),
                output_name="ShotSuggestionOutput",
            )
        except GatewayError as exc:
            status = StepStatus.SUBMISSION_UNKNOWN if exc.submission_unknown else StepStatus.FAILED
            self._repository.update_step(
                step.id,
                status=status,
                error=_error_payload(exc),
            )
            raise
        except Exception as exc:
            error = _error_payload(exc)
            self._repository.update_step(step.id, status=StepStatus.FAILED, error=error)
            raise
        snapshot = {
            "scene": scene.draft.model_dump(mode="json", by_alias=True),
            "providerOutput": result.payload,
            "responseId": result.response_id,
            "requestHash": result.request_hash,
            "normalizationWarnings": [],
        }
        try:
            output = ShotSuggestionOutput.model_validate(result.payload)
        except Exception as exc:
            self._repository.update_step(
                step.id,
                status=StepStatus.FAILED,
                error=_error_payload(exc),
                input_snapshot=snapshot,
            )
            raise
        self._repository.update_step(
            step.id,
            status=StepStatus.SUCCEEDED,
            input_snapshot=snapshot,
        )
        return SuggestionResult(step.id, output)

    def accept_suggestions(self, step_id: uuid.UUID) -> tuple[StoredShot, ...]:
        step = self._repository.get_step(step_id)
        if step.kind is not StepKind.DIRECTOR or step.scene_id is None:
            raise ValueError("step is not a scene shot-suggestion result")
        if step.status is not StepStatus.SUCCEEDED:
            raise ValueError("only a succeeded suggestion step can be accepted")
        output = ShotSuggestionOutput.model_validate(step.input_snapshot.get("providerOutput"))
        drafts = tuple(
            ShotCardDraft(
                title=item.title,
                direction=item.direction,
                durationSeconds=item.suggested_duration_seconds,
                anchorMode=AnchorMode.TEXT_ONLY,
            )
            for item in output.shots
        )
        return self._repository.replace_shots(step.scene_id, drafts)


class ShotProductionService:
    def __init__(
        self,
        *,
        repository: ShotQueueStore,
        gateway: MediaGateway | None,
        asset_store: AssetStore,
        media_probe: MediaProbe,
        frame_extractor: FrameExtractor | None,
        provider_name: str,
        resolution: str,
        enable_video_advice: bool = True,
        poll_interval_seconds: float = 10,
        task_timeout_seconds: float = 1800,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._asset_store = asset_store
        self._media_probe = media_probe
        self._frame_extractor = frame_extractor
        self._provider_name = provider_name
        self._resolution = resolution
        self._enable_video_advice = enable_video_advice
        self._poll_interval_seconds = poll_interval_seconds
        self._task_timeout_seconds = task_timeout_seconds

    def import_reference(
        self,
        *,
        project_id: uuid.UUID,
        path: Path,
        usage: str,
        role: str,
    ) -> StoredAsset:
        if usage not in {"approved_anchor", "generation_reference"}:
            raise ValueError("unsupported reference usage")
        if role not in {"identity", "style", "scene", "prop", "composition"}:
            raise ValueError("unsupported reference role")
        landed = self._asset_store.import_local(path)
        qc = self._media_probe.inspect_image(landed.path)
        return self._repository.add_asset(
            landed=landed,
            role="external_reference",
            media_type="image",
            scope="project",
            status="approved" if usage == "approved_anchor" else "ready",
            project_id=project_id,
            scene_id=None,
            shot_id=None,
            step_id=None,
            semantic_key=f"external:{landed.sha256[:16]}",
            metadata={"usage": usage, "referenceRole": role, "qc": qc},
        )

    def preview_shot_prompt(self, shot_id: uuid.UUID) -> dict[str, Any]:
        shot = self._repository.get_shot(shot_id)
        context = self._prompt_context(shot)
        anchor, references, descriptions = self._resolve_video_inputs(
            shot,
            require_generated_anchor=False,
        )
        plan = build_shot_input_plan(
            resolution=self._resolution,
            duration_seconds=shot.draft.duration_seconds,
            anchor=None if anchor is None else _media_source(anchor),
            references=tuple(_media_source(item) for item in references),
        )
        prompt = compile_shot_video_prompt(
            context,
            plan,
            binding_descriptions=descriptions,
        )
        return {
            "prompt": prompt.text,
            "charCount": prompt.char_count,
            "utf8Bytes": prompt.utf8_bytes,
            "inputPlan": plan.model_dump(mode="json"),
        }

    def generate_anchor(
        self,
        shot_id: uuid.UUID,
        *,
        allow_paid_generation: bool,
        regenerate: bool = False,
        reason: str | None = None,
    ) -> dict[str, Any]:
        shot = self._repository.get_shot(shot_id)
        if shot.draft.anchor_mode is not AnchorMode.GENERATE:
            raise ValueError("the shot anchor mode is not generate")
        self._require_paid_gateway(allow_paid_generation)
        references = self._reference_assets(shot, target=ReferenceTarget.ANCHOR)
        context = self._prompt_context(shot)
        descriptions = tuple(
            f"@图片{index}只负责{binding.role.value}"
            for index, binding in enumerate(
                self._reference_bindings(shot, target=ReferenceTarget.ANCHOR), 1
            )
        )
        prompt = compile_anchor_prompt(
            context,
            reference_descriptions=descriptions,
            regeneration_instruction=reason if regenerate else None,
        )
        snapshot = {
            "shotCardId": str(shot.id),
            "referenceAssetIds": [str(item.id) for item in references],
            "durationSeconds": shot.draft.duration_seconds,
        }
        step, _ = self._new_paid_step(
            shot,
            kind=StepKind.IMAGE,
            operation_key="image:anchor",
            model=self._gateway.image_model,
            purpose=PromptPurpose.IMAGE,
            prompt=prompt.text,
            snapshot=snapshot,
            force_new_attempt=regenerate,
            retry_reason=reason,
        )
        if step.status is not StepStatus.PENDING:
            existing_asset = next(
                (
                    item
                    for item in self._repository.list_assets(shot_id=shot.id)
                    if item.step_id == step.id and item.media_type == "image"
                ),
                None,
            )
            return {
                "stepId": str(step.id),
                "assetId": None if existing_asset is None else str(existing_asset.id),
                "reused": True,
                "status": step.status.value,
            }
        self._repository.update_step(step.id, status=StepStatus.SUBMITTING)
        try:
            result = self._gateway.generate_image(
                prompt=prompt.text,
                reference_paths=tuple(item.path for item in references),
            )
            landed = self._asset_store.download(result.url, suffix=".png")
            qc = self._media_probe.inspect_image(landed.path)
            asset = self._repository.add_asset(
                landed=landed,
                role="shot_anchor",
                media_type="image",
                scope="shot",
                status="candidate",
                project_id=shot.project_id,
                scene_id=shot.scene_id,
                shot_id=shot.id,
                step_id=step.id,
                semantic_key=f"shot:{shot.id}:anchor",
                metadata={
                    "qc": qc,
                    "providerUrl": result.url,
                    "advisories": (
                        ["画面边缘大面积接近纯黑，请人工确认是否存在异常黑边"]
                        if qc.get("blackBorderDetected")
                        else []
                    ),
                },
            )
            self._repository.update_step(step.id, status=StepStatus.AWAITING_REVIEW)
            return {"stepId": str(step.id), "assetId": str(asset.id), "status": "awaiting_review"}
        except GatewayError as exc:
            status = StepStatus.SUBMISSION_UNKNOWN if exc.submission_unknown else StepStatus.FAILED
            self._repository.update_step(
                step.id,
                status=status,
                error=_error_payload(exc),
            )
            raise
        except Exception as exc:
            self._repository.update_step(
                step.id,
                status=StepStatus.FAILED,
                error=_error_payload(exc),
            )
            raise

    def generate_video(
        self,
        shot_id: uuid.UUID,
        *,
        allow_paid_generation: bool,
        regenerate: bool = False,
        reason: str | None = None,
    ) -> dict[str, Any]:
        self._require_paid_gateway(allow_paid_generation)
        shot = self._repository.get_shot(shot_id)
        context = self._prompt_context(shot)
        anchor, references, descriptions = self._resolve_video_inputs(shot)
        plan = build_shot_input_plan(
            resolution=self._resolution,
            duration_seconds=shot.draft.duration_seconds,
            anchor=None if anchor is None else _media_source(anchor),
            references=tuple(_media_source(item) for item in references),
        )
        prompt = compile_shot_video_prompt(
            context,
            plan,
            binding_descriptions=descriptions,
            regeneration_instruction=reason if regenerate else None,
        )
        sources = (() if anchor is None else (anchor,)) + references
        snapshot = {
            "shotCardId": str(shot.id),
            "inputPlan": plan.model_dump(mode="json"),
            "sourceAssetIds": [str(item.id) for item in sources],
        }
        step, _ = self._new_paid_step(
            shot,
            kind=StepKind.VIDEO,
            operation_key="video:shot",
            model=self._gateway.video_model,
            purpose=PromptPurpose.VIDEO,
            prompt=prompt.text,
            snapshot=snapshot,
            force_new_attempt=regenerate,
            retry_reason=reason,
        )
        if step.status is not StepStatus.PENDING:
            return {"stepId": str(step.id), "reused": True, "status": step.status.value}
        self._repository.update_step(step.id, status=StepStatus.SUBMITTING)
        try:
            task = self._gateway.submit_video(
                prompt=prompt.text,
                input_plan=plan,
                input_sources=tuple(item.path for item in sources),
            )
        except GatewayError as exc:
            status = StepStatus.SUBMISSION_UNKNOWN if exc.submission_unknown else StepStatus.FAILED
            self._repository.update_step(step.id, status=status, error=_error_payload(exc))
            raise
        return self._continue_submitted_video(step, task)

    def resume_step(self, step_id: uuid.UUID, *, wait: bool = False) -> dict[str, Any]:
        self._require_gateway()
        step = self._repository.get_step(step_id)
        if step.kind is not StepKind.VIDEO or not step.provider_task_id:
            raise ValueError("only a video step with a provider task can be resumed")
        deadline = time.monotonic() + (self._task_timeout_seconds if wait else 0)
        while True:
            task = self._gateway.get_video_task(step.provider_task_id)
            normalized = _provider_step_status(task.status)
            if normalized in {StepStatus.QUEUED, StepStatus.RUNNING}:
                if normalized is not step.status:
                    self._repository.update_step(step.id, status=normalized)
                    step = self._repository.get_step(step.id)
                if not wait or time.monotonic() >= deadline:
                    return {
                        "stepId": str(step.id),
                        "status": normalized.value,
                        "taskId": task.task_id,
                    }
                time.sleep(self._poll_interval_seconds)
                continue
            if normalized is StepStatus.FAILED:
                self._repository.update_step(
                    step.id,
                    status=StepStatus.FAILED,
                    error={"code": task.error_code, "message": task.error_message},
                )
                return {"stepId": str(step.id), "status": "failed", "taskId": task.task_id}
            if not task.video_url:
                raise ValueError("succeeded provider task has no downloadable video URL")
            return self._land_video(step, task.video_url)

    def _continue_submitted_video(
        self,
        step: StoredStep,
        task: VideoTaskResult,
    ) -> dict[str, Any]:
        submitted_status = _provider_step_status(task.status)
        if submitted_status is StepStatus.FAILED:
            self._repository.update_step(
                step.id,
                status=StepStatus.FAILED,
                task_id=task.task_id,
                error={"code": task.error_code, "message": task.error_message},
            )
            return {"stepId": str(step.id), "status": "failed", "taskId": task.task_id}

        # Receiving a provider task ID is the durable submission boundary.  Even
        # when the provider already reports running or succeeded, record queued
        # first so recovery observes the same honest lifecycle as async tasks.
        self._repository.update_step(step.id, status=StepStatus.QUEUED, task_id=task.task_id)
        if submitted_status is StepStatus.RUNNING:
            self._repository.update_step(step.id, status=StepStatus.RUNNING)
        if submitted_status is StepStatus.SUCCEEDED and task.video_url:
            return self._land_video(self._repository.get_step(step.id), task.video_url)
        return self.resume_step(step.id, wait=False)

    def decide_asset(
        self,
        asset_id: uuid.UUID,
        *,
        decision: str,
        reason: str | None,
        select: bool,
    ) -> dict[str, Any]:
        asset = self._repository.decide_asset(asset_id, decision=decision, reason=reason)
        if decision == "approved" and select and asset.shot_card_id is not None:
            kind = "anchor" if asset.media_type == "image" else "video"
            self._repository.select_shot_asset(asset.shot_card_id, kind=kind, asset_id=asset.id)
        return {"assetId": str(asset.id), "decision": decision, "selected": select}

    def reconcile_candidates(self, step_id: uuid.UUID) -> tuple[dict[str, Any], ...]:
        self._require_gateway()
        step = self._repository.get_step(step_id)
        if step.status is not StepStatus.SUBMISSION_UNKNOWN or step.kind is not StepKind.VIDEO:
            raise ValueError("only submission_unknown video steps can be reconciled")
        candidates = self._gateway.list_video_tasks(model=step.model or self._gateway.video_model)
        input_plan = step.input_snapshot.get("inputPlan")
        expected_duration = (
            input_plan.get("duration_seconds") if isinstance(input_plan, dict) else None
        )
        expected_resolution = input_plan.get("resolution") if isinstance(input_plan, dict) else None
        bound_task_ids = {
            item.provider_task_id
            for item in self._repository.list_steps(project_id=step.project_id)
            if item.id != step.id and item.provider_task_id
        }
        return tuple(
            {
                "taskId": item.task_id,
                "status": item.status,
                "createdAt": None if item.created_at is None else item.created_at.isoformat(),
                "durationSeconds": item.duration_seconds,
                "resolution": item.resolution,
                "ratio": item.ratio,
                "generateAudio": item.generate_audio,
            }
            for item in candidates
            if item.task_id not in bound_task_ids
            and item.duration_seconds in {None, expected_duration}
            and item.resolution in {None, expected_resolution}
            and item.ratio in {None, "9:16"}
            and item.generate_audio in {None, True}
            and (
                item.created_at is None
                or step.created_at is None
                or abs((item.created_at - step.created_at).total_seconds()) <= 1800
            )
        )

    def reconcile(self, step_id: uuid.UUID, *, task_id: str) -> dict[str, Any]:
        step = self._repository.get_step(step_id)
        if step.status is not StepStatus.SUBMISSION_UNKNOWN:
            raise ValueError("step is not awaiting reconciliation")
        if task_id not in {str(item["taskId"]) for item in self.reconcile_candidates(step_id)}:
            raise ValueError("the selected provider task does not match this video intent")
        self._repository.update_step(step_id, status=StepStatus.QUEUED, task_id=task_id)
        return self.resume_step(step_id, wait=False)

    def range_edit(
        self,
        shot_id: uuid.UUID,
        *,
        source_asset_id: uuid.UUID,
        start_ms: int,
        end_ms: int,
        instruction: str,
        allow_paid_generation: bool,
    ) -> dict[str, Any]:
        self._require_paid_gateway(allow_paid_generation)
        if self._frame_extractor is None:
            raise RuntimeError("range editing requires ffmpeg frame extraction")
        if not 500 <= end_ms - start_ms <= 13_000:
            raise ValueError("range edit selection must be between 0.5 and 13 seconds")
        shot = self._repository.get_shot(shot_id)
        source = self._repository.get_asset(source_asset_id)
        duration_ms = int(source.metadata.get("qc", {}).get("durationMs") or 0)
        if source.shot_card_id != shot_id or source.media_type != "video":
            raise ValueError("range edit source must be a video version of this shot")
        if not 0 <= start_ms < end_ms <= duration_ms:
            raise ValueError("range edit selection is outside the source video")
        provider_url = source.metadata.get("providerUrl")
        if not isinstance(provider_url, str) or not provider_url.startswith("https://"):
            raise ValueError("source video no longer has an accessible Ark HTTPS URL")
        boundary_paths = self._frame_extractor.extract_frames_at(
            source,
            timestamps_ms=(max(0, start_ms - 1), min(duration_ms - 1, end_ms)),
        )
        try:
            boundary_assets = tuple(
                self._repository.add_asset(
                    landed=self._asset_store.import_local(path),
                    role="range_boundary",
                    media_type="image",
                    scope="shot",
                    status="ready",
                    project_id=shot.project_id,
                    scene_id=shot.scene_id,
                    shot_id=shot.id,
                    step_id=None,
                    semantic_key=f"shot:{shot.id}:boundary:{index}",
                    metadata={"timestampMs": timestamp},
                )
                for index, (path, timestamp) in enumerate(
                    zip(boundary_paths, (start_ms, end_ms), strict=True), 1
                )
            )
        finally:
            for path in boundary_paths:
                path.unlink(missing_ok=True)
        provider_duration = min(13, max(4, math.ceil((end_ms - start_ms) / 1000)))
        plan = build_edit_input_plan(
            resolution=self._resolution,
            duration_seconds=provider_duration,
            source_video=_media_source(source),
            before_frame=_media_source(boundary_assets[0]),
            after_frame=_media_source(boundary_assets[1]),
        )
        prompt = compile_range_edit_prompt(
            self._prompt_context(shot),
            instruction=instruction,
            source_start_ms=start_ms,
            source_end_ms=end_ms,
        )
        snapshot = {
            "shotCardId": str(shot.id),
            "sourceAssetId": str(source.id),
            "startMs": start_ms,
            "endMs": end_ms,
            "targetDurationMs": end_ms - start_ms,
            "inputPlan": plan.model_dump(mode="json"),
            "boundaryAssetIds": [str(item.id) for item in boundary_assets],
        }
        step, _ = self._new_paid_step(
            shot,
            kind=StepKind.VIDEO,
            operation_key="video:range-edit",
            model=self._gateway.video_model,
            purpose=PromptPurpose.VIDEO,
            prompt=prompt.text,
            snapshot=snapshot,
            force_new_attempt=True,
            retry_reason="用户显式发起区间重拍",
        )
        self._repository.update_step(step.id, status=StepStatus.SUBMITTING)
        try:
            task = self._gateway.submit_video(
                prompt=prompt.text,
                input_plan=plan,
                input_sources=(provider_url, boundary_assets[0].path, boundary_assets[1].path),
            )
        except GatewayError as exc:
            status = StepStatus.SUBMISSION_UNKNOWN if exc.submission_unknown else StepStatus.FAILED
            self._repository.update_step(step.id, status=status, error=_error_payload(exc))
            raise
        return self._continue_submitted_video(step, task)

    def _land_video(self, step: StoredStep, video_url: str) -> dict[str, Any]:
        if step.shot_card_id is None:
            raise ValueError("video step is not bound to a shot card")
        shot = self._repository.get_shot(step.shot_card_id)
        existing_asset = next(
            (
                item
                for item in self._repository.list_assets(shot_id=shot.id)
                if item.step_id == step.id and item.media_type == "video"
            ),
            None,
        )
        if existing_asset is not None:
            if step.status in {StepStatus.QUEUED, StepStatus.RUNNING}:
                self._repository.update_step(step.id, status=StepStatus.AWAITING_REVIEW)
            return {
                "stepId": str(step.id),
                "taskId": step.provider_task_id,
                "assetId": str(existing_asset.id),
                "status": "awaiting_review",
                "qc": existing_asset.metadata.get("qc", {}),
                "reused": True,
            }
        landed = self._asset_store.download(video_url, suffix=".mp4")
        if step.operation_key == "video:range-edit":
            source = self._repository.get_asset(
                uuid.UUID(str(step.input_snapshot["sourceAssetId"]))
            )
            replacement_qc = self._media_probe.inspect_video(
                landed.path,
                expected_duration_seconds=max(
                    4,
                    round(int(step.input_snapshot["targetDurationMs"]) / 1000),
                ),
                expected_resolution=self._resolution,
                minimum_duration_seconds=1,
                maximum_duration_seconds=15,
                duration_tolerance_ms=5000,
                require_audio=False,
            )
            replacement_duration_ms = int(replacement_qc.get("durationMs") or 0)
            if replacement_duration_ms <= 0:
                raise ValueError("range edit provider result has no measurable duration")
            landed = self._asset_store.render_range_replacement(
                base_path=source.path,
                replacement_path=landed.path,
                replacement_duration_ms=replacement_duration_ms,
                start_ms=int(step.input_snapshot["startMs"]),
                end_ms=int(step.input_snapshot["endMs"]),
            )
        qc = self._media_probe.inspect_video(
            landed.path,
            expected_duration_seconds=shot.draft.duration_seconds,
            expected_resolution=self._resolution,
        )
        if not qc.get("passed"):
            self._repository.update_step(
                step.id,
                status=StepStatus.FAILED,
                error={"code": "technical_qc_failed", "qc": qc},
            )
            raise ValueError(f"video technical QC failed: {qc.get('failures')}")
        asset = self._repository.add_asset(
            landed=landed,
            role=("shot_video_edit" if step.operation_key == "video:range-edit" else "shot_video"),
            media_type="video",
            scope="shot",
            status="candidate",
            project_id=shot.project_id,
            scene_id=shot.scene_id,
            shot_id=shot.id,
            step_id=step.id,
            semantic_key=f"shot:{shot.id}:video:{step.attempt}",
            metadata={
                "qc": qc,
                "providerUrl": (None if step.operation_key == "video:range-edit" else video_url),
                "providerSegmentUrl": (
                    video_url if step.operation_key == "video:range-edit" else None
                ),
                "taskId": step.provider_task_id,
                "rangeEdit": (
                    None
                    if step.operation_key != "video:range-edit"
                    else {
                        "sourceAssetId": step.input_snapshot["sourceAssetId"],
                        "startMs": step.input_snapshot["startMs"],
                        "endMs": step.input_snapshot["endMs"],
                    }
                ),
            },
        )
        self._repository.update_step(step.id, status=StepStatus.AWAITING_REVIEW)
        self._record_video_review(shot, step, asset)
        return {
            "stepId": str(step.id),
            "taskId": step.provider_task_id,
            "assetId": str(asset.id),
            "status": "awaiting_review",
            "qc": qc,
        }

    def _record_video_review(
        self,
        shot: StoredShot,
        step: StoredStep,
        asset: StoredAsset,
    ) -> None:
        if self._frame_extractor is None:
            return
        frames: tuple[Path, ...] = ()
        try:
            duration_ms = int(asset.metadata["qc"]["durationMs"])
            count = min(12, max(4, math.ceil(duration_ms / 1000) + 2))
            frames = self._frame_extractor.extract_review_frames(asset, count=count)
            for ordinal, frame in enumerate(frames, 1):
                self._repository.add_asset(
                    landed=self._asset_store.import_local(frame),
                    role="review_frame",
                    media_type="image",
                    scope="shot",
                    status="ready",
                    project_id=shot.project_id,
                    scene_id=shot.scene_id,
                    shot_id=shot.id,
                    step_id=step.id,
                    semantic_key=f"shot:{shot.id}:video:{asset.id}:frame:{ordinal}",
                    metadata={
                        "sourceVideoAssetId": str(asset.id),
                        "ordinal": ordinal,
                        "frameCount": len(frames),
                    },
                )
            if not self._enable_video_advice or self._gateway is None:
                return
            result = self._gateway.diagnose_video_frames(
                prompt=compile_video_review_prompt(self._prompt_context(shot)),
                frame_paths=frames,
            )
            warnings = tuple(
                {"severity": "suggestion", "message": item} for item in result.violations
            )
            self._repository.add_review(
                step_id=step.id,
                asset_id=asset.id,
                source="ark_visual",
                decision="pending",
                reason="AI suggestions do not automatically approve or reject media",
                warnings=warnings,
                evidence={
                    "confidence": result.confidence,
                    "evidence": list(result.evidence),
                    "shotBoundariesSeconds": list(result.shot_boundaries_seconds),
                },
            )
        except Exception as exc:
            self._repository.add_review(
                step_id=step.id,
                asset_id=asset.id,
                source="technical",
                decision="pending",
                reason="AI advice was unavailable; manual review remains available",
                warnings=({"severity": "warning", "message": str(exc)},),
                evidence={},
            )
        finally:
            for frame in frames:
                frame.unlink(missing_ok=True)

    def _new_paid_step(
        self,
        shot: StoredShot,
        *,
        kind: StepKind,
        operation_key: str,
        model: str,
        purpose: PromptPurpose,
        prompt: str,
        snapshot: dict[str, Any],
        force_new_attempt: bool = False,
        retry_reason: str | None = None,
    ) -> tuple[StoredStep, Any]:
        input_hash = _hash_json({"prompt": prompt, "snapshot": snapshot})
        # Reuse the current input's first attempt.  A changed input or explicit
        # regenerate request receives a new attempt without touching old media.
        existing = [
            item
            for item in self._repository.list_steps(
                project_id=shot.project_id,
                shot_id=shot.id,
            )
            if item.operation_key == operation_key
            and item.input_snapshot.get("inputHash") == input_hash
        ]
        operation_steps = [
            item
            for item in self._repository.list_steps(
                project_id=shot.project_id,
                shot_id=shot.id,
            )
            if item.operation_key == operation_key
        ]
        unresolved = next(
            (item for item in operation_steps if item.status is StepStatus.SUBMISSION_UNKNOWN),
            None,
        )
        if unresolved is not None and force_new_attempt:
            raise ValueError(
                f"step {unresolved.id} is submission_unknown; reconcile it before regeneration"
            )
        if force_new_attempt:
            previous = operation_steps[-1] if operation_steps else None
            if previous is not None and previous.status in {
                StepStatus.PENDING,
                StepStatus.SUBMITTING,
                StepStatus.QUEUED,
                StepStatus.RUNNING,
            }:
                raise ValueError(f"step {previous.id} is still active and cannot be regenerated")
            attempt = self._repository.next_attempt(
                shot_id=shot.id,
                operation_key=operation_key,
            )
            snapshot = {
                **snapshot,
                "retryOfStepId": None if previous is None else str(previous.id),
                "retryReason": retry_reason or "explicit regeneration",
            }
        elif existing:
            attempt = existing[-1].attempt
        else:
            attempt = self._repository.next_attempt(
                shot_id=shot.id,
                operation_key=operation_key,
            )
        snapshot = {**snapshot, "inputHash": input_hash}
        return self._repository.create_step_with_prompt(
            project_id=shot.project_id,
            scene_id=shot.scene_id,
            shot_id=shot.id,
            kind=kind,
            operation_key=operation_key,
            attempt=attempt,
            provider=self._provider_name,
            model=model,
            input_hash=input_hash,
            input_snapshot=snapshot,
            purpose=purpose,
            prompt_text=prompt,
        )

    def _resolve_video_inputs(
        self,
        shot: StoredShot,
        *,
        require_generated_anchor: bool = True,
    ) -> tuple[StoredAsset | None, tuple[StoredAsset, ...], tuple[str, ...]]:
        binding_pairs = [
            (binding, self._repository.get_asset(binding.asset_id))
            for binding in shot.draft.reference_bindings
        ]
        anchor: StoredAsset | None = None
        if shot.draft.anchor_mode is AnchorMode.EXISTING:
            anchor = next(
                asset
                for binding, asset in binding_pairs
                if binding.usage is ReferenceUsage.APPROVED_ANCHOR
            )
        elif shot.draft.anchor_mode is AnchorMode.GENERATE:
            if shot.selected_anchor_asset_id is None:
                if require_generated_anchor:
                    raise ValueError("generated anchor must be approved and selected before video")
            else:
                anchor = self._repository.get_asset(shot.selected_anchor_asset_id)
        if anchor is not None and (
            anchor.media_type != "image"
            or anchor.status not in {"approved", "ready"}
            or not anchor.path.is_file()
        ):
            raise ValueError("the selected anchor is missing, damaged, or not approved")
        references = tuple(
            asset
            for binding, asset in binding_pairs
            if binding.usage is ReferenceUsage.GENERATION_REFERENCE
            and binding.apply_to in {ReferenceTarget.VIDEO, ReferenceTarget.BOTH}
        )
        if any(
            item.media_type != "image"
            or item.status not in {"approved", "ready"}
            or not item.path.is_file()
            for item in references
        ):
            raise ValueError("a selected generation reference is unavailable or not an image")
        ordered = (() if anchor is None else (anchor,)) + references
        descriptions = tuple(
            f"@图片{index}={item.metadata.get('referenceRole', item.role)}参考，"
            "只承担已声明职责，不改写其他主体"
            for index, item in enumerate(ordered, 1)
        )
        return anchor, references, descriptions

    def _reference_assets(
        self, shot: StoredShot, *, target: ReferenceTarget
    ) -> tuple[StoredAsset, ...]:
        assets = tuple(
            self._repository.get_asset(item.asset_id)
            for item in self._reference_bindings(shot, target=target)
        )
        if any(
            item.media_type != "image"
            or item.status not in {"approved", "ready"}
            or not item.path.is_file()
            for item in assets
        ):
            raise ValueError("a selected generation reference is unavailable or not an image")
        return assets

    @staticmethod
    def _reference_bindings(shot: StoredShot, *, target: ReferenceTarget) -> tuple[Any, ...]:
        return tuple(
            item
            for item in shot.draft.reference_bindings
            if item.usage is ReferenceUsage.GENERATION_REFERENCE
            and item.apply_to in {target, ReferenceTarget.BOTH}
        )

    def _prompt_context(self, shot: StoredShot) -> ShotPromptContext:
        scene = self._repository.get_scene(shot.scene_id)
        project = self._repository.get_project(shot.project_id)
        return ShotPromptContext(
            project_title=project.title,
            scene_title=scene.draft.title,
            scene_text=scene.draft.source_text,
            context_note=scene.draft.context_note,
            shot_title=shot.draft.title,
            direction=shot.draft.direction,
            duration_seconds=shot.draft.duration_seconds,
        )

    def _require_gateway(self) -> None:
        if self._gateway is None:
            raise RuntimeError("Ark media gateway is not configured")

    def _require_paid_gateway(self, allowed: bool) -> None:
        if not allowed:
            raise ValueError("this operation requires explicit paid-generation permission")
        self._require_gateway()


class SequenceService:
    def __init__(
        self,
        *,
        repository: ShotQueueStore,
        asset_store: AssetStore,
        media_probe: MediaProbe,
        resolution: str,
    ) -> None:
        self._repository = repository
        self._asset_store = asset_store
        self._media_probe = media_probe
        self._resolution = resolution

    def build_project_sequence(self, project_id: uuid.UUID) -> StoredSequence:
        scenes = self._repository.list_scenes(project_id)
        selected: list[tuple[StoredShot, StoredAsset, int]] = []
        for scene in scenes:
            for shot in self._repository.list_shots(scene.id):
                if shot.selected_video_asset_id is None:
                    continue
                asset = self._repository.get_asset(shot.selected_video_asset_id)
                duration_ms = int(asset.metadata.get("qc", {}).get("durationMs") or 0)
                if duration_ms <= 0:
                    raise ValueError("selected video is missing QC duration")
                selected.append((shot, asset, duration_ms))
        if not selected:
            raise ValueError("a project sequence requires at least one approved shot video")
        clips: list[SequenceClip] = []
        cursor = 0
        for order, (shot, asset, duration_ms) in enumerate(selected, 1):
            clips.append(
                SequenceClip(
                    order=order,
                    shot_card_id=shot.id,
                    source_asset_id=asset.id,
                    source_start_ms=0,
                    source_end_ms=duration_ms,
                    timeline_start_ms=cursor,
                    timeline_end_ms=cursor + duration_ms,
                )
            )
            cursor += duration_ms
        plan = ProjectSequencePlan(duration_ms=cursor, clips=clips)
        landed = self._asset_store.concatenate_videos(tuple(item[1].path for item in selected))
        qc = self._media_probe.inspect_video(
            landed.path,
            expected_duration_seconds=round(cursor / 1000),
            expected_resolution=self._resolution,
            minimum_duration_seconds=1,
            maximum_duration_seconds=max(15, math.ceil(cursor / 1000) + 1),
            duration_tolerance_ms=1500,
        )
        if not qc.get("passed"):
            raise ValueError(f"project sequence QC failed: {qc.get('failures')}")
        asset = self._repository.add_asset(
            landed=landed,
            role="project_sequence",
            media_type="video",
            scope="project",
            status="candidate",
            project_id=project_id,
            scene_id=None,
            shot_id=None,
            step_id=None,
            semantic_key=f"project:{project_id}:sequence",
            metadata={"qc": qc, "audioPolicy": "native_fades"},
        )
        project = self._repository.get_project(project_id)
        prior_sequences = self._repository.list_sequences(project_id)
        parent_sequence_id = project.selected_sequence_id or (
            prior_sequences[-1].id if prior_sequences else None
        )
        return self._repository.create_sequence(
            project_id=project_id,
            plan=plan,
            parent_sequence_id=parent_sequence_id,
            rendered_asset_id=asset.id,
            status=SequenceStatus.CONTENT_REVIEW,
        )


def _media_source(asset: StoredAsset) -> MediaSource:
    return MediaSource(
        asset_id=asset.id,
        semantic_key=asset.semantic_key or f"asset:{asset.id}",
        media_type=asset.media_type,
        sha256=asset.sha256,
        metadata=asset.metadata,
    )


def _provider_step_status(value: str) -> StepStatus:
    normalized = value.lower()
    if normalized in {"queued", "pending"}:
        return StepStatus.QUEUED
    if normalized in {"running", "processing"}:
        return StepStatus.RUNNING
    if normalized in {"succeeded", "completed"}:
        return StepStatus.SUCCEEDED
    return StepStatus.FAILED


def _hash_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _error_payload(exc: Exception) -> dict[str, Any]:
    return {
        "code": getattr(exc, "code", exc.__class__.__name__),
        "message": str(exc),
        "retryable": bool(getattr(exc, "retryable", False)),
        "submissionUnknown": bool(getattr(exc, "submission_unknown", False)),
        "requestId": getattr(exc, "request_id", None),
    }
