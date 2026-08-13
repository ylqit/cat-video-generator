"""V5 project editing, independent video-clip production and project sequences."""

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
    LookReferenceBinding,
    LookReferencePurpose,
    ReferenceBinding,
    ReferenceRole,
    ReferenceTarget,
    ReferenceUsage,
    SceneLookDraft,
    SceneLookPlan,
    SceneLookUsage,
    ShotAssistAnalysis,
    ShotAssistPatch,
    ShotCardDraft,
    ShotPromptContext,
    ShotSuggestion,
    ShotSuggestionOutput,
    StoryDiagnosisOutput,
    StoryProjectInput,
    StoryRewriteOutput,
    StoryRewriteStrategy,
    VisualProfileDraft,
)
from ..domain.creative_workflow import story_source_hash
from ..domain.prompts import (
    compile_anchor_prompt,
    compile_range_edit_prompt,
    compile_scene_look_prompt,
    compile_shot_assistance_prompt,
    compile_shot_suggestion_prompt,
    compile_shot_video_prompt,
    compile_shot_video_prompt_parts,
    compile_story_diagnosis_prompt,
    compile_story_rewrite_prompt,
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
from ..domain.shot_assistance import analyze_shot_draft
from ..domain.workflow import PromptPurpose, StepKind, StepStatus
from .ports import (
    AssetStore,
    DirectorGateway,
    FrameExtractor,
    GatewayError,
    MediaGateway,
    MediaProbe,
    RuntimePreflight,
    ShotQueueStore,
    StoredAsset,
    StoredProject,
    StoredScene,
    StoredSequence,
    StoredShot,
    StoredStep,
    StoredVisualProfileRevision,
    VideoTaskResult,
)


@dataclass(frozen=True, slots=True)
class SuggestionResult:
    step_id: uuid.UUID
    output: ShotSuggestionOutput


@dataclass(frozen=True, slots=True)
class StoryDiagnosisResult:
    step_id: uuid.UUID
    output: StoryDiagnosisOutput


@dataclass(frozen=True, slots=True)
class StoryRewriteResult:
    step_id: uuid.UUID
    output: StoryRewriteOutput


@dataclass(frozen=True, slots=True)
class ShotAssistanceResult:
    step_id: uuid.UUID
    analysis: ShotAssistAnalysis


class RevisionConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SceneLookInputSet:
    scene: StoredScene
    profile: StoredVisualProfileRevision
    draft: SceneLookDraft
    bindings: tuple[LookReferenceBinding, ...]
    assets: tuple[StoredAsset, ...]
    descriptions: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreviousTailState:
    previous_shot: StoredShot | None
    source_video_id: uuid.UUID | None
    active: StoredAsset | None
    bound: StoredAsset | None
    stale: bool


@dataclass(frozen=True, slots=True)
class ShotAssistCandidate:
    asset: StoredAsset
    source_layer: str
    responsibility: str


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

    def diagnose_story(
        self,
        scene_id: uuid.UUID,
        *,
        allow_paid_generation: bool,
    ) -> StoryDiagnosisResult:
        self._require_paid_director(
            allow_paid_generation,
            "story diagnosis requires explicit paid-generation permission",
        )
        scene = self._repository.get_scene(scene_id)
        project = self._repository.get_project(scene.project_id)
        profile = self._repository.get_visual_profile(project.id)
        self._assert_scene_stage_available(
            project_id=project.id,
            scene_id=scene.id,
            operation_key="director:story-diagnosis",
        )
        previous, following = self._adjacent_scenes(scene)
        prompt = compile_story_diagnosis_prompt(
            project_title=project.title,
            scene=scene.draft,
            visual_profile=profile.draft,
            previous_scene_summary=(
                None if previous is None else previous.draft.source_text
            ),
            next_scene_summary=(
                None if following is None else following.draft.source_text
            ),
        )
        snapshot = {
            "sourceHash": story_source_hash(scene.draft),
            "scene": scene.draft.model_dump(mode="json", by_alias=True),
            "visualProfileRevisionId": str(profile.id),
            "visualProfileHash": profile.profile_hash,
            "previousScene": _scene_story_snapshot(previous),
            "nextScene": _scene_story_snapshot(following),
        }
        step = self._run_scene_director_stage(
            project_id=project.id,
            scene_id=scene.id,
            operation_key="director:story-diagnosis",
            prompt=prompt,
            snapshot=snapshot,
            output_type=StoryDiagnosisOutput,
            output_name="StoryDiagnosisOutput",
        )
        return StoryDiagnosisResult(
            step_id=step.id,
            output=StoryDiagnosisOutput.model_validate(
                step.input_snapshot.get("providerOutput")
            ),
        )

    def accept_story_diagnosis(
        self,
        step_id: uuid.UUID,
        *,
        diagnosis: StoryDiagnosisOutput,
        selected_strategy: StoryRewriteStrategy | str | None,
        additional_instructions: str,
        preserve_original: bool,
    ) -> StoredStep:
        step = self._repository.get_step(step_id)
        self._validate_scene_stage_step(
            step,
            operation_key="director:story-diagnosis",
        )
        assert step.scene_id is not None
        scene = self._repository.get_scene(step.scene_id)
        expected_hash = str(step.input_snapshot.get("sourceHash") or "")
        if story_source_hash(scene.draft) != expected_hash:
            raise RevisionConflictError(
                "scene story changed after diagnosis; run the diagnosis again"
            )
        selected = (
            None
            if selected_strategy is None
            else StoryRewriteStrategy(selected_strategy)
        )
        if preserve_original and selected is not None:
            raise ValueError(
                "preserve-original and a rewrite strategy are mutually exclusive"
            )
        if not preserve_original and selected is None:
            raise ValueError("select a rewrite strategy or preserve the original story")
        if selected is not None and selected not in {
            item.strategy for item in diagnosis.rewrite_options
        }:
            raise ValueError("selected rewrite strategy is not present in the accepted diagnosis")
        accepted_output = {
            "diagnosis": diagnosis.model_dump(mode="json", by_alias=True),
            "selectedStrategy": None if selected is None else selected.value,
            "additionalInstructions": additional_instructions.strip(),
            "preserveOriginal": preserve_original,
        }
        return self._repository.accept_story_diagnosis(
            step_id=step.id,
            expected_source_hash=expected_hash,
            accepted_output=accepted_output,
        )

    def rewrite_story(
        self,
        scene_id: uuid.UUID,
        *,
        diagnosis_step_id: uuid.UUID,
        allow_paid_generation: bool,
    ) -> StoryRewriteResult:
        self._require_paid_director(
            allow_paid_generation,
            "story rewrite requires explicit paid-generation permission",
        )
        scene = self._repository.get_scene(scene_id)
        project = self._repository.get_project(scene.project_id)
        profile = self._repository.get_visual_profile(project.id)
        diagnosis_step = self._repository.get_step(diagnosis_step_id)
        if (
            diagnosis_step.scene_id != scene.id
            or diagnosis_step.operation_key != "director:story-diagnosis"
            or diagnosis_step.status is not StepStatus.SUCCEEDED
            or "acceptedOutput" not in diagnosis_step.input_snapshot
        ):
            raise ValueError("story rewrite requires an accepted story diagnosis")
        accepted_diagnosis = diagnosis_step.input_snapshot["acceptedOutput"]
        if (
            accepted_diagnosis.get("preserveOriginal") is True
            or not accepted_diagnosis.get("selectedStrategy")
        ):
            raise ValueError(
                "story rewrite requires a selected diagnosis rewrite strategy"
            )
        if diagnosis_step.input_snapshot.get("sourceHash") != story_source_hash(
            scene.draft
        ):
            raise RevisionConflictError(
                "scene story changed after diagnosis; run the diagnosis again"
            )
        self._assert_scene_stage_available(
            project_id=project.id,
            scene_id=scene.id,
            operation_key="director:story-rewrite",
        )
        prompt = compile_story_rewrite_prompt(
            project_title=project.title,
            scene=scene.draft,
            visual_profile=profile.draft,
            accepted_diagnosis=accepted_diagnosis,
        )
        snapshot = {
            "sourceHash": story_source_hash(scene.draft),
            "scene": scene.draft.model_dump(mode="json", by_alias=True),
            "visualProfileRevisionId": str(profile.id),
            "visualProfileHash": profile.profile_hash,
            "diagnosisStepId": str(diagnosis_step.id),
            "acceptedDiagnosis": accepted_diagnosis,
        }
        step = self._run_scene_director_stage(
            project_id=project.id,
            scene_id=scene.id,
            operation_key="director:story-rewrite",
            prompt=prompt,
            snapshot=snapshot,
            output_type=StoryRewriteOutput,
            output_name="StoryRewriteOutput",
        )
        return StoryRewriteResult(
            step_id=step.id,
            output=StoryRewriteOutput.model_validate(
                step.input_snapshot.get("providerOutput")
            ),
        )

    def accept_story_rewrite(
        self,
        step_id: uuid.UUID,
        *,
        rewrite: StoryRewriteOutput,
    ) -> StoredScene:
        step = self._repository.get_step(step_id)
        self._validate_scene_stage_step(
            step,
            operation_key="director:story-rewrite",
        )
        assert step.scene_id is not None
        scene = self._repository.get_scene(step.scene_id)
        expected_hash = str(step.input_snapshot.get("sourceHash") or "")
        if story_source_hash(scene.draft) != expected_hash:
            raise RevisionConflictError(
                "scene story changed after rewrite; generate a new rewrite"
            )
        revised_draft = scene.draft.model_copy(
            update={"source_text": rewrite.rewritten_story}
        )
        accepted_output = {
            **rewrite.model_dump(mode="json", by_alias=True),
            "acceptedStoryHash": story_source_hash(revised_draft),
        }
        return self._repository.accept_story_rewrite(
            step_id=step.id,
            expected_source_hash=expected_hash,
            accepted_output=accepted_output,
            rewritten_story=rewrite.rewritten_story,
        )

    def creative_workflow(self, scene_id: uuid.UUID) -> dict[str, Any]:
        scene = self._repository.get_scene(scene_id)
        steps = self._repository.list_steps(
            project_id=scene.project_id,
            scene_id=scene.id,
        )
        stage_keys = {
            "diagnosis": "director:story-diagnosis",
            "rewrite": "director:story-rewrite",
            "storyboard": "director:shot-suggestions",
        }
        diagnosis_steps = [
            item
            for item in steps
            if item.operation_key == "director:story-diagnosis"
        ]
        first_scene_snapshot = (
            None
            if not diagnosis_steps
            else diagnosis_steps[0].input_snapshot.get("scene")
        )
        original_story = (
            first_scene_snapshot.get("sourceText")
            if isinstance(first_scene_snapshot, dict)
            else scene.draft.source_text
        )
        current_source = "scene_draft"
        current_source_step_id: str | None = None
        current_hash = story_source_hash(scene.draft)
        for step in reversed(steps):
            accepted = step.input_snapshot.get("acceptedOutput")
            if not isinstance(accepted, dict):
                continue
            if (
                step.operation_key == "director:story-rewrite"
                and accepted.get("acceptedStoryHash") == current_hash
            ):
                current_source = "accepted_rewrite"
                current_source_step_id = str(step.id)
                break
            if (
                step.operation_key == "director:story-diagnosis"
                and accepted.get("preserveOriginal") is True
                and step.input_snapshot.get("sourceHash") == current_hash
            ):
                current_source = "preserved_original"
                current_source_step_id = str(step.id)
                break
        return {
            "sceneId": str(scene.id),
            "originalStory": original_story,
            "currentStory": scene.draft.source_text,
            "currentStoryHash": current_hash,
            "currentStorySource": current_source,
            "currentStorySourceStepId": current_source_step_id,
            "stages": {
                name: [
                    _creative_step_json(item)
                    for item in reversed(steps)
                    if item.operation_key == operation_key
                ]
                for name, operation_key in stage_keys.items()
            },
            "reviews": [
                _creative_step_json(item)
                for item in reversed(steps)
                if item.operation_key == "director:shot-assistance"
            ],
        }

    def restore_project_canon_references(
        self,
        project_id: uuid.UUID,
    ) -> dict[str, Any]:
        current = self._repository.get_visual_profile(project_id)
        canon = self._repository.get_default_visual_profile(project_id)
        purposes = {item.purpose for item in canon.reference_bindings}
        required = {
            LookReferencePurpose.PERSON_IDENTITY,
            LookReferencePurpose.CAT_IDENTITY,
            LookReferencePurpose.STYLE,
        }
        if not required.issubset(purposes):
            raise ValueError(
                "Canon references are incomplete; repair Canon assets before restoring the project"
            )
        restored_draft: VisualProfileDraft = current.draft.model_copy(
            update={"reference_bindings": canon.reference_bindings}
        )
        profile, cleaned_shot_count = self._repository.restore_project_canon_references(
            project_id,
            restored_draft,
        )
        return {
            "projectId": str(project_id),
            "visualProfileRevisionId": str(profile.id),
            "visualProfileRevision": profile.revision,
            "referenceCount": len(profile.draft.reference_bindings),
            "cleanedShotCount": cleaned_shot_count,
        }

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
        profile = self._repository.get_visual_profile(project.id)
        approved_story_step = self._approved_story_step(scene)
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
            story_mode=scene.draft.story_mode.value,
            target_shot_count=scene.draft.target_shot_count,
            visual_profile=profile.draft,
        )
        input_hash = _hash_json(
            {
                "prompt": prompt,
                "project": str(project.id),
                "scene": scene.draft.model_dump(mode="json", by_alias=True),
                "approvedStoryStepId": str(approved_story_step.id),
                "visualProfileRevisionId": str(profile.id),
                "visualProfileHash": profile.profile_hash,
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
            input_snapshot={
                "scene": scene.draft.model_dump(mode="json", by_alias=True),
                "sourceHash": story_source_hash(scene.draft),
                "approvedStoryStepId": str(approved_story_step.id),
                "visualProfileRevisionId": str(profile.id),
                "visualProfileHash": profile.profile_hash,
            },
            purpose=PromptPurpose.DIRECTOR,
            prompt_text=prompt,
        )
        if step.status is StepStatus.SUCCEEDED:
            saved = step.input_snapshot.get("providerOutput")
            output = ShotSuggestionOutput.model_validate(saved)
            _validate_suggestion_count(output, scene.draft.target_shot_count)
            return SuggestionResult(step.id, output)
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
            "sourceHash": story_source_hash(scene.draft),
            "approvedStoryStepId": str(approved_story_step.id),
            "visualProfileRevisionId": str(profile.id),
            "visualProfileHash": profile.profile_hash,
            "providerOutput": result.payload,
            "responseId": result.response_id,
            "requestHash": result.request_hash,
            "normalizationWarnings": [],
        }
        try:
            output = ShotSuggestionOutput.model_validate(result.payload)
            _validate_suggestion_count(output, scene.draft.target_shot_count)
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

    def accept_suggestions(
        self,
        step_id: uuid.UUID,
        *,
        look_plan: SceneLookPlan | None,
        shots: tuple[ShotSuggestion, ...],
    ) -> tuple[StoredShot, ...]:
        step = self._repository.get_step(step_id)
        if step.kind is not StepKind.DIRECTOR or step.scene_id is None:
            raise ValueError("step is not a scene shot-suggestion result")
        if step.status is not StepStatus.SUCCEEDED:
            raise ValueError("only a succeeded suggestion step can be accepted")
        scene = self._repository.get_scene(step.scene_id)
        if step.input_snapshot.get("sourceHash") != story_source_hash(scene.draft):
            raise RevisionConflictError(
                "scene story changed after storyboard generation; generate suggestions again"
            )
        if len(shots) != scene.draft.target_shot_count:
            raise ValueError(
                f"当前模式必须接受{scene.draft.target_shot_count}个视频片段"
            )
        drafts = tuple(
            ShotCardDraft(
                title=item.title,
                direction=item.direction,
                durationSeconds=item.suggested_duration_seconds,
                anchorMode=AnchorMode.TEXT_ONLY,
            )
            for item in shots
        )
        accepted_output = {
            "lookPlan": (
                None
                if look_plan is None
                else look_plan.model_dump(mode="json", by_alias=True)
            ),
            "shots": [item.model_dump(mode="json", by_alias=True) for item in shots],
        }
        return self._repository.accept_scene_suggestions(
            step_id=step.id,
            drafts=drafts,
            look_plan=look_plan,
            accepted_output=accepted_output,
        )

    def _require_paid_director(self, allowed: bool, message: str) -> None:
        if not allowed:
            raise ValueError(message)
        if self._director is None:
            raise RuntimeError("Director gateway is not configured")

    def _assert_scene_stage_available(
        self,
        *,
        project_id: uuid.UUID,
        scene_id: uuid.UUID,
        operation_key: str,
    ) -> None:
        prior = [
            item
            for item in self._repository.list_steps(
                project_id=project_id,
                scene_id=scene_id,
            )
            if item.shot_card_id is None and item.operation_key == operation_key
        ]
        unresolved = next(
            (item for item in prior if item.status is StepStatus.SUBMISSION_UNKNOWN),
            None,
        )
        if unresolved is not None:
            raise ValueError(
                f"step {unresolved.id} is submission_unknown; do not repeat the paid request"
            )
        active = next(
            (
                item
                for item in prior
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

    def _run_scene_director_stage(
        self,
        *,
        project_id: uuid.UUID,
        scene_id: uuid.UUID,
        operation_key: str,
        prompt: str,
        snapshot: dict[str, Any],
        output_type: type[StoryDiagnosisOutput] | type[StoryRewriteOutput],
        output_name: str,
    ) -> StoredStep:
        if self._director is None:
            raise RuntimeError("Director gateway is not configured")
        attempt = self._repository.next_scene_attempt(
            scene_id=scene_id,
            operation_key=operation_key,
        )
        step, _ = self._repository.create_step_with_prompt(
            project_id=project_id,
            scene_id=scene_id,
            shot_id=None,
            kind=StepKind.DIRECTOR,
            operation_key=operation_key,
            attempt=attempt,
            provider=self._provider_name,
            model=self._director.model,
            input_hash=_hash_json({"prompt": prompt, "snapshot": snapshot}),
            input_snapshot=snapshot,
            purpose=PromptPurpose.DIRECTOR,
            prompt_text=prompt,
        )
        if step.status is StepStatus.SUCCEEDED:
            output_type.model_validate(step.input_snapshot.get("providerOutput"))
            return step
        self._repository.update_step(step.id, status=StepStatus.SUBMITTING)
        try:
            result = self._director.generate_structured(
                prompt=prompt,
                schema=output_type.model_json_schema(by_alias=True),
                output_name=output_name,
            )
            output_type.model_validate(result.payload)
        except GatewayError as exc:
            status = (
                StepStatus.SUBMISSION_UNKNOWN
                if exc.submission_unknown
                else StepStatus.FAILED
            )
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
        return self._repository.update_step(
            step.id,
            status=StepStatus.SUCCEEDED,
            input_snapshot={
                **snapshot,
                "providerOutput": result.payload,
                "responseId": result.response_id,
                "requestHash": result.request_hash,
            },
        )

    def _adjacent_scenes(
        self,
        scene: StoredScene,
    ) -> tuple[StoredScene | None, StoredScene | None]:
        ordered = sorted(
            self._repository.list_scenes(scene.project_id),
            key=lambda item: item.order,
        )
        index = next(index for index, item in enumerate(ordered) if item.id == scene.id)
        previous = ordered[index - 1] if index > 0 else None
        following = ordered[index + 1] if index + 1 < len(ordered) else None
        return previous, following

    def _validate_scene_stage_step(
        self,
        step: StoredStep,
        *,
        operation_key: str,
    ) -> None:
        if (
            step.kind is not StepKind.DIRECTOR
            or step.status is not StepStatus.SUCCEEDED
            or step.scene_id is None
            or step.shot_card_id is not None
            or step.operation_key != operation_key
        ):
            raise ValueError(f"step is not a succeeded {operation_key} result")
        if "acceptedAt" in step.input_snapshot:
            raise RevisionConflictError("creative workflow step has already been accepted")

    def _approved_story_step(self, scene: StoredScene) -> StoredStep:
        current_hash = story_source_hash(scene.draft)
        steps = reversed(
            self._repository.list_steps(
                project_id=scene.project_id,
                scene_id=scene.id,
            )
        )
        for step in steps:
            accepted = step.input_snapshot.get("acceptedOutput")
            if not isinstance(accepted, dict):
                continue
            if (
                step.operation_key == "director:story-rewrite"
                and accepted.get("acceptedStoryHash") == current_hash
            ):
                return step
            if (
                step.operation_key == "director:story-diagnosis"
                and accepted.get("preserveOriginal") is True
                and step.input_snapshot.get("sourceHash") == current_hash
            ):
                return step
        raise ValueError(
            "storyboard generation requires an accepted story rewrite or an explicit "
            "preserve-original decision"
        )

    def assist_shot(
        self,
        shot_id: uuid.UUID,
        *,
        source_draft_revision: int,
        candidate_asset_ids: tuple[uuid.UUID, ...],
        allow_paid_generation: bool,
    ) -> ShotAssistanceResult:
        if not allow_paid_generation:
            raise ValueError("shot assistance requires explicit paid-generation permission")
        if self._director is None:
            raise RuntimeError("Director gateway is not configured")
        shot = self._repository.get_shot(shot_id)
        if shot.draft_revision != source_draft_revision:
            raise RevisionConflictError("片段草稿已更新，请基于最新版本重新分析")
        prior_steps = [
            item
            for item in self._repository.list_steps(
                project_id=shot.project_id,
                shot_id=shot.id,
            )
            if item.operation_key == "director:shot-assistance"
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

        scene = self._repository.get_scene(shot.scene_id)
        project = self._repository.get_project(shot.project_id)
        candidates = self._shot_assist_candidates(shot, scene, project)
        candidate_by_id = {item.asset.id: item for item in candidates}
        unknown_ids = set(candidate_asset_ids).difference(candidate_by_id)
        if unknown_ids:
            raise ValueError(
                "shot-assistance references must come from the current shot input context"
            )

        requested_ids = set(candidate_asset_ids)
        selected_candidates: list[ShotAssistCandidate] = []
        seen_hashes: set[str] = set()
        for candidate in candidates:
            asset = candidate.asset
            if asset.id not in requested_ids:
                continue
            if (
                asset.media_type != "image"
                or asset.status not in {"approved", "ready"}
                or not asset.content_ready
            ):
                raise ValueError("shot-assistance reference is unavailable or not an image")
            if asset.sha256 in seen_hashes:
                continue
            seen_hashes.add(asset.sha256)
            selected_candidates.append(candidate)
        if len(selected_candidates) > 9:
            raise ValueError("shot assistance accepts at most 9 unique images")

        assets = tuple(item.asset for item in selected_candidates)
        profile = self._repository.get_visual_profile(shot.project_id)
        ordered_shots = sorted(
            self._repository.list_shots(shot.scene_id),
            key=lambda item: item.order,
        )
        current_index = next(
            index for index, item in enumerate(ordered_shots) if item.id == shot.id
        )
        previous = ordered_shots[current_index - 1] if current_index > 0 else None
        following = (
            ordered_shots[current_index + 1]
            if current_index + 1 < len(ordered_shots)
            else None
        )
        local_analysis = analyze_shot_draft(shot.draft)
        reference_manifest = tuple(
            f"@图片{index}={candidate.asset.display_name}；"
            f"assetId={candidate.asset.id}；来源={candidate.source_layer}；"
            f"当前职责={candidate.responsibility}"
            for index, candidate in enumerate(selected_candidates, 1)
        )
        prompt = compile_shot_assistance_prompt(
            project_title=project.title,
            scene_title=scene.draft.title,
            scene_text=scene.draft.source_text,
            current=shot.draft,
            previous=None if previous is None else previous.draft,
            following=None if following is None else following.draft,
            visual_profile=profile.draft,
            local_analysis=local_analysis,
            reference_manifest=reference_manifest,
        )
        snapshot = {
            "sourceDraftRevision": shot.draft_revision,
            "currentShot": shot.draft.model_dump(mode="json", by_alias=True),
            "previousShot": (
                None
                if previous is None
                else previous.draft.model_dump(mode="json", by_alias=True)
            ),
            "nextShot": (
                None
                if following is None
                else following.draft.model_dump(mode="json", by_alias=True)
            ),
            "localAnalysis": local_analysis.model_dump(mode="json", by_alias=True),
            "sceneStoryHash": story_source_hash(scene.draft),
            "visualProfileRevisionId": str(profile.id),
            "visualProfileHash": profile.profile_hash,
            "candidateAssets": [
                {
                    "assetId": str(candidate.asset.id),
                    "sha256": candidate.asset.sha256,
                    "ordinal": index,
                    "sourceLayer": candidate.source_layer,
                    "responsibility": candidate.responsibility,
                }
                for index, candidate in enumerate(selected_candidates, 1)
            ],
        }
        attempt = self._repository.next_attempt(
            shot_id=shot.id,
            operation_key="director:shot-assistance",
        )
        step, _ = self._repository.create_step_with_prompt(
            project_id=shot.project_id,
            scene_id=shot.scene_id,
            shot_id=shot.id,
            kind=StepKind.DIRECTOR,
            operation_key="director:shot-assistance",
            attempt=attempt,
            provider=self._provider_name,
            model=self._director.analysis_model,
            input_hash=_hash_json({"prompt": prompt, "snapshot": snapshot}),
            input_snapshot=snapshot,
            purpose=PromptPurpose.DIRECTOR,
            prompt_text=prompt,
        )
        self._repository.update_step(step.id, status=StepStatus.SUBMITTING)
        try:
            result = self._director.analyze_structured(
                prompt=prompt,
                schema=ShotAssistAnalysis.model_json_schema(by_alias=True),
                output_name="ShotAssistAnalysis",
                image_paths=tuple(asset.require_path() for asset in assets),
            )
            analysis = ShotAssistAnalysis.model_validate(result.payload)
        except GatewayError as exc:
            status = StepStatus.SUBMISSION_UNKNOWN if exc.submission_unknown else StepStatus.FAILED
            self._repository.update_step(step.id, status=status, error=_error_payload(exc))
            raise
        except Exception as exc:
            self._repository.update_step(
                step.id,
                status=StepStatus.FAILED,
                error=_error_payload(exc),
            )
            raise
        completed_snapshot = {
            **snapshot,
            "providerOutput": result.payload,
            "responseId": result.response_id,
            "requestHash": result.request_hash,
        }
        self._repository.update_step(
            step.id,
            status=StepStatus.SUCCEEDED,
            input_snapshot=completed_snapshot,
        )
        return ShotAssistanceResult(step.id, analysis)

    def shot_assist_context(self, shot_id: uuid.UUID) -> dict[str, Any]:
        shot = self._repository.get_shot(shot_id)
        scene = self._repository.get_scene(shot.scene_id)
        project = self._repository.get_project(shot.project_id)
        ordered_shots = sorted(
            self._repository.list_shots(shot.scene_id),
            key=lambda item: item.order,
        )
        current_index = next(
            index for index, item in enumerate(ordered_shots) if item.id == shot.id
        )
        tail_state = _previous_tail_state(self._repository, shot)
        candidates: list[dict[str, Any]] = []
        selected_ids: list[str] = []
        seen_ids: set[uuid.UUID] = set()
        seen_hashes: set[str] = set()
        for candidate in self._shot_assist_candidates(shot, scene, project):
            asset = candidate.asset
            duplicate = asset.id in seen_ids or asset.sha256 in seen_hashes
            if not duplicate:
                seen_ids.add(asset.id)
                seen_hashes.add(asset.sha256)
            available = (
                asset.media_type == "image"
                and asset.status in {"approved", "ready"}
                and asset.content_ready
            )
            if not duplicate and available and len(selected_ids) < 9:
                selected_ids.append(str(asset.id))
            candidates.append(
                {
                    "assetId": str(asset.id),
                    "displayName": asset.display_name,
                    "sha256": asset.sha256,
                    "sourceLayer": candidate.source_layer,
                    "responsibility": candidate.responsibility,
                    "contentReady": asset.content_ready,
                    "available": available,
                    "duplicate": duplicate,
                }
            )
        return {
            "shotId": str(shot.id),
            "sourceDraftRevision": shot.draft_revision,
            "model": (
                None if self._director is None else self._director.analysis_model
            ),
            "localAnalysis": analyze_shot_draft(shot.draft).model_dump(
                mode="json", by_alias=True
            ),
            "previousShot": (
                None
                if current_index == 0
                else {
                    "id": str(ordered_shots[current_index - 1].id),
                    "title": ordered_shots[current_index - 1].draft.title,
                }
            ),
            "nextShot": (
                None
                if current_index + 1 >= len(ordered_shots)
                else {
                    "id": str(ordered_shots[current_index + 1].id),
                    "title": ordered_shots[current_index + 1].draft.title,
                }
            ),
            "previousTail": _tail_state_json(tail_state),
            "candidates": candidates,
            "defaultCandidateAssetIds": selected_ids,
            "warnings": (
                ["可用候选图片超过9张，请在付费分析前取消部分选择"]
                if sum(1 for item in candidates if item["available"] and not item["duplicate"])
                > 9
                else []
            ),
        }

    def _shot_assist_candidates(
        self,
        shot: StoredShot,
        scene: StoredScene,
        project: StoredProject,
    ) -> tuple[ShotAssistCandidate, ...]:
        candidate_ids: list[uuid.UUID] = []
        if shot.selected_anchor_asset_id is not None:
            candidate_ids.append(shot.selected_anchor_asset_id)
        candidate_ids.extend(
            binding.asset_id
            for binding in shot.draft.reference_bindings
            if binding.asset_id != scene.selected_look_asset_id
        )
        if scene.selected_look_asset_id is not None:
            candidate_ids.append(scene.selected_look_asset_id)
        candidate_ids.extend(
            binding.asset_id
            for binding in _project_reference_bindings(
                self._repository,
                shot,
                scene,
                project,
            )
            if binding.asset_id != scene.selected_look_asset_id
        )
        tail_state = _previous_tail_state(self._repository, shot)
        if tail_state.active is not None:
            candidate_ids.append(tail_state.active.id)

        seen_ids: set[uuid.UUID] = set()
        candidates: list[ShotAssistCandidate] = []
        for asset_id in candidate_ids:
            if asset_id in seen_ids:
                continue
            seen_ids.add(asset_id)
            asset = self._repository.get_asset(asset_id)
            if asset.project_id not in {None, shot.project_id}:
                raise ValueError("shot-assistance reference belongs to another project")
            candidates.append(
                ShotAssistCandidate(
                    asset=asset,
                    source_layer=_shot_assist_asset_layer(shot, scene, project, asset),
                    responsibility=_shot_assist_asset_responsibility(shot, asset),
                )
            )
        return tuple(candidates)

    def list_shot_assistance(self, shot_id: uuid.UUID) -> list[dict[str, Any]]:
        shot = self._repository.get_shot(shot_id)
        records: list[dict[str, Any]] = []
        for step in reversed(
            self._repository.list_steps(project_id=shot.project_id, shot_id=shot.id)
        ):
            if step.operation_key != "director:shot-assistance":
                continue
            source_revision = step.input_snapshot.get("sourceDraftRevision")
            records.append(
                {
                    "stepId": str(step.id),
                    "status": step.status.value,
                    "sourceDraftRevision": source_revision,
                    "stale": source_revision != shot.draft_revision,
                    "analysis": step.input_snapshot.get("providerOutput"),
                    "acceptedOutput": step.input_snapshot.get("acceptedOutput"),
                    "acceptedAt": step.input_snapshot.get("acceptedAt"),
                    "error": step.error,
                    "createdAt": (
                        None if step.created_at is None else step.created_at.isoformat()
                    ),
                }
            )
        return records

    def accept_shot_assistance(
        self,
        step_id: uuid.UUID,
        *,
        source_draft_revision: int,
        patch: ShotAssistPatch,
    ) -> StoredShot:
        step = self._repository.get_step(step_id)
        if (
            step.kind is not StepKind.DIRECTOR
            or step.status is not StepStatus.SUCCEEDED
            or step.operation_key != "director:shot-assistance"
            or step.shot_card_id is None
        ):
            raise ValueError("step is not a succeeded shot-assistance analysis")
        shot = self._repository.get_shot(step.shot_card_id)
        if step.input_snapshot.get("sourceDraftRevision") != source_draft_revision:
            raise RevisionConflictError("接受请求与分析来源版本不一致")
        if shot.draft_revision != source_draft_revision:
            raise RevisionConflictError("片段草稿已更新，旧分析不能再接受")
        analysis = ShotAssistAnalysis.model_validate(
            step.input_snapshot.get("providerOutput")
        )
        selected = patch.model_dump(mode="json", by_alias=True, exclude_none=True)
        proposed = (
            {}
            if analysis.patch is None
            else analysis.patch.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            )
        )
        proposed.setdefault(
            "durationSeconds",
            analysis.pacing_plan.recommended_duration_seconds,
        )
        proposed.setdefault(
            "sceneLookUsage",
            analysis.recommended_scene_look_usage.value,
        )
        proposed.setdefault(
            "anchorMode",
            analysis.recommended_anchor_mode.value,
        )
        creative_bodies = {
            body
            for body in (
                analysis.creative_body,
                *(item.body for item in analysis.creative_alternatives),
            )
            if body is not None
        }
        for key, value in selected.items():
            if key == "direction" and value in creative_bodies:
                continue
            if proposed.get(key) != value:
                raise ValueError("只能接受该次LLM分析实际提出的字段值")
        return self._repository.accept_shot_assistance(
            step_id=step.id,
            source_draft_revision=source_draft_revision,
            patch=patch,
        )


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
        runtime_preflight: RuntimePreflight | None = None,
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
        self._runtime_preflight = runtime_preflight
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
        if role not in {"style", "prop", "composition"}:
            raise ValueError(
                "uploaded generic references may only use style, prop, or composition; "
                "identity and scene responsibilities come from managed sources"
            )
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

    def get_scene_look_draft(self, scene_id: uuid.UUID) -> dict[str, Any]:
        scene = self._repository.get_scene_look_draft(scene_id)
        draft = scene.look_draft or self._default_scene_look_draft(scene)
        return {
            "sceneId": str(scene.id),
            "revision": scene.look_draft_revision,
            "draft": draft.model_dump(mode="json", by_alias=True),
        }

    def save_scene_look_draft(
        self,
        scene_id: uuid.UUID,
        *,
        expected_revision: int,
        draft: SceneLookDraft,
    ) -> dict[str, Any]:
        scene = self._repository.save_scene_look_draft(
            scene_id,
            expected_revision=expected_revision,
            draft=draft,
        )
        if scene.look_draft is None:
            raise RuntimeError("saved scene look draft was not returned")
        return {
            "sceneId": str(scene.id),
            "revision": scene.look_draft_revision,
            "draft": scene.look_draft.model_dump(mode="json", by_alias=True),
        }

    def preview_scene_look_prompt(self, scene_id: uuid.UUID) -> dict[str, Any]:
        inputs = self._scene_look_inputs(scene_id, strict=False)
        project = self._repository.get_project(inputs.scene.project_id)
        prompt = compile_scene_look_prompt(
            project_title=project.title,
            scene_title=inputs.scene.draft.title,
            scene_text=inputs.scene.draft.source_text,
            look_plan=inputs.draft.look_plan,
            visual_profile=inputs.profile.draft,
            reference_descriptions=inputs.descriptions,
        )
        return {
            "prompt": prompt.text,
            "charCount": prompt.char_count,
            "utf8Bytes": prompt.utf8_bytes,
            "referenceCount": len(inputs.assets),
            "references": [
                {
                    "index": index,
                    "assetId": str(asset.id),
                    "sha256": asset.sha256,
                    "semanticKey": asset.semantic_key,
                    "purpose": binding.purpose.value,
                    "instruction": binding.instruction,
                    "contentReady": asset.content_ready,
                }
                for index, (binding, asset) in enumerate(
                    zip(inputs.bindings, inputs.assets, strict=True),
                    1,
                )
            ],
            "warnings": list(inputs.warnings),
            "visualProfileRevisionId": str(inputs.profile.id),
            "visualProfileRevision": inputs.profile.revision,
            "draftRevision": inputs.scene.look_draft_revision,
        }

    def validate_scene_look_request(self, scene_id: uuid.UUID, draft_revision: int) -> None:
        scene = self._repository.get_scene(scene_id)
        if scene.look_draft is None:
            raise RevisionConflictError("请先保存场景定妆草稿再生成")
        if scene.look_draft_revision != draft_revision:
            raise RevisionConflictError("场景定妆草稿已更新，请重新预览后再生成")
        self._scene_look_inputs(scene_id, strict=True)

    def preview_shot_prompt(self, shot_id: uuid.UUID) -> dict[str, Any]:
        shot = self._repository.get_shot(shot_id)
        profile = self._repository.get_visual_profile(shot.project_id)
        scene = self._repository.get_scene(shot.scene_id)
        project = self._repository.get_project(shot.project_id)
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
        prompt_parts = compile_shot_video_prompt_parts(
            context,
            plan,
            binding_descriptions=descriptions,
            visual_profile=profile.draft,
        )
        prompt = prompt_parts.final
        ordered_assets = (() if anchor is None else (anchor,)) + references
        local_analysis = analyze_shot_draft(shot.draft)
        return {
            "prompt": prompt.text,
            "creativeBody": prompt_parts.creative_body,
            "systemShell": prompt_parts.system_shell.text,
            "charCount": prompt.char_count,
            "utf8Bytes": prompt.utf8_bytes,
            "inputPlan": plan.model_dump(mode="json"),
            "draftRevision": shot.draft_revision,
            "sceneLookUsage": shot.draft.scene_look_usage.value,
            "localAnalysis": local_analysis.model_dump(mode="json", by_alias=True),
            "qualitativePacing": local_analysis.qualitative_pacing,
            "references": [
                {
                    "index": index,
                    "assetId": str(asset.id),
                    "displayName": asset.display_name,
                    "sourceLayer": _shot_assist_asset_layer(
                        shot, scene, project, asset
                    ),
                    "responsibility": descriptions[index - 1],
                    "contentReady": asset.content_ready,
                }
                for index, asset in enumerate(ordered_assets, 1)
            ],
            "previousTail": _tail_state_json(
                _previous_tail_state(self._repository, shot)
            ),
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
        reference_pairs = self._resolved_reference_pairs(
            shot,
            target=ReferenceTarget.ANCHOR,
        )
        references = tuple(asset for _binding, asset in reference_pairs)
        if len(references) > 14:
            raise ValueError("Seedream最多允许14张参考图")
        context = self._prompt_context(shot)
        descriptions = tuple(
            _video_reference_description(
                index,
                binding,
                scene_look_usage=shot.draft.scene_look_usage,
            )
            for index, (binding, _asset) in enumerate(reference_pairs, 1)
        )
        profile = self._repository.get_visual_profile(shot.project_id)
        prompt = compile_anchor_prompt(
            context,
            reference_descriptions=descriptions,
            regeneration_instruction=reason if regenerate else None,
            visual_profile=profile.draft,
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
                reference_paths=tuple(item.require_path() for item in references),
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

    def generate_scene_look(
        self,
        scene_id: uuid.UUID,
        *,
        allow_paid_generation: bool,
        draft_revision: int,
        regenerate: bool = False,
        reason: str | None = None,
    ) -> dict[str, Any]:
        self.validate_scene_look_request(scene_id, draft_revision)
        self._require_paid_gateway(allow_paid_generation)
        inputs = self._scene_look_inputs(scene_id, strict=True)
        scene = inputs.scene
        project = self._repository.get_project(scene.project_id)
        references = inputs.assets
        prompt = compile_scene_look_prompt(
            project_title=project.title,
            scene_title=scene.draft.title,
            scene_text=scene.draft.source_text,
            look_plan=inputs.draft.look_plan,
            visual_profile=inputs.profile.draft,
            reference_descriptions=inputs.descriptions,
            regeneration_instruction=reason if regenerate else None,
        )
        snapshot = {
            "sceneId": str(scene.id),
            "lookDraftRevision": scene.look_draft_revision,
            "visualProfileRevisionId": str(inputs.profile.id),
            "visualProfileRevision": inputs.profile.revision,
            "lookPlan": inputs.draft.look_plan.model_dump(mode="json", by_alias=True),
            "references": [
                {
                    "assetId": str(asset.id),
                    "sha256": asset.sha256,
                    "semanticKey": asset.semantic_key,
                    "purpose": binding.purpose.value,
                    "instruction": binding.instruction,
                }
                for binding, asset in zip(inputs.bindings, references, strict=True)
            ],
            "referenceAssetIds": [str(item.id) for item in references],
            "promptSha256": hashlib.sha256(prompt.text.encode("utf-8")).hexdigest(),
        }
        operation_key = "image:scene-look"
        input_hash = _hash_json({"prompt": prompt.text, "snapshot": snapshot})
        operation_steps = [
            item
            for item in self._repository.list_steps(
                project_id=project.id,
                scene_id=scene.id,
            )
            if item.shot_card_id is None and item.operation_key == operation_key
        ]
        unresolved = next(
            (item for item in operation_steps if item.status is StepStatus.SUBMISSION_UNKNOWN),
            None,
        )
        if regenerate and unresolved is not None:
            raise ValueError(
                f"step {unresolved.id} is submission_unknown; reconcile it before regeneration"
            )
        if regenerate:
            previous = operation_steps[-1] if operation_steps else None
            if previous is not None and previous.status in {
                StepStatus.PENDING,
                StepStatus.SUBMITTING,
                StepStatus.QUEUED,
                StepStatus.RUNNING,
            }:
                raise ValueError(f"step {previous.id} is still active and cannot be regenerated")
            attempt = self._repository.next_scene_attempt(
                scene_id=scene.id,
                operation_key=operation_key,
            )
            snapshot = {
                **snapshot,
                "retryOfStepId": None if previous is None else str(previous.id),
                "retryReason": reason or "explicit regeneration",
            }
        else:
            existing = [
                item
                for item in operation_steps
                if item.input_snapshot.get("inputHash") == input_hash
            ]
            attempt = (
                existing[-1].attempt
                if existing
                else self._repository.next_scene_attempt(
                    scene_id=scene.id,
                    operation_key=operation_key,
                )
            )
        snapshot = {**snapshot, "inputHash": input_hash}
        step, _ = self._repository.create_step_with_prompt(
            project_id=project.id,
            scene_id=scene.id,
            shot_id=None,
            kind=StepKind.IMAGE,
            operation_key=operation_key,
            attempt=attempt,
            provider=self._provider_name,
            model=self._gateway.image_model,
            input_hash=input_hash,
            input_snapshot=snapshot,
            purpose=PromptPurpose.IMAGE,
            prompt_text=prompt.text,
        )
        if step.status is not StepStatus.PENDING:
            existing_asset = next(
                (
                    item
                    for item in self._repository.list_assets(project_id=project.id)
                    if item.step_id == step.id and item.role == "scene_look"
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
                reference_paths=tuple(item.require_path() for item in references),
            )
            landed = self._asset_store.download(result.url, suffix=".png")
            qc = self._media_probe.inspect_image(landed.path)
            asset = self._repository.add_asset(
                landed=landed,
                role="scene_look",
                media_type="image",
                scope="scene",
                status="candidate",
                project_id=project.id,
                scene_id=scene.id,
                shot_id=None,
                step_id=step.id,
                semantic_key=f"scene:{scene.id}:look:{step.attempt}",
                metadata={
                    "qc": qc,
                    "providerUrl": result.url,
                    "visualProfileRevisionId": str(inputs.profile.id),
                    "visualProfileRevision": inputs.profile.revision,
                    "lookDraftRevision": scene.look_draft_revision,
                    "promptSha256": snapshot["promptSha256"],
                    "referenceAssetIds": snapshot["referenceAssetIds"],
                },
            )
            self._repository.update_step(step.id, status=StepStatus.AWAITING_REVIEW)
            return {
                "stepId": str(step.id),
                "assetId": str(asset.id),
                "status": "awaiting_review",
            }
        except GatewayError as exc:
            status = StepStatus.SUBMISSION_UNKNOWN if exc.submission_unknown else StepStatus.FAILED
            self._repository.update_step(step.id, status=status, error=_error_payload(exc))
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
        if self._runtime_preflight is not None:
            self._runtime_preflight.validate_for_video_generation(
                allow_paid_generation=allow_paid_generation
            )
        self._require_paid_gateway(allow_paid_generation)
        shot = self._repository.get_shot(shot_id)
        profile = self._repository.get_visual_profile(shot.project_id)
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
            visual_profile=profile.draft,
        )
        sources = (() if anchor is None else (anchor,)) + references
        snapshot = {
            "shotCardId": str(shot.id),
            "visualProfileRevisionId": str(profile.id),
            "visualProfileRevision": profile.revision,
            "inputPlan": plan.model_dump(mode="json"),
            "sourceAssetIds": [str(item.id) for item in sources],
            "sourceAssets": [
                {
                    "assetId": str(item.id),
                    "sha256": item.sha256,
                    "semanticKey": item.semantic_key,
                }
                for item in sources
            ],
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
                input_sources=tuple(item.require_path() for item in sources),
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
        if decision == "approved" and select:
            if asset.role == "scene_look" and asset.scene_id is not None:
                self._repository.select_scene_look_asset(asset.scene_id, asset.id)
            elif asset.shot_card_id is not None:
                kind = "anchor" if asset.media_type == "image" else "video"
                self._repository.select_shot_asset(asset.shot_card_id, kind=kind, asset_id=asset.id)
        tail_frame: dict[str, Any] | None = None
        if (
            decision == "approved"
            and asset.media_type == "video"
            and asset.shot_card_id is not None
        ):
            try:
                tail = self._ensure_tail_frame(asset, shot_id=asset.shot_card_id)
                tail_frame = {"status": "ready", "assetId": str(tail.id)}
            except Exception as exc:
                tail_frame = {
                    "status": "unavailable",
                    "error": _error_payload(exc),
                }
        return {
            "assetId": str(asset.id),
            "decision": decision,
            "selected": select,
            "tailFrame": tail_frame,
        }

    def adopt_previous_tail_anchor(self, shot_id: uuid.UUID) -> StoredShot:
        shot = self._repository.get_shot(shot_id)
        ordered = sorted(
            self._repository.list_shots(shot.scene_id),
            key=lambda item: item.order,
        )
        current_index = next(index for index, item in enumerate(ordered) if item.id == shot.id)
        if current_index == 0:
            raise ValueError("第一个片段没有可采用的上一片段尾帧")
        previous = ordered[current_index - 1]
        if previous.selected_video_asset_id is None:
            raise ValueError("上一片段尚未选择批准视频")
        source_video = self._repository.get_asset(previous.selected_video_asset_id)
        tail = self._ensure_tail_frame(source_video, shot_id=previous.id)
        bindings = [
            binding
            for binding in shot.draft.reference_bindings
            if binding.usage is not ReferenceUsage.APPROVED_ANCHOR
        ]
        bindings.append(
            ReferenceBinding(
                assetId=tail.id,
                usage=ReferenceUsage.APPROVED_ANCHOR,
                role=ReferenceRole.COMPOSITION,
                applyTo=ReferenceTarget.BOTH,
            )
        )
        values = shot.draft.model_dump(mode="python")
        values.update(
            {
                "anchor_mode": AnchorMode.EXISTING,
                "reference_bindings": bindings,
                "scene_look_usage": (
                    SceneLookUsage.APPEARANCE_ONLY
                    if shot.draft.scene_look_usage is SceneLookUsage.DERIVE_ANCHOR
                    else shot.draft.scene_look_usage
                ),
            }
        )
        return self._repository.update_shot(
            shot.id,
            ShotCardDraft.model_validate(values),
        )

    def tail_frame_status(self, shot_id: uuid.UUID) -> dict[str, Any]:
        shot = self._repository.get_shot(shot_id)
        return _tail_state_json(_previous_tail_state(self._repository, shot))

    def _ensure_tail_frame(
        self,
        source_video: StoredAsset,
        *,
        shot_id: uuid.UUID,
    ) -> StoredAsset:
        if source_video.media_type != "video" or source_video.status != "approved":
            raise ValueError("尾帧只能从已批准视频抽取")
        existing = next(
            (
                asset
                for asset in reversed(self._repository.list_assets(shot_id=shot_id))
                if asset.role == "shot_tail_frame"
                and asset.metadata.get("sourceVideoAssetId") == str(source_video.id)
                and asset.metadata.get("sourceVideoSha256") == source_video.sha256
                and asset.content_ready
            ),
            None,
        )
        if existing is not None:
            return existing
        if self._frame_extractor is None:
            raise RuntimeError("尾帧抽取需要可用的FFmpeg")
        frame_path, timestamp_ms = self._frame_extractor.extract_tail_frame(source_video)
        try:
            landed = self._asset_store.import_local(frame_path)
            qc = self._media_probe.inspect_image(landed.path)
        finally:
            frame_path.unlink(missing_ok=True)
        return self._repository.add_asset(
            landed=landed,
            role="shot_tail_frame",
            media_type="image",
            scope="shot",
            status="approved",
            project_id=source_video.project_id,
            scene_id=source_video.scene_id,
            shot_id=shot_id,
            step_id=None,
            semantic_key=f"shot:{shot_id}:tail:{source_video.id}",
            metadata={
                "sourceVideoAssetId": str(source_video.id),
                "sourceVideoSha256": source_video.sha256,
                "timestampMs": timestamp_ms,
                "qc": qc,
            },
        )

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
        if self._runtime_preflight is not None:
            self._runtime_preflight.validate_for_range_edit(
                allow_paid_generation=allow_paid_generation
            )
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
                input_sources=(
                    provider_url,
                    boundary_assets[0].require_path(),
                    boundary_assets[1].require_path(),
                ),
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
                base_path=source.require_path(),
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
        explicit_pairs = [
            (binding, self._repository.get_asset(binding.asset_id))
            for binding in shot.draft.reference_bindings
        ]
        anchor: StoredAsset | None = None
        if shot.draft.anchor_mode is AnchorMode.EXISTING:
            anchor = next(
                asset
                for binding, asset in explicit_pairs
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
            or not anchor.content_ready
        ):
            raise ValueError("the selected anchor is missing, damaged, or not approved")
        generation_pairs = list(
            self._resolved_reference_pairs(shot, target=ReferenceTarget.VIDEO)
        )
        if anchor is not None:
            generation_pairs = [
                pair
                for pair in generation_pairs
                if pair[1].id != anchor.id and pair[1].sha256 != anchor.sha256
            ]
        references = tuple(asset for _binding, asset in generation_pairs)
        ordered = (() if anchor is None else (anchor,)) + references
        if len(ordered) > 9:
            raise ValueError("Seedance最多允许9张图片输入（含锚点）")
        descriptions = (() if anchor is None else ("@图片1=批准锚点，锁定本片段开场状态",))
        descriptions += tuple(
            _video_reference_description(
                index,
                binding,
                scene_look_usage=shot.draft.scene_look_usage,
            )
            for index, (binding, _asset) in enumerate(
                generation_pairs,
                1 if anchor is None else 2,
            )
        )
        return anchor, references, descriptions

    def _resolved_reference_pairs(
        self,
        shot: StoredShot,
        *,
        target: ReferenceTarget,
    ) -> tuple[tuple[ReferenceBinding, StoredAsset], ...]:
        resolved: list[tuple[ReferenceBinding, StoredAsset]] = []
        seen_ids: set[uuid.UUID] = set()
        seen_hashes: set[str] = set()
        for binding in self._reference_bindings(shot, target=target):
            asset = self._repository.get_asset(binding.asset_id)
            if (
                asset.media_type != "image"
                or asset.status not in {"approved", "ready"}
                or not asset.content_ready
            ):
                raise ValueError("a selected generation reference is unavailable or not an image")
            if asset.id in seen_ids or asset.sha256 in seen_hashes:
                continue
            seen_ids.add(asset.id)
            seen_hashes.add(asset.sha256)
            resolved.append((binding, asset))
        return tuple(resolved)

    def _reference_bindings(
        self,
        shot: StoredShot,
        *,
        target: ReferenceTarget,
    ) -> tuple[ReferenceBinding, ...]:
        project = self._repository.get_project(shot.project_id)
        scene = self._repository.get_scene(shot.scene_id)
        return tuple(
            item
            for item in _merge_generation_references(
                custom=tuple(shot.draft.reference_bindings),
                scene_look_asset_id=scene.selected_look_asset_id,
                project_defaults=_project_reference_bindings(
                    self._repository,
                    shot,
                    scene,
                    project,
                ),
                inherit_project_references=shot.draft.inherit_project_references,
                scene_look_usage=shot.draft.scene_look_usage,
                target=target,
            )
            if item.apply_to in {target, ReferenceTarget.BOTH}
        )

    def _default_scene_look_draft(self, scene: StoredScene) -> SceneLookDraft:
        profile = self._repository.get_visual_profile(scene.project_id)
        look_plan = scene.draft.look_plan or SceneLookPlan()
        bindings: list[LookReferenceBinding] = []
        for binding in _order_look_bindings(profile.draft.reference_bindings):
            if binding.purpose is not LookReferencePurpose.STYLE:
                bindings.append(binding)
                continue
            asset = self._repository.get_asset(binding.asset_id)
            semantic_key = asset.semantic_key or ""
            if semantic_key in {"style:outdoor", "style:indoor"}:
                expected = f"style:{look_plan.environment_style.value}"
                if semantic_key != expected:
                    continue
            bindings.append(binding)
        return SceneLookDraft(
            visualProfileRevisionId=profile.id,
            lookPlan=look_plan,
            referenceBindings=bindings,
        )

    def _scene_look_inputs(self, scene_id: uuid.UUID, *, strict: bool) -> SceneLookInputSet:
        scene = self._repository.get_scene(scene_id)
        draft = scene.look_draft or self._default_scene_look_draft(scene)
        profile = self._repository.get_visual_profile_revision(
            draft.visual_profile_revision_id
        )
        if profile.project_id != scene.project_id:
            raise ValueError("场景定妆引用了其他项目的视觉档案")
        bindings: list[LookReferenceBinding] = []
        assets: list[StoredAsset] = []
        warnings: list[str] = []
        seen_ids: set[uuid.UUID] = set()
        seen_hashes: set[str] = set()
        for binding in _order_look_bindings(draft.reference_bindings):
            asset = self._repository.get_asset(binding.asset_id)
            if asset.id in seen_ids or asset.sha256 in seen_hashes:
                continue
            seen_ids.add(asset.id)
            seen_hashes.add(asset.sha256)
            bindings.append(binding)
            assets.append(asset)
            if (
                asset.media_type != "image"
                or asset.status not in {"approved", "ready"}
                or not asset.content_ready
            ):
                warnings.append(f"{asset.semantic_key or asset.id} 图片内容不可用")
        purposes = {item.purpose for item in bindings}
        required = {
            LookReferencePurpose.PERSON_IDENTITY: "至少选择一张人物身份参考",
            LookReferencePurpose.CAT_IDENTITY: "至少选择一张猫咪身份参考",
            LookReferencePurpose.STYLE: "至少选择一张画风参考",
        }
        warnings.extend(message for purpose, message in required.items() if purpose not in purposes)
        if len(assets) > 14:
            warnings.append("Seedream 最多允许 14 张参考图")
        if strict and warnings:
            raise ValueError("；".join(warnings))
        descriptions = tuple(
            _scene_look_reference_description(index, binding, asset)
            for index, (binding, asset) in enumerate(
                zip(bindings, assets, strict=True),
                1,
            )
        )
        return SceneLookInputSet(
            scene=scene,
            profile=profile,
            draft=draft,
            bindings=tuple(bindings),
            assets=tuple(assets),
            descriptions=descriptions,
            warnings=tuple(warnings),
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
        runtime_preflight: RuntimePreflight | None = None,
    ) -> None:
        self._repository = repository
        self._asset_store = asset_store
        self._media_probe = media_probe
        self._resolution = resolution
        self._runtime_preflight = runtime_preflight

    def build_project_sequence(self, project_id: uuid.UUID) -> StoredSequence:
        if self._runtime_preflight is not None:
            self._runtime_preflight.validate_for_local_composition()
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
        landed = self._asset_store.concatenate_videos(
            tuple(item[1].require_path() for item in selected)
        )
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


def _project_reference_bindings(
    repository: ShotQueueStore,
    shot: StoredShot,
    scene: StoredScene,
    project: StoredProject,
) -> tuple[ReferenceBinding, ...]:
    bindings = project.default_reference_bindings
    if not bindings:
        profile = repository.get_visual_profile(shot.project_id)
        bindings = tuple(
            ReferenceBinding(
                assetId=item.asset_id,
                usage=ReferenceUsage.GENERATION_REFERENCE,
                role=(
                    ReferenceRole.STYLE
                    if item.purpose is LookReferencePurpose.STYLE
                    else ReferenceRole.IDENTITY
                ),
                applyTo=ReferenceTarget.BOTH,
            )
            for item in profile.draft.reference_bindings
        )

    look_plan = (
        scene.look_draft.look_plan
        if scene.look_draft is not None
        else scene.draft.look_plan
    )
    expected_style = (
        None
        if look_plan is None
        else f"style:{look_plan.environment_style.value}"
    )
    filtered: list[ReferenceBinding] = []
    for binding in bindings:
        asset = repository.get_asset(binding.asset_id)
        if (
            expected_style is not None
            and asset.semantic_key in {"style:outdoor", "style:indoor"}
            and asset.semantic_key != expected_style
        ):
            continue
        filtered.append(binding)
    return tuple(filtered)


def _merge_generation_references(
    *,
    custom: tuple[ReferenceBinding, ...],
    scene_look_asset_id: uuid.UUID | None,
    project_defaults: tuple[ReferenceBinding, ...],
    inherit_project_references: bool,
    scene_look_usage: SceneLookUsage,
    target: ReferenceTarget,
) -> tuple[ReferenceBinding, ...]:
    ordered = [
        item
        for item in custom
        if item.usage is ReferenceUsage.GENERATION_REFERENCE
        and item.asset_id != scene_look_asset_id
    ]
    include_scene_look = scene_look_usage in {
        SceneLookUsage.APPEARANCE_ONLY,
        SceneLookUsage.FULL_REFERENCE,
    } or (
        scene_look_usage is SceneLookUsage.DERIVE_ANCHOR
        and target is ReferenceTarget.ANCHOR
    )
    if include_scene_look and scene_look_asset_id is not None:
        ordered.append(
            ReferenceBinding(
                assetId=scene_look_asset_id,
                usage=ReferenceUsage.GENERATION_REFERENCE,
                role=ReferenceRole.SCENE,
                applyTo=ReferenceTarget.BOTH,
            )
        )
    if inherit_project_references:
        ordered.extend(
            item
            for item in project_defaults
            if item.usage is ReferenceUsage.GENERATION_REFERENCE
            and item.asset_id != scene_look_asset_id
        )
    seen: set[uuid.UUID] = set()
    merged: list[ReferenceBinding] = []
    for item in ordered:
        if item.asset_id in seen:
            continue
        seen.add(item.asset_id)
        merged.append(item)
    return tuple(merged)


def _scene_story_snapshot(scene: StoredScene | None) -> dict[str, Any] | None:
    if scene is None:
        return None
    return {
        "sceneId": str(scene.id),
        "title": scene.draft.title,
        "sourceText": scene.draft.source_text,
    }


def _creative_step_json(step: StoredStep) -> dict[str, Any]:
    return {
        "stepId": str(step.id),
        "operationKey": step.operation_key,
        "status": step.status.value,
        "attempt": step.attempt,
        "model": step.model,
        "sourceHash": step.input_snapshot.get("sourceHash"),
        "providerOutput": step.input_snapshot.get("providerOutput"),
        "acceptedOutput": step.input_snapshot.get("acceptedOutput"),
        "acceptedAt": step.input_snapshot.get("acceptedAt"),
        "error": step.error,
        "createdAt": None if step.created_at is None else step.created_at.isoformat(),
    }


def _scene_look_reference_description(
    index: int,
    binding: LookReferenceBinding,
    asset: StoredAsset,
) -> str:
    responsibilities = {
        LookReferencePurpose.PERSON_IDENTITY: "只锁定人物脸型、五官、肤色和发型，忽略旧服装与背景",
        LookReferencePurpose.PERSON_BODY: "只锁定人物年龄感、身高和头身比例，忽略旧服装与姿态",
        LookReferencePurpose.CAT_IDENTITY: "只锁定猫咪脸部、毛色分区、虎斑、眼睛、尾巴和体型",
        LookReferencePurpose.STYLE: "只锁定线条、材质、色彩、自然光和景深，不改写角色身份",
        LookReferencePurpose.WARDROBE: "只参考本场景服装款式和材质，不替换人物身份",
        LookReferencePurpose.PROP: "只参考关键道具的外观、结构和比例",
        LookReferencePurpose.COMPOSITION: "只参考构图、机位和主体空间关系",
    }
    semantic = asset.semantic_key or f"asset:{asset.id}"
    instruction = f"；补充：{binding.instruction}" if binding.instruction else ""
    return f"@图片{index}={semantic}；{responsibilities[binding.purpose]}{instruction}"


def _video_reference_description(
    index: int,
    binding: ReferenceBinding,
    *,
    scene_look_usage: SceneLookUsage = SceneLookUsage.APPEARANCE_ONLY,
) -> str:
    scene_responsibilities = {
        SceneLookUsage.OFF: "场景基础定妆已禁用",
        SceneLookUsage.APPEARANCE_ONLY: (
            "场景基础定妆，只继承服饰、配件、环境基调和共同道具；"
            "忽略定妆图中的姿态、动作结果和构图"
        ),
        SceneLookUsage.FULL_REFERENCE: (
            "场景基础定妆，完整参考本场服装、道具、姿态和构图"
        ),
        SceneLookUsage.DERIVE_ANCHOR: (
            "场景基础定妆，用于派生本片段开场状态；角色身份仍由项目身份图负责"
        ),
    }
    responsibilities = {
        ReferenceRole.IDENTITY: "项目角色身份，只锁定人物或猫咪的长期外观",
        ReferenceRole.STYLE: "项目系列画风，只锁定线条、材质、色彩和光线",
        ReferenceRole.SCENE: scene_responsibilities[scene_look_usage],
        ReferenceRole.PROP: "本片段道具外观、结构和比例",
        ReferenceRole.COMPOSITION: "本片段构图、机位和主体空间关系",
    }
    return (
        f"@图片{index}={responsibilities[binding.role]}；"
        "只承担已声明职责，不改写其他主体或长期身份"
    )


def _order_look_bindings(
    bindings: list[LookReferenceBinding],
) -> tuple[LookReferenceBinding, ...]:
    purpose_order = {
        purpose: index
        for index, purpose in enumerate(
            (
                LookReferencePurpose.PERSON_IDENTITY,
                LookReferencePurpose.PERSON_BODY,
                LookReferencePurpose.CAT_IDENTITY,
                LookReferencePurpose.STYLE,
                LookReferencePurpose.WARDROBE,
                LookReferencePurpose.PROP,
                LookReferencePurpose.COMPOSITION,
            )
        )
    }
    return tuple(
        binding
        for _original_order, binding in sorted(
            enumerate(bindings),
            key=lambda item: (purpose_order[item[1].purpose], item[0]),
        )
    )


def _validate_suggestion_count(output: ShotSuggestionOutput, target_count: int) -> None:
    if len(output.shots) != target_count:
        raise ValueError(
            f"导演建议返回{len(output.shots)}个视频片段，但当前场景要求{target_count}个"
        )


def _shot_assist_asset_layer(
    shot: StoredShot,
    scene: StoredScene,
    project: StoredProject,
    asset: StoredAsset,
) -> str:
    if asset.role == "shot_tail_frame":
        return "previous_tail"
    if shot.selected_anchor_asset_id == asset.id:
        return "shot"
    if scene.selected_look_asset_id == asset.id:
        return "scene_look"
    if any(binding.asset_id == asset.id for binding in shot.draft.reference_bindings):
        return "shot"
    if any(binding.asset_id == asset.id for binding in project.default_reference_bindings):
        return "project"
    return "candidate"


def _shot_assist_asset_responsibility(shot: StoredShot, asset: StoredAsset) -> str:
    if shot.selected_anchor_asset_id == asset.id:
        return "批准锚点：锁定当前片段开场状态"
    if asset.role == "shot_tail_frame":
        return "上一片段尾帧：只用于判断连续衔接"
    if asset.role == "scene_look":
        return "场景基础定妆：只承担当前场景造型与共同视觉基线"
    return asset.reference_purpose or asset.role


def _previous_tail_state(repository: ShotQueueStore, shot: StoredShot) -> PreviousTailState:
    ordered = sorted(
        repository.list_shots(shot.scene_id),
        key=lambda item: item.order,
    )
    current_index = next(index for index, item in enumerate(ordered) if item.id == shot.id)
    if current_index == 0:
        return PreviousTailState(None, None, None, None, False)
    previous = ordered[current_index - 1]
    source_video_id = previous.selected_video_asset_id
    bound: StoredAsset | None = None
    for binding in shot.draft.reference_bindings:
        if binding.usage is not ReferenceUsage.APPROVED_ANCHOR:
            continue
        candidate = repository.get_asset(binding.asset_id)
        if candidate.role == "shot_tail_frame":
            bound = candidate
            break
    source_id_text = None if source_video_id is None else str(source_video_id)
    stale = bool(
        bound is not None
        and bound.metadata.get("sourceVideoAssetId") != source_id_text
    )
    active = next(
        (
            asset
            for asset in reversed(repository.list_assets(shot_id=previous.id))
            if asset.role == "shot_tail_frame"
            and asset.metadata.get("sourceVideoAssetId") == source_id_text
            and asset.content_ready
        ),
        None,
    )
    return PreviousTailState(previous, source_video_id, active, bound, stale)


def _tail_state_json(state: PreviousTailState) -> dict[str, Any]:
    if state.previous_shot is None:
        return {"available": False, "reason": "first_shot", "stale": False}
    return {
        "available": state.active is not None,
        "previousShotId": str(state.previous_shot.id),
        "sourceVideoAssetId": (
            None if state.source_video_id is None else str(state.source_video_id)
        ),
        "assetId": None if state.active is None else str(state.active.id),
        "boundAssetId": None if state.bound is None else str(state.bound.id),
        "stale": state.stale,
    }


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
