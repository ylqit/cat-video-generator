"""Offline V4 shot-queue data-flow smoke test.

The script uses in-memory workflow state, deterministic provider substitutes
and local FFmpeg fixtures.  It never reads ARK_API_KEY and never sends a
network request.
"""

from __future__ import annotations

import hashlib
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
from cat_video_generator.domain.contracts import (
    AnchorMode,
    ReferenceBinding,
    ReferenceRole,
    ReferenceTarget,
    ReferenceUsage,
    SceneDraft,
    ShotCardDraft,
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
        del prompt, schema, output_name
        return DirectorResult(
            payload={
                "sceneTitle": "池塘边的小发现",
                "shots": [
                    {
                        "title": "猫咪发现浮标",
                        "direction": (
                            "中景固定机位，灰白猫贴近岸边先看见浮标轻晃，"
                            "人物在后方稳定持竿；猫耳转向水面后停在安全位置，"
                            "画面在浮标再次下沉时稳定结束。"
                        ),
                        "suggestedDurationSeconds": 8,
                    },
                    {
                        "title": "人物回应信号",
                        "direction": (
                            "侧面近景缓慢跟随，人物沿竿身方向收紧钓线，"
                            "灰白猫保持四足站姿观察；鱼线只连接鱼竿和浮标，"
                            "动作在两者共同看向水面时稳定结束。"
                        ),
                        "suggestedDurationSeconds": 8,
                    },
                ],
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
        self.fail_unknown_once = False

    def generate_image(self, *, prompt: str, reference_paths: tuple[Path, ...]) -> ImageResult:
        del prompt, reference_paths
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
    with tempfile.TemporaryDirectory(prefix="cvg-v4-smoke-") as temporary:
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
        _require(
            not repository.list_steps(project_id=project_id), "project creation created a step"
        )

        suggestion = editing.suggest_shots(scene.id, allow_paid_generation=True)
        shots = list(editing.accept_suggestions(suggestion.step_id))
        _require(len(shots) == 2, "fixture suggestions were not accepted")
        third = repository.add_shot(
            scene.id,
            ShotCardDraft(
                title="生成锚点的收束镜头",
                direction="近景固定机位，猫咪回到人物脚边抬头，人物放松鱼竿并轻摸猫咪头顶；钓具保持在人物一侧，动作在安静关系回报中结束。",
                durationSeconds=8,
                anchorMode=AnchorMode.GENERATE,
            ),
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
        _require(len(gateway.submissions[0].bindings) == 0, "text-only shot sent an image")
        _require(len(gateway.submissions[1].bindings) == 1, "existing anchor was not sent")
        _require(
            len(gateway.submissions[2].bindings) == 2, "generated anchor/reference order failed"
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
            "fixed three-slot data leaked into V4",
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
                "rangeEditPreservedSource": True,
                "submissionUnknownFrozen": True,
                "realArkCalls": 0,
            }
        )


def _fixtures(root: Path, ffmpeg: str) -> dict[str, Path]:
    anchor = root / "anchor.png"
    Image.new("RGB", (480, 854), (205, 222, 198)).save(anchor)
    shot = root / "shot.mp4"
    edit = root / "edit.mp4"
    _video_fixture(ffmpeg, shot, duration=8, color="0x8fb7a0", frequency=440)
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
