"""Offline V5 creation-flow data-chain smoke test.

The script uses in-memory workflow state, deterministic provider substitutes
and local FFmpeg fixtures.  It never reads ARK_API_KEY and never sends a
network request.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from PIL import Image

from cat_video_generator.application.ports import (
    DirectorResult,
    GatewayError,
    ImageResult,
    LandedAsset,
    StoredAsset,
    StoredProject,
    StoredPrompt,
    StoredReview,
    StoredScene,
    StoredSequence,
    StoredShot,
    StoredStep,
    VideoDiagnosticResult,
    VideoTaskResult,
)
from cat_video_generator.application.shot_queue import (
    ProjectEditingService,
    SequenceService,
    ShotProductionService,
)
from cat_video_generator.bootstrap import build_runtime_container
from cat_video_generator.config import load_local_env
from cat_video_generator.domain.contracts import (
    AnchorMode,
    ReferenceBinding,
    ReferenceRole,
    ReferenceTarget,
    ReferenceUsage,
    SceneDraft,
    SceneLookPlan,
    ShotCardDraft,
    StoryMode,
    StoryProjectInput,
)
from cat_video_generator.domain.rendering import (
    ProjectSequencePlan,
    RenderOperation,
    SequenceStatus,
    VideoInputPlan,
)
from cat_video_generator.domain.workflow import (
    PromptPurpose,
    RunStatus,
    SceneStatus,
    ShotStatus,
    StepKind,
    StepStatus,
    transition_step,
)
from cat_video_generator.infrastructure.media.qc import (
    FfmpegFrameExtractor,
    FfprobeMediaProbe,
)
from cat_video_generator.infrastructure.media.storage import LocalAssetStore


class MemoryStore:
    def __init__(self) -> None:
        self.projects: dict[uuid.UUID, StoredProject] = {}
        self.scenes: dict[uuid.UUID, StoredScene] = {}
        self.shots: dict[uuid.UUID, StoredShot] = {}
        self.steps: dict[uuid.UUID, StoredStep] = {}
        self.prompts: dict[uuid.UUID, StoredPrompt] = {}
        self.assets: dict[uuid.UUID, StoredAsset] = {}
        self.reviews: dict[uuid.UUID, StoredReview] = {}
        self.sequences: dict[uuid.UUID, StoredSequence] = {}
        self._idempotency: dict[str, uuid.UUID] = {}

    def create_project(self, source: StoryProjectInput, *, content_date: date) -> StoredProject:
        project = StoredProject(uuid.uuid4(), source.title, content_date, RunStatus.ACTIVE)
        self.projects[project.id] = project
        scene = StoredScene(
            uuid.uuid4(),
            project.id,
            1,
            SceneDraft(title=source.first_scene_title, sourceText=source.first_scene_text),
            SceneStatus.DRAFT,
        )
        self.scenes[scene.id] = scene
        return project

    def list_projects(self) -> tuple[StoredProject, ...]:
        return tuple(self.projects.values())

    def update_project(
        self,
        project_id: uuid.UUID,
        *,
        title: str,
        content_date: date,
    ) -> StoredProject:
        self.projects[project_id] = replace(
            self.projects[project_id],
            title=title,
            content_date=content_date,
        )
        return self.projects[project_id]

    def get_project(self, project_id: uuid.UUID) -> StoredProject:
        return self.projects[project_id]

    def update_project_default_references(
        self,
        project_id: uuid.UUID,
        references: list[ReferenceBinding] | tuple[ReferenceBinding, ...],
    ) -> StoredProject:
        self.projects[project_id] = replace(
            self.projects[project_id],
            default_reference_bindings=tuple(references),
        )
        return self.projects[project_id]

    def add_scene(self, project_id: uuid.UUID, draft: SceneDraft) -> StoredScene:
        scene = StoredScene(
            uuid.uuid4(),
            project_id,
            len(self.list_scenes(project_id)) + 1,
            draft,
            SceneStatus.DRAFT,
        )
        self.scenes[scene.id] = scene
        return scene

    def update_scene(self, scene_id: uuid.UUID, draft: SceneDraft) -> StoredScene:
        self.scenes[scene_id] = replace(self.scenes[scene_id], draft=draft)
        return self.scenes[scene_id]

    def delete_scene(self, scene_id: uuid.UUID) -> None:
        del self.scenes[scene_id]

    def reorder_scenes(self, project_id: uuid.UUID, scene_ids: tuple[uuid.UUID, ...]) -> None:
        for order, scene_id in enumerate(scene_ids, 1):
            self.scenes[scene_id] = replace(self.scenes[scene_id], order=order)

    def list_scenes(self, project_id: uuid.UUID) -> tuple[StoredScene, ...]:
        return tuple(
            sorted(
                (item for item in self.scenes.values() if item.project_id == project_id),
                key=lambda item: item.order,
            )
        )

    def get_scene(self, scene_id: uuid.UUID) -> StoredScene:
        return self.scenes[scene_id]

    def select_scene_look_asset(
        self,
        scene_id: uuid.UUID,
        asset_id: uuid.UUID | None,
    ) -> StoredScene:
        self.scenes[scene_id] = replace(
            self.scenes[scene_id],
            selected_look_asset_id=asset_id,
        )
        return self.scenes[scene_id]

    def add_shot(self, scene_id: uuid.UUID, draft: ShotCardDraft) -> StoredShot:
        scene = self.scenes[scene_id]
        shot = StoredShot(
            uuid.uuid4(),
            scene_id,
            scene.project_id,
            len(self.list_shots(scene_id)) + 1,
            draft,
            ShotStatus.READY,
        )
        self.shots[shot.id] = shot
        return shot

    def replace_shots(
        self, scene_id: uuid.UUID, drafts: tuple[ShotCardDraft, ...]
    ) -> tuple[StoredShot, ...]:
        for shot in self.list_shots(scene_id):
            del self.shots[shot.id]
        return tuple(self.add_shot(scene_id, draft) for draft in drafts)

    def accept_scene_suggestions(
        self,
        *,
        step_id: uuid.UUID,
        drafts: tuple[ShotCardDraft, ...],
        look_plan: SceneLookPlan | None,
        accepted_output: dict[str, Any],
    ) -> tuple[StoredShot, ...]:
        step = self.steps[step_id]
        if step.scene_id is None:
            raise ValueError("suggestion step is not bound to a scene")
        scene = self.scenes[step.scene_id]
        shots = self.replace_shots(scene.id, drafts)
        self.scenes[scene.id] = replace(
            scene,
            draft=scene.draft.model_copy(update={"look_plan": look_plan}),
        )
        self.steps[step_id] = replace(
            step,
            input_snapshot={
                **step.input_snapshot,
                "acceptedOutput": accepted_output,
                "acceptedAt": datetime.now(UTC).isoformat(),
            },
        )
        return shots

    def update_shot(self, shot_id: uuid.UUID, draft: ShotCardDraft) -> StoredShot:
        self.shots[shot_id] = replace(self.shots[shot_id], draft=draft, status=ShotStatus.READY)
        return self.shots[shot_id]

    def delete_shot(self, shot_id: uuid.UUID) -> None:
        del self.shots[shot_id]

    def reorder_shots(self, scene_id: uuid.UUID, shot_ids: tuple[uuid.UUID, ...]) -> None:
        for order, shot_id in enumerate(shot_ids, 1):
            self.shots[shot_id] = replace(self.shots[shot_id], order=order)

    def list_shots(self, scene_id: uuid.UUID) -> tuple[StoredShot, ...]:
        return tuple(
            sorted(
                (item for item in self.shots.values() if item.scene_id == scene_id),
                key=lambda item: item.order,
            )
        )

    def get_shot(self, shot_id: uuid.UUID) -> StoredShot:
        return self.shots[shot_id]

    def next_attempt(self, *, shot_id: uuid.UUID, operation_key: str) -> int:
        attempts = [
            item.attempt
            for item in self.steps.values()
            if item.shot_card_id == shot_id and item.operation_key == operation_key
        ]
        return max(attempts, default=0) + 1

    def next_scene_attempt(self, *, scene_id: uuid.UUID, operation_key: str) -> int:
        attempts = [
            item.attempt
            for item in self.steps.values()
            if item.scene_id == scene_id
            and item.shot_card_id is None
            and item.operation_key == operation_key
        ]
        return max(attempts, default=0) + 1

    def create_step_with_prompt(
        self,
        *,
        project_id: uuid.UUID,
        scene_id: uuid.UUID | None,
        shot_id: uuid.UUID | None,
        kind: StepKind,
        operation_key: str,
        attempt: int,
        provider: str,
        model: str,
        input_hash: str,
        input_snapshot: dict[str, Any],
        purpose: PromptPurpose,
        prompt_text: str,
    ) -> tuple[StoredStep, StoredPrompt]:
        del provider
        key = f"{project_id}:{shot_id or scene_id}:{operation_key}:{attempt}:{input_hash}"
        if key in self._idempotency:
            step = self.steps[self._idempotency[key]]
            return step, self.prompts[step.id]
        step = StoredStep(
            uuid.uuid4(),
            project_id,
            scene_id,
            shot_id,
            kind,
            StepStatus.PENDING,
            attempt,
            operation_key,
            input_snapshot,
            model=model,
            created_at=datetime.now(UTC),
        )
        prompt = StoredPrompt(
            uuid.uuid4(),
            step.id,
            purpose,
            model,
            prompt_text,
            hashlib.sha256(prompt_text.encode()).hexdigest(),
        )
        self.steps[step.id] = step
        self.prompts[step.id] = prompt
        self._idempotency[key] = step.id
        return step, prompt

    def update_step(
        self,
        step_id: uuid.UUID,
        *,
        status: StepStatus,
        task_id: str | None = None,
        error: dict[str, Any] | None = None,
        input_snapshot: dict[str, Any] | None = None,
    ) -> StoredStep:
        current = self.steps[step_id]
        transition_step(current.status, status)
        if task_id is not None and any(
            item.id != step_id and item.provider_task_id == task_id for item in self.steps.values()
        ):
            raise ValueError("provider task is already bound")
        self.steps[step_id] = replace(
            current,
            status=status,
            provider_task_id=task_id or current.provider_task_id,
            error=error if error is not None else current.error,
            input_snapshot=input_snapshot or current.input_snapshot,
        )
        return self.steps[step_id]

    def get_step(self, step_id: uuid.UUID) -> StoredStep:
        return self.steps[step_id]

    def list_steps(
        self,
        *,
        project_id: uuid.UUID,
        scene_id: uuid.UUID | None = None,
        shot_id: uuid.UUID | None = None,
    ) -> tuple[StoredStep, ...]:
        return tuple(
            item
            for item in self.steps.values()
            if item.project_id == project_id
            and (scene_id is None or item.scene_id == scene_id)
            and (shot_id is None or item.shot_card_id == shot_id)
        )

    def get_prompt(self, step_id: uuid.UUID) -> StoredPrompt | None:
        return self.prompts.get(step_id)

    def add_asset(
        self,
        *,
        landed: LandedAsset,
        role: str,
        media_type: str,
        scope: str,
        status: str,
        project_id: uuid.UUID | None,
        scene_id: uuid.UUID | None,
        shot_id: uuid.UUID | None,
        step_id: uuid.UUID | None,
        semantic_key: str | None,
        metadata: dict[str, Any],
    ) -> StoredAsset:
        asset = StoredAsset(
            uuid.uuid4(),
            project_id,
            scene_id,
            shot_id,
            step_id,
            role,
            media_type,
            scope,
            status,
            landed.path,
            landed.sha256,
            metadata,
            semantic_key,
        )
        self.assets[asset.id] = asset
        return asset

    def get_asset(self, asset_id: uuid.UUID) -> StoredAsset:
        return self.assets[asset_id]

    def list_assets(
        self,
        *,
        project_id: uuid.UUID | None = None,
        shot_id: uuid.UUID | None = None,
        include_canon: bool = False,
    ) -> tuple[StoredAsset, ...]:
        return tuple(
            item
            for item in self.assets.values()
            if (
                project_id is None
                or item.project_id == project_id
                or (include_canon and item.scope == "canon")
            )
            and (shot_id is None or item.shot_card_id == shot_id)
        )

    def select_shot_asset(
        self, shot_id: uuid.UUID, *, kind: str, asset_id: uuid.UUID
    ) -> StoredShot:
        shot = self.shots[shot_id]
        asset = self.assets[asset_id]
        if asset.status not in {"approved", "ready"}:
            raise ValueError("asset is not approved")
        update = (
            {"selected_anchor_asset_id": asset_id, "status": ShotStatus.VIDEO_PENDING}
            if kind == "anchor"
            else {"selected_video_asset_id": asset_id, "status": ShotStatus.APPROVED}
        )
        self.shots[shot_id] = replace(shot, **update)
        return self.shots[shot_id]

    def add_review(
        self,
        *,
        step_id: uuid.UUID,
        asset_id: uuid.UUID | None,
        source: str,
        decision: str,
        reason: str | None,
        warnings: tuple[dict[str, Any], ...],
        evidence: dict[str, Any],
    ) -> StoredReview:
        review = StoredReview(
            uuid.uuid4(), step_id, asset_id, source, decision, reason, warnings, evidence
        )
        self.reviews[review.id] = review
        return review

    def decide_asset(
        self, asset_id: uuid.UUID, *, decision: str, reason: str | None
    ) -> StoredAsset:
        asset = self.assets[asset_id]
        if asset.step_id is None:
            raise ValueError("imported references are not reviewed")
        step = self.steps[asset.step_id]
        if step.status is not StepStatus.AWAITING_REVIEW:
            if asset.status == decision:
                return asset
            raise ValueError("asset is not awaiting review")
        self.assets[asset_id] = replace(asset, status=decision)
        self.steps[step.id] = replace(
            step,
            status=StepStatus.SUCCEEDED if decision == "approved" else StepStatus.FAILED,
        )
        self.add_review(
            step_id=step.id,
            asset_id=asset.id,
            source="human",
            decision=decision,
            reason=reason,
            warnings=(),
            evidence={},
        )
        return self.assets[asset_id]

    def list_reviews(self, step_id: uuid.UUID) -> tuple[StoredReview, ...]:
        return tuple(item for item in self.reviews.values() if item.step_id == step_id)

    def create_sequence(
        self,
        *,
        project_id: uuid.UUID,
        plan: ProjectSequencePlan,
        parent_sequence_id: uuid.UUID | None,
        rendered_asset_id: uuid.UUID | None,
        status: SequenceStatus,
    ) -> StoredSequence:
        sequence = StoredSequence(
            uuid.uuid4(),
            project_id,
            len(self.list_sequences(project_id)) + 1,
            parent_sequence_id,
            rendered_asset_id,
            status,
            plan,
            datetime.now(UTC),
        )
        self.sequences[sequence.id] = sequence
        return sequence

    def list_sequences(self, project_id: uuid.UUID) -> tuple[StoredSequence, ...]:
        return tuple(item for item in self.sequences.values() if item.project_id == project_id)

    def select_sequence(self, project_id: uuid.UUID, sequence_id: uuid.UUID) -> StoredSequence:
        sequence = self.sequences[sequence_id]
        if sequence.status is not SequenceStatus.APPROVED:
            raise ValueError("sequence is not approved")
        self.projects[project_id] = replace(
            self.projects[project_id], selected_sequence_id=sequence_id
        )
        return sequence

    def decide_sequence(self, sequence_id: uuid.UUID, *, approved: bool) -> StoredSequence:
        sequence = self.sequences[sequence_id]
        self.sequences[sequence_id] = replace(
            sequence,
            status=SequenceStatus.APPROVED if approved else SequenceStatus.REJECTED,
        )
        return self.sequences[sequence_id]

    def shot_trace(self, shot_id: uuid.UUID) -> dict[str, Any]:
        shot = self.get_shot(shot_id)
        attempts = self.list_steps(project_id=shot.project_id, shot_id=shot.id)
        return {
            "id": str(shot.id),
            "title": shot.draft.title,
            "direction": shot.draft.direction,
            "assets": [
                {"id": str(item.id), "role": item.role, "status": item.status}
                for item in self.list_assets(shot_id=shot.id)
            ],
            "attempts": [
                {
                    "id": str(item.id),
                    "status": item.status.value,
                    "prompt": self.prompts[item.id].text,
                    "reviews": [review.decision for review in self.list_reviews(item.id)],
                }
                for item in attempts
            ],
        }

    def project_graph(self, project_id: uuid.UUID) -> dict[str, Any]:
        return {
            "project": {"id": str(project_id), "title": self.projects[project_id].title},
            "scenes": [
                {
                    "id": str(scene.id),
                    "title": scene.draft.title,
                    "shots": [self.shot_trace(shot.id) for shot in self.list_shots(scene.id)],
                }
                for scene in self.list_scenes(project_id)
            ],
            "sequences": [
                {
                    "id": str(item.id),
                    "revision": item.revision,
                    "status": item.status.value,
                }
                for item in self.list_sequences(project_id)
            ],
        }


class FixtureDirector:
    model = "fixture-director"

    def generate_structured(
        self, *, prompt: str, schema: dict[str, Any], output_name: str
    ) -> DirectorResult:
        del schema, output_name
        match = re.search(r"严格输出(\d+)个视频片段", prompt)
        count = int(match.group(1)) if match else 1
        suggestions = [
            {
                "title": f"猫咪主导的生活片段{index}",
                "direction": (
                    "1. 中景固定机位，猫咪位于人物前侧先观察目标，人物保持在后方准备。\n"
                    "2. 近景轻微跟随猫咪自然四足靠近，人物用手拿取所需道具并配合。\n"
                    "3. 中景固定收尾，人猫完成同一微事件的因果互动，环境声与接触声同步。"
                ),
                "suggestedDurationSeconds": 8 + index,
            }
            for index in range(1, count + 1)
        ]
        return DirectorResult(
            payload={
                "sceneTitle": "池塘边的小发现",
                "lookPlan": {
                    "personWardrobe": "浅色户外外套",
                    "personAccessories": "帆布包",
                    "catAppearance": "保持Canon外观且不增加服饰",
                    "keyProps": "鱼竿与小水桶",
                    "imageRecommended": True,
                    "recommendationReason": "多片段复用服装和关键道具",
                },
                "shots": suggestions,
            },
            response_id="fixture-response",
            model=self.model,
            request_hash="fixture-request",
        )


class FixtureGateway:
    image_model = "fixture-image"
    video_model = "fixture-video"
    review_model = "fixture-review"

    def __init__(self) -> None:
        self.submissions: list[VideoInputPlan] = []
        self.image_submissions = 0
        self.fail_unknown_once = False

    def generate_image(self, *, prompt: str, reference_paths: tuple[Path, ...]) -> ImageResult:
        del prompt, reference_paths
        self.image_submissions += 1
        return ImageResult("https://fixture.local/anchor.png", self.image_model)

    def submit_video(
        self,
        *,
        prompt: str,
        input_plan: VideoInputPlan,
        input_sources: tuple[Path | str, ...],
    ) -> VideoTaskResult:
        del prompt, input_sources
        self.submissions.append(input_plan)
        if self.fail_unknown_once:
            self.fail_unknown_once = False
            raise GatewayError(
                "fixture submission outcome is unknown",
                code="fixture_timeout",
                retryable=False,
                submission_unknown=True,
            )
        suffix = "edit.mp4" if input_plan.operation is RenderOperation.EDIT else "shot.mp4"
        return VideoTaskResult(
            task_id=f"fixture-task-{len(self.submissions)}",
            status="succeeded",
            video_url=f"https://fixture.local/{suffix}",
            duration_seconds=input_plan.duration_seconds,
            resolution=input_plan.resolution,
        )

    def get_video_task(self, task_id: str) -> VideoTaskResult:
        return VideoTaskResult(task_id, "succeeded", "https://fixture.local/shot.mp4")

    def list_video_tasks(self, *, model: str, page_size: int = 100) -> tuple[VideoTaskResult, ...]:
        del model, page_size
        return ()

    def diagnose_video_frames(
        self, *, prompt: str, frame_paths: tuple[Path, ...]
    ) -> VideoDiagnosticResult:
        del prompt
        return VideoDiagnosticResult(
            identity_ok=True,
            style_ok=True,
            constraints_ok=True,
            narrative_order_ok=True,
            confidence=0.91,
            violations=(),
            evidence=tuple(
                {
                    "timestamp": f"{index}s",
                    "object": "shot",
                    "observation": "fixture frame available",
                    "relationError": None,
                }
                for index, _path in enumerate(frame_paths)
            ),
            shot_boundaries_seconds=(0.0, 4.0, 8.0),
            response_id="fixture-review",
            model=self.review_model,
            request_hash="fixture-review-request",
        )


class FixtureAssetStore:
    def __init__(self, local: LocalAssetStore, fixtures: dict[str, Path]) -> None:
        self._local = local
        self._fixtures = fixtures

    def download(self, url: str, *, suffix: str) -> LandedAsset:
        del suffix
        return self._local.import_local(self._fixtures[url])

    def import_local(self, path: Path) -> LandedAsset:
        return self._local.import_local(path)

    def concatenate_videos(self, paths: tuple[Path, ...]) -> LandedAsset:
        return self._local.concatenate_videos(paths)

    def render_range_replacement(
        self,
        *,
        base_path: Path,
        replacement_path: Path,
        replacement_duration_ms: int,
        start_ms: int,
        end_ms: int,
    ) -> LandedAsset:
        return self._local.render_range_replacement(
            base_path=base_path,
            replacement_path=replacement_path,
            replacement_duration_ms=replacement_duration_ms,
            start_ms=start_ms,
            end_ms=end_ms,
        )


def main() -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("local smoke requires ffmpeg and ffprobe")
    sample_path = (Path(__file__).parents[1] / "docs" / "采茶叶.mp4").resolve()
    if not sample_path.is_file():
        raise RuntimeError(f"local sample is missing: {sample_path}")
    database_canon = _verify_database_canon()
    with tempfile.TemporaryDirectory(prefix="cvg-v5-smoke-") as temporary:
        root = Path(temporary)
        fixtures = _fixtures(root, ffmpeg)
        repository = MemoryStore()
        gateway = FixtureGateway()
        local_store = LocalAssetStore(
            work_root=root / "work",
            asset_root=root / "assets",
            ffmpeg_path=Path(ffmpeg),
        )
        store = FixtureAssetStore(local_store, fixtures)
        probe = FfprobeMediaProbe(Path(ffprobe))
        extractor = FfmpegFrameExtractor(ffmpeg_path=Path(ffmpeg), work_root=root / "frames")
        sample_probe = _probe_json(ffprobe, sample_path)
        _require(
            any(item.get("codec_type") == "video" for item in sample_probe.get("streams", [])),
            "采茶叶.mp4 has no readable video stream",
        )
        normalized_sample = _normalize_sample(ffmpeg, sample_path, root / "采茶叶-normalized.mp4")
        sample_master = local_store.concatenate_videos((normalized_sample, normalized_sample))
        _require(sample_master.path.is_file(), "采茶叶.mp4 local composition was not landed")
        composed_probe = _probe_json(ffprobe, sample_master.path)
        _require(
            any(item.get("codec_type") == "video" for item in composed_probe.get("streams", [])),
            "composed sample asset is unreadable",
        )
        editing = ProjectEditingService(
            repository=repository,
            director=FixtureDirector(),
            provider_name="fixture",
        )
        production = ShotProductionService(
            repository=repository,
            gateway=gateway,
            asset_store=store,
            media_probe=probe,
            frame_extractor=extractor,
            provider_name="fixture",
            resolution="480p",
        )
        sequences = SequenceService(
            repository=repository,
            asset_store=store,
            media_probe=probe,
            resolution="480p",
        )

        created = editing.create_project(
            StoryProjectInput(
                title="离线钓鱼镜头队列",
                firstSceneTitle="池塘边",
                firstSceneText="灰白猫先发现浮标轻晃，人物随后稳定持竿回应，最后一起观察水面。",
            ),
            content_date=date(2026, 8, 12),
        )
        project_id = uuid.UUID(created["projectId"])
        repository.update_project(
            project_id,
            title="离线钓鱼片段项目",
            content_date=date(2026, 8, 13),
        )
        _require(
            repository.get_project(project_id).content_date == date(2026, 8, 13),
            "project settings did not flow through storage",
        )
        scene = repository.list_scenes(project_id)[0]
        scene = repository.update_scene(
            scene.id,
            scene.draft.model_copy(
                update={"story_mode": StoryMode.MULTI, "target_shot_count": 2}
            ),
        )
        _require(
            not repository.list_steps(project_id=project_id), "project creation created a step"
        )

        suggestion = editing.suggest_shots(scene.id, allow_paid_generation=True)
        edited_look = suggestion.output.look_plan.model_copy(
            update={"person_wardrobe": "人工调整后的米白外套"}
        )
        edited_suggestions = tuple(
            item.model_copy(
                update={
                    "title": f"人工编辑：{item.title}",
                    "direction": f"{item.direction}\n人工收尾：猫咪回看人物后稳定结束。",
                    "suggested_duration_seconds": 10 + index,
                }
            )
            for index, item in enumerate(suggestion.output.shots)
        )
        shots = list(
            editing.accept_suggestions(
                suggestion.step_id,
                look_plan=edited_look,
                shots=edited_suggestions,
            )
        )
        _require(len(shots) == 2, "fixture suggestions were not accepted")
        accepted_step = repository.get_step(suggestion.step_id)
        _require(
            "providerOutput" in accepted_step.input_snapshot,
            "provider output was overwritten",
        )
        _require(
            "acceptedOutput" in accepted_step.input_snapshot,
            "accepted output was not audited",
        )
        _require("acceptedAt" in accepted_step.input_snapshot, "acceptance timestamp is missing")
        _require(
            accepted_step.input_snapshot["providerOutput"]
            != accepted_step.input_snapshot["acceptedOutput"],
            "edited suggestion was not distinct from provider output",
        )
        single_scene = repository.add_scene(
            project_id,
            SceneDraft(
                title="收束片段",
                sourceText="猫咪回到人物脚边，人物放下鱼竿并轻摸猫咪头顶。",
                storyMode="single",
                targetShotCount=1,
            ),
        )
        single_suggestion = editing.suggest_shots(
            single_scene.id,
            allow_paid_generation=True,
        )
        single_shots = editing.accept_suggestions(
            single_suggestion.step_id,
            look_plan=single_suggestion.output.look_plan,
            shots=single_suggestion.output.shots,
        )
        _require(len(single_shots) == 1, "single mode did not create exactly one clip")
        third = repository.update_shot(
            single_shots[0].id,
            single_shots[0].draft.model_copy(update={"anchor_mode": AnchorMode.GENERATE}),
        )
        shots.append(third)

        approved_anchor = production.import_reference(
            project_id=project_id,
            path=fixtures["https://fixture.local/anchor.png"],
            usage="approved_anchor",
            role="composition",
        )
        existing_draft = shots[1].draft.model_copy(
            update={
                "anchor_mode": AnchorMode.EXISTING,
                "reference_bindings": [
                    ReferenceBinding(
                        assetId=approved_anchor.id,
                        usage=ReferenceUsage.APPROVED_ANCHOR,
                        role=ReferenceRole.COMPOSITION,
                        applyTo=ReferenceTarget.VIDEO,
                    )
                ],
            }
        )
        shots[1] = repository.update_shot(shots[1].id, existing_draft)
        generation_reference = production.import_reference(
            project_id=project_id,
            path=fixtures["https://fixture.local/anchor.png"],
            usage="generation_reference",
            role="identity",
        )
        repository.update_project_default_references(
            project_id,
            (
                ReferenceBinding(
                    assetId=generation_reference.id,
                    usage=ReferenceUsage.GENERATION_REFERENCE,
                    role=ReferenceRole.IDENTITY,
                    applyTo=ReferenceTarget.BOTH,
                ),
            ),
        )
        repository.select_scene_look_asset(single_scene.id, approved_anchor.id)
        generated_draft = shots[2].draft.model_copy(
            update={
                "reference_bindings": [
                    ReferenceBinding(
                        assetId=generation_reference.id,
                        usage=ReferenceUsage.GENERATION_REFERENCE,
                        role=ReferenceRole.IDENTITY,
                        applyTo=ReferenceTarget.BOTH,
                    )
                ]
            }
        )
        shots[2] = repository.update_shot(shots[2].id, generated_draft)

        anchor_result = production.generate_anchor(shots[2].id, allow_paid_generation=True)
        production.decide_asset(
            uuid.UUID(anchor_result["assetId"]),
            decision="approved",
            reason="offline fixture",
            select=True,
        )

        approved_videos: list[uuid.UUID] = []
        for shot in shots:
            preview = production.preview_shot_prompt(shot.id)
            _require(preview["prompt"].strip(), "compiled prompt is empty")
            result = production.generate_video(shot.id, allow_paid_generation=True)
            asset_id = uuid.UUID(result["assetId"])
            production.decide_asset(
                asset_id,
                decision="approved",
                reason="offline fixture",
                select=True,
            )
            approved_videos.append(asset_id)

        modes = [item.operation for item in gateway.submissions[:3]]
        _require(modes == [RenderOperation.SHOT] * 3, "shot requests used a wrong operation")
        _require(
            len(gateway.submissions[0].bindings) == 1,
            "project default reference was not inherited",
        )
        _require(
            len(gateway.submissions[1].bindings) == 2,
            "existing anchor and project reference were not both sent",
        )
        _require(
            len(gateway.submissions[2].bindings) == 3,
            "generated anchor/custom-scene-project reference order or deduplication failed",
        )

        regenerated = production.generate_video(
            shots[0].id,
            allow_paid_generation=True,
            regenerate=True,
            reason="offline explicit regeneration",
        )
        regenerated_id = uuid.UUID(regenerated["assetId"])
        production.decide_asset(
            regenerated_id,
            decision="approved",
            reason="offline regenerated version",
            select=True,
        )
        video_attempts = [
            item
            for item in repository.list_steps(project_id=project_id, shot_id=shots[0].id)
            if item.operation_key == "video:shot"
        ]
        _require([item.attempt for item in video_attempts] == [1, 2], "attempt history was lost")
        reused = production.generate_video(shots[0].id, allow_paid_generation=True)
        _require(reused.get("reused") is True, "same input was charged again")

        sequence = sequences.build_project_sequence(project_id)
        _require(sequence.plan.duration_ms >= 23_000, "project sequence is too short")
        repository.decide_sequence(sequence.id, approved=True)
        repository.select_sequence(project_id, sequence.id)

        source = repository.get_asset(regenerated_id)
        edit_result = production.range_edit(
            shots[0].id,
            source_asset_id=source.id,
            start_ms=1500,
            end_ms=3500,
            instruction="保持人物和猫咪不变，只修复钓线连接方向",
            allow_paid_generation=True,
        )
        edited_id = uuid.UUID(edit_result["assetId"])
        edited = repository.get_asset(edited_id)
        _require(edited.metadata.get("rangeEdit") is not None, "range revision metadata missing")
        production.decide_asset(
            edited_id,
            decision="approved",
            reason="offline range edit",
            select=True,
        )
        _require(repository.get_asset(source.id).path.is_file(), "range edit overwrote source")
        revised_sequence = sequences.build_project_sequence(project_id)
        _require(
            revised_sequence.parent_sequence_id == sequence.id,
            "sequence revision did not retain its parent",
        )
        repository.decide_sequence(revised_sequence.id, approved=True)
        repository.select_sequence(project_id, revised_sequence.id)
        repository.select_sequence(project_id, sequence.id)
        _require(
            repository.get_project(project_id).selected_sequence_id == sequence.id,
            "approved project sequence could not be rolled back",
        )

        failure_shot = repository.add_shot(
            scene.id,
            ShotCardDraft(
                title="未知提交保护",
                direction="固定中景，灰白猫安静观察水面，人物保持鱼竿稳定，镜头在浮标静止时结束。",
            ),
        )
        gateway.fail_unknown_once = True
        before = len(gateway.submissions)
        try:
            production.generate_video(failure_shot.id, allow_paid_generation=True)
        except GatewayError:
            pass
        else:
            raise AssertionError("submission_unknown fixture did not fail")
        try:
            production.generate_video(
                failure_shot.id,
                allow_paid_generation=True,
                regenerate=True,
                reason="must reconcile first",
            )
        except ValueError as exc:
            _require("submission_unknown" in str(exc), "wrong unknown-submit error")
        else:
            raise AssertionError("submission_unknown created a paid retry")
        _require(len(gateway.submissions) == before + 1, "unknown submit was posted twice")

        limit_assets = tuple(
            production.import_reference(
                project_id=project_id,
                path=fixtures["https://fixture.local/anchor.png"],
                usage="generation_reference",
                role="identity",
            )
            for _index in range(15)
        )
        limit_scene = repository.add_scene(
            project_id,
            SceneDraft(title="引用上限预检", sourceText="只验证本地引用数量，不提交任务。"),
        )
        too_many_anchor = repository.add_shot(
            limit_scene.id,
            ShotCardDraft(
                title="图片引用上限",
                direction="1. 固定中景，猫咪观察人物整理道具并稳定结束。",
                anchorMode=AnchorMode.GENERATE,
                inheritProjectReferences=False,
                referenceBindings=[
                    ReferenceBinding(
                        assetId=item.id,
                        usage=ReferenceUsage.GENERATION_REFERENCE,
                        role=ReferenceRole.IDENTITY,
                        applyTo=ReferenceTarget.ANCHOR,
                    )
                    for item in limit_assets
                ],
            ),
        )
        image_calls_before = gateway.image_submissions
        try:
            production.generate_anchor(too_many_anchor.id, allow_paid_generation=True)
        except ValueError as exc:
            _require("14" in str(exc), "wrong Seedream reference limit error")
        else:
            raise AssertionError("15 image references reached the paid gateway")
        _require(gateway.image_submissions == image_calls_before, "image limit failed after submit")

        too_many_video = repository.add_shot(
            limit_scene.id,
            ShotCardDraft(
                title="视频引用上限",
                direction="1. 固定中景，猫咪观察人物整理道具并稳定结束。",
                inheritProjectReferences=False,
                referenceBindings=[
                    ReferenceBinding(
                        assetId=item.id,
                        usage=ReferenceUsage.GENERATION_REFERENCE,
                        role=ReferenceRole.IDENTITY,
                        applyTo=ReferenceTarget.VIDEO,
                    )
                    for item in limit_assets[:10]
                ],
            ),
        )
        video_calls_before = len(gateway.submissions)
        try:
            production.generate_video(too_many_video.id, allow_paid_generation=True)
        except ValueError as exc:
            _require("9" in str(exc), "wrong Seedance reference limit error")
        else:
            raise AssertionError("10 video references reached the paid gateway")
        _require(len(gateway.submissions) == video_calls_before, "video limit failed after submit")

        advisory_reviews = [
            item for item in repository.reviews.values() if item.source == "ark_visual"
        ]
        _require(advisory_reviews, "multi-frame advisory review was not recorded")
        _require(
            all(item.decision == "pending" for item in advisory_reviews),
            "AI advice automatically decided media",
        )
        _require(
            all(not hasattr(item, "slot") for item in repository.steps.values()),
            "fixed three-slot data leaked into V5",
        )
        graph = repository.project_graph(project_id)
        traced_shots = [shot for scene_item in graph["scenes"] for shot in scene_item["shots"]]
        _require(traced_shots, "project graph did not expose shot cards")
        _require(
            any(shot["attempts"] for shot in traced_shots),
            "project graph did not expose prompts and attempts",
        )
        _require(
            len(graph["sequences"]) == 2,
            "project graph did not expose both sequence revisions",
        )

        print(
            {
                "projectId": str(project_id),
                "sceneCount": len(repository.list_scenes(project_id)),
                "shotCount": len(repository.list_shots(scene.id)),
                "providerSubmissions": len(gateway.submissions),
                "videoVersions": len(
                    [item for item in repository.assets.values() if item.media_type == "video"]
                ),
                "sequenceRevisions": len(repository.list_sequences(project_id)),
                "singleAndMultiSuggestions": True,
                "providerAndAcceptedOutputRetained": True,
                "referencePrecedenceAndLimits": True,
                "sampleInput": str(sample_path),
                "sampleCompositeSha256": sample_master.sha256,
                "databaseCanon": database_canon,
                "rangeEditPreservedSource": True,
                "submissionUnknownFrozen": True,
                "realArkCalls": 0,
            }
        )


def _verify_database_canon() -> dict[str, Any]:
    load_local_env()
    recommended_keys = {
        "person:headshot",
        "person:fullbody",
        "cat:front",
        "cat:side",
        "style:line_texture",
    }
    container = build_runtime_container()
    try:
        assets = container.repository.list_assets()
        _require(len(assets) == 11, "database does not expose exactly 11 Canon assets")
        for asset in assets:
            _require(asset.path.is_file(), f"Canon content is missing: {asset.semantic_key}")
            digest = hashlib.sha256(asset.path.read_bytes()).hexdigest()
            _require(digest == asset.sha256, f"Canon content hash drifted: {asset.semantic_key}")
        projects = container.repository.list_projects()
        _require(projects, "Canon project-default verification requires one local project")
        project = projects[0]
        original = project.default_reference_bindings
        selected = tuple(
            ReferenceBinding(
                assetId=asset.id,
                usage=ReferenceUsage.GENERATION_REFERENCE,
                role=(
                    ReferenceRole.STYLE
                    if asset.semantic_key and asset.semantic_key.startswith("style:")
                    else ReferenceRole.IDENTITY
                ),
                applyTo=ReferenceTarget.BOTH,
            )
            for asset in assets
            if asset.semantic_key in recommended_keys
        )
        _require(len(selected) == 5, "recommended Canon default set is not exactly five assets")
        try:
            saved = container.repository.update_project_default_references(
                project.id,
                list(selected),
            )
            _require(
                saved.default_reference_bindings == selected,
                "project Canon defaults were not persisted",
            )
        finally:
            container.repository.update_project_default_references(project.id, list(original))
        return {
            "assetsReadable": len(assets),
            "recommendedDefaultsRoundTrip": len(selected),
            "projectId": str(project.id),
        }
    finally:
        container.close()


def _probe_json(ffprobe: str, path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise AssertionError(f"ffprobe returned a non-object for {path}")
    return payload


def _normalize_sample(ffmpeg: str, source: Path, output: Path) -> Path:
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            "scale=480:854:force_original_aspect_ratio=decrease,"
            "pad=480:854:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(output),
        ],
        check=True,
        timeout=1800,
    )
    return output


def _fixtures(root: Path, ffmpeg: str) -> dict[str, Path]:
    anchor = root / "anchor.png"
    Image.new("RGB", (480, 854), (205, 222, 198)).save(anchor)
    shot = root / "shot.mp4"
    edit = root / "edit.mp4"
    # The edited fixture suggestions span 9–11 seconds; a 10-second substitute
    # stays within the production QC tolerance for every accepted clip.
    _video_fixture(ffmpeg, shot, duration=10, color="0x8fb7a0", frequency=440)
    _video_fixture(ffmpeg, edit, duration=4, color="0xd4aa78", frequency=520)
    return {
        "https://fixture.local/anchor.png": anchor,
        "https://fixture.local/shot.mp4": shot,
        "https://fixture.local/edit.mp4": edit,
    }


def _video_fixture(ffmpeg: str, path: Path, *, duration: int, color: str, frequency: int) -> None:
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=480x854:r=24:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-y",
            str(path),
        ],
        check=True,
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    main()
