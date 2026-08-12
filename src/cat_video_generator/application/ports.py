"""Boundaries used by the V4 shot queue application services."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

from ..domain.contracts import SceneDraft, ShotCardDraft, StoryProjectInput
from ..domain.rendering import ProjectSequencePlan, SequenceStatus, VideoInputPlan
from ..domain.workflow import (
    PromptPurpose,
    RunStatus,
    SceneStatus,
    ShotStatus,
    StepKind,
    StepStatus,
)


@dataclass(frozen=True, slots=True)
class DirectorResult:
    payload: dict[str, Any]
    response_id: str
    model: str
    request_hash: str


@dataclass(frozen=True, slots=True)
class ImageResult:
    url: str
    model: str


@dataclass(frozen=True, slots=True)
class VideoTaskResult:
    task_id: str
    status: str
    video_url: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    model: str | None = None
    created_at: datetime | None = None
    duration_seconds: int | None = None
    ratio: str | None = None
    resolution: str | None = None
    generate_audio: bool | None = None


@dataclass(frozen=True, slots=True)
class VideoDiagnosticResult:
    identity_ok: bool
    style_ok: bool
    constraints_ok: bool
    narrative_order_ok: bool
    confidence: float
    violations: tuple[str, ...]
    evidence: tuple[dict[str, str | None], ...]
    shot_boundaries_seconds: tuple[float, ...]
    response_id: str
    model: str
    request_hash: str


class GatewayError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool,
        submission_unknown: bool = False,
        request_id: str | None = None,
        timed_out: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.submission_unknown = submission_unknown
        self.request_id = request_id
        self.timed_out = timed_out


@dataclass(frozen=True, slots=True)
class StoredProject:
    id: uuid.UUID
    title: str
    content_date: date
    status: RunStatus
    selected_sequence_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class StoredScene:
    id: uuid.UUID
    project_id: uuid.UUID
    order: int
    draft: SceneDraft
    status: SceneStatus


@dataclass(frozen=True, slots=True)
class StoredShot:
    id: uuid.UUID
    scene_id: uuid.UUID
    project_id: uuid.UUID
    order: int
    draft: ShotCardDraft
    status: ShotStatus
    selected_anchor_asset_id: uuid.UUID | None = None
    selected_video_asset_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class StoredStep:
    id: uuid.UUID
    project_id: uuid.UUID
    scene_id: uuid.UUID | None
    shot_card_id: uuid.UUID | None
    kind: StepKind
    status: StepStatus
    attempt: int
    operation_key: str
    input_snapshot: dict[str, Any] = field(default_factory=dict)
    provider: str | None = None
    provider_task_id: str | None = None
    model: str | None = None
    error: dict[str, Any] | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StoredPrompt:
    id: uuid.UUID
    step_id: uuid.UUID
    purpose: PromptPurpose
    model: str
    text: str
    sha256: str


@dataclass(frozen=True, slots=True)
class StoredAsset:
    id: uuid.UUID
    project_id: uuid.UUID | None
    scene_id: uuid.UUID | None
    shot_card_id: uuid.UUID | None
    step_id: uuid.UUID | None
    role: str
    media_type: str
    scope: str
    status: str
    path: Path
    sha256: str
    metadata: dict[str, Any]
    semantic_key: str | None = None


@dataclass(frozen=True, slots=True)
class StoredReview:
    id: uuid.UUID
    step_id: uuid.UUID
    asset_id: uuid.UUID | None
    source: str
    decision: str
    reason: str | None
    warnings: tuple[dict[str, Any], ...]
    evidence: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StoredSequence:
    id: uuid.UUID
    project_id: uuid.UUID
    revision: int
    parent_sequence_id: uuid.UUID | None
    rendered_asset_id: uuid.UUID | None
    status: SequenceStatus
    plan: ProjectSequencePlan
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LandedAsset:
    path: Path
    sha256: str
    byte_size: int


class DirectorGateway(Protocol):
    @property
    def model(self) -> str: ...

    def generate_structured(
        self, *, prompt: str, schema: dict[str, Any], output_name: str
    ) -> DirectorResult: ...


class MediaGateway(Protocol):
    @property
    def image_model(self) -> str: ...

    @property
    def video_model(self) -> str: ...

    @property
    def review_model(self) -> str: ...

    def generate_image(self, *, prompt: str, reference_paths: tuple[Path, ...]) -> ImageResult: ...

    def submit_video(
        self,
        *,
        prompt: str,
        input_plan: VideoInputPlan,
        input_sources: tuple[Path | str, ...],
    ) -> VideoTaskResult: ...

    def get_video_task(self, task_id: str) -> VideoTaskResult: ...

    def list_video_tasks(
        self, *, model: str, page_size: int = 100
    ) -> tuple[VideoTaskResult, ...]: ...

    def diagnose_video_frames(
        self, *, prompt: str, frame_paths: tuple[Path, ...]
    ) -> VideoDiagnosticResult: ...


class AssetStore(Protocol):
    def download(self, url: str, *, suffix: str) -> LandedAsset: ...

    def import_local(self, path: Path) -> LandedAsset: ...

    def concatenate_videos(self, paths: tuple[Path, ...]) -> LandedAsset: ...

    def render_range_replacement(
        self,
        *,
        base_path: Path,
        replacement_path: Path,
        replacement_duration_ms: int,
        start_ms: int,
        end_ms: int,
    ) -> LandedAsset: ...


class MediaProbe(Protocol):
    def inspect_image(self, path: Path) -> dict[str, Any]: ...

    def inspect_video(
        self,
        path: Path,
        *,
        expected_duration_seconds: int,
        expected_resolution: str,
        minimum_duration_seconds: int = 8,
        maximum_duration_seconds: int = 15,
        duration_tolerance_ms: int = 1000,
        require_audio: bool = True,
    ) -> dict[str, Any]: ...


class FrameExtractor(Protocol):
    def extract_review_frames(self, source: StoredAsset, *, count: int) -> tuple[Path, ...]: ...

    def extract_frames_at(
        self, source: StoredAsset, *, timestamps_ms: tuple[int, ...]
    ) -> tuple[Path, ...]: ...


class ShotQueueStore(Protocol):
    def create_project(self, source: StoryProjectInput, *, content_date: date) -> StoredProject: ...

    def update_project(
        self,
        project_id: uuid.UUID,
        *,
        title: str,
        content_date: date,
    ) -> StoredProject: ...

    def list_projects(self) -> tuple[StoredProject, ...]: ...

    def get_project(self, project_id: uuid.UUID) -> StoredProject: ...

    def add_scene(self, project_id: uuid.UUID, draft: SceneDraft) -> StoredScene: ...

    def update_scene(self, scene_id: uuid.UUID, draft: SceneDraft) -> StoredScene: ...

    def delete_scene(self, scene_id: uuid.UUID) -> None: ...

    def reorder_scenes(self, project_id: uuid.UUID, scene_ids: tuple[uuid.UUID, ...]) -> None: ...

    def list_scenes(self, project_id: uuid.UUID) -> tuple[StoredScene, ...]: ...

    def get_scene(self, scene_id: uuid.UUID) -> StoredScene: ...

    def add_shot(self, scene_id: uuid.UUID, draft: ShotCardDraft) -> StoredShot: ...

    def replace_shots(
        self, scene_id: uuid.UUID, drafts: tuple[ShotCardDraft, ...]
    ) -> tuple[StoredShot, ...]: ...

    def update_shot(self, shot_id: uuid.UUID, draft: ShotCardDraft) -> StoredShot: ...

    def delete_shot(self, shot_id: uuid.UUID) -> None: ...

    def reorder_shots(self, scene_id: uuid.UUID, shot_ids: tuple[uuid.UUID, ...]) -> None: ...

    def list_shots(self, scene_id: uuid.UUID) -> tuple[StoredShot, ...]: ...

    def get_shot(self, shot_id: uuid.UUID) -> StoredShot: ...

    def next_attempt(self, *, shot_id: uuid.UUID, operation_key: str) -> int: ...

    def next_scene_attempt(self, *, scene_id: uuid.UUID, operation_key: str) -> int: ...

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
    ) -> tuple[StoredStep, StoredPrompt]: ...

    def update_step(
        self,
        step_id: uuid.UUID,
        *,
        status: StepStatus,
        task_id: str | None = None,
        error: dict[str, Any] | None = None,
        input_snapshot: dict[str, Any] | None = None,
    ) -> StoredStep: ...

    def get_step(self, step_id: uuid.UUID) -> StoredStep: ...

    def list_steps(
        self,
        *,
        project_id: uuid.UUID,
        scene_id: uuid.UUID | None = None,
        shot_id: uuid.UUID | None = None,
    ) -> tuple[StoredStep, ...]: ...

    def get_prompt(self, step_id: uuid.UUID) -> StoredPrompt | None: ...

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
    ) -> StoredAsset: ...

    def get_asset(self, asset_id: uuid.UUID) -> StoredAsset: ...

    def list_assets(
        self,
        *,
        project_id: uuid.UUID | None = None,
        shot_id: uuid.UUID | None = None,
        include_canon: bool = False,
    ) -> tuple[StoredAsset, ...]: ...

    def select_shot_asset(
        self, shot_id: uuid.UUID, *, kind: str, asset_id: uuid.UUID
    ) -> StoredShot: ...

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
    ) -> StoredReview: ...

    def decide_asset(
        self,
        asset_id: uuid.UUID,
        *,
        decision: str,
        reason: str | None,
    ) -> StoredAsset: ...

    def list_reviews(self, step_id: uuid.UUID) -> tuple[StoredReview, ...]: ...

    def create_sequence(
        self,
        *,
        project_id: uuid.UUID,
        plan: ProjectSequencePlan,
        parent_sequence_id: uuid.UUID | None,
        rendered_asset_id: uuid.UUID | None,
        status: SequenceStatus,
    ) -> StoredSequence: ...

    def list_sequences(self, project_id: uuid.UUID) -> tuple[StoredSequence, ...]: ...

    def select_sequence(self, project_id: uuid.UUID, sequence_id: uuid.UUID) -> StoredSequence: ...

    def decide_sequence(self, sequence_id: uuid.UUID, *, approved: bool) -> StoredSequence: ...
