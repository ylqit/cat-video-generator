"""Application层允许依赖的外部边界协议。

这里只为数据库、Ark、资产存储和媒体探测等真正的生命周期边界建立协议，
不为路径拼接或单次函数转发创建抽象。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

from ..domain.contracts import (
    AcceptedOutcome,
    DailyProductionPlan,
    EpisodePlan,
    RecentContentSummary,
    Slot,
)
from ..domain.pipeline import PipelineSettings
from ..domain.rendering import SequenceStatus, VideoInputPlan, VideoSequencePlan
from ..domain.workflow import (
    EpisodeStatus,
    PromptPurpose,
    RunStatus,
    StepKind,
    StepStatus,
)


@dataclass(frozen=True, slots=True)
class DirectorResult:
    """Ark导演的一次结构化输出。"""

    payload: dict[str, Any]
    response_id: str
    model: str
    request_hash: str


@dataclass(frozen=True, slots=True)
class ImageResult:
    """Seedream返回的一张定妆图或开场锚点。"""

    url: str
    model: str


@dataclass(frozen=True, slots=True)
class VideoTaskResult:
    """异步视频任务的可持久化状态。"""

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
class ImageReviewResult:
    """定妆图或开场锚点的轻量语义审核结果。"""

    identity_ok: bool
    style_ok: bool
    appearance_ok: bool
    composition_ok: bool
    constraints_ok: bool
    confidence: float
    violations: tuple[str, ...]
    warnings: tuple[str, ...]
    evidence: tuple[str, ...]
    response_id: str
    model: str
    request_hash: str


@dataclass(frozen=True, slots=True)
class VideoDiagnosticResult:
    """抽帧序列的非阻断语义诊断；最终批准权仍属于人工内容审核。"""

    identity_ok: bool
    style_ok: bool
    constraints_ok: bool
    narrative_order_ok: bool
    confidence: float
    violations: tuple[str, ...]
    evidence: tuple[dict[str, str | None], ...]
    shot_boundaries_seconds: tuple[float, ...]
    actual_outcome: str
    carry_forward: tuple[str, ...]
    do_not_carry_forward: tuple[str, ...]
    response_id: str
    model: str
    request_hash: str


@dataclass(frozen=True, slots=True)
class StoredRun:
    id: uuid.UUID
    content_date: date
    status: str
    plan: DailyProductionPlan | None


@dataclass(frozen=True, slots=True)
class StoredEpisode:
    id: uuid.UUID
    run_id: uuid.UUID
    plan: EpisodePlan
    status: EpisodeStatus
    selected_video_asset_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class StoredStep:
    id: uuid.UUID
    run_id: uuid.UUID
    episode_id: uuid.UUID | None
    kind: StepKind
    status: StepStatus
    attempt: int
    provider_task_id: str | None
    model: str | None
    operation_key: str
    input_snapshot: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    submitted_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class StoredPrompt:
    """数据库中一次真实供应商调用对应的不可变Prompt。"""

    id: uuid.UUID
    step_id: uuid.UUID
    purpose: PromptPurpose
    model: str
    text: str
    sha256: str


@dataclass(frozen=True, slots=True)
class StoredAsset:
    id: uuid.UUID
    run_id: uuid.UUID | None
    episode_id: uuid.UUID | None
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
class StoredVideoSequence:
    """数据库中的一个非破坏性视频时间轴版本；状态可推进，EDL不原位覆写历史版本。"""

    id: uuid.UUID
    episode_id: uuid.UUID
    revision: int
    parent_sequence_id: uuid.UUID | None
    base_asset_id: uuid.UUID
    rendered_asset_id: uuid.UUID | None
    status: SequenceStatus
    plan: VideoSequencePlan
    audio_policy: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class VideoSequenceSelectionResult:
    """正式视频切换结果；视频选择和结果卡处理必须来自同一个数据库事务。"""

    sequence: StoredVideoSequence
    outcome_kept: bool
    outcome_revoked: bool


@dataclass(frozen=True, slots=True)
class LandedAsset:
    path: Path
    sha256: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class DeliveryBuild:
    """原子构建完成的本地交付包。"""

    path: Path
    manifest_sha256: str
    items: tuple[dict[str, str | int], ...]


@dataclass(frozen=True, slots=True)
class ReviewCommitResult:
    """一次原子审核提交的结果；重复同一决定返回原记录。"""

    review_id: uuid.UUID
    decision: str
    idempotent: bool


class GatewayError(RuntimeError):
    """Application可理解的外部调用错误基类。"""

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


class DirectorGateway(Protocol):
    """一次调用只返回一个结构化导演对象。"""

    @property
    def model(self) -> str: ...

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        output_name: str,
    ) -> DirectorResult: ...


class MediaGenerationGateway(Protocol):
    """Ark Seedream和Seedance的供应商边界。"""

    @property
    def image_model(self) -> str: ...

    @property
    def video_model(self) -> str: ...

    def generate_image(
        self,
        *,
        prompt: str,
        reference_paths: tuple[Path, ...],
    ) -> ImageResult: ...

    def submit_video(
        self,
        *,
        prompt: str,
        input_plan: VideoInputPlan,
        input_sources: tuple[Path | str, ...],
    ) -> VideoTaskResult: ...

    def get_video_task(self, task_id: str) -> VideoTaskResult: ...

    def list_video_tasks(
        self,
        *,
        model: str,
        page_size: int = 100,
    ) -> tuple[VideoTaskResult, ...]: ...


class VisualReviewGateway(Protocol):
    """视觉图片审核与视频诊断的独立Ark边界。"""

    @property
    def review_model(self) -> str: ...

    def review_image(
        self,
        *,
        prompt: str,
        image_path: Path,
        reference_paths: tuple[Path, ...],
    ) -> ImageReviewResult: ...

    def diagnose_video_frames(
        self,
        *,
        prompt: str,
        frame_paths: tuple[Path, ...],
    ) -> VideoDiagnosticResult: ...


class PlanningStore(Protocol):
    """导演规划所需的最小持久化能力。"""

    def create_draft_run(self, content_date: date) -> uuid.UUID: ...
    def create_step_with_prompt_intent(self, **kwargs: Any) -> tuple[StoredStep, uuid.UUID]: ...
    def save_prompt(self, **kwargs: Any) -> uuid.UUID: ...
    def finish_director_step(self, **kwargs: Any) -> None: ...
    def fail_director_step(self, **kwargs: Any) -> None: ...
    def fail_step(self, step_id: uuid.UUID, **kwargs: Any) -> None: ...
    def finalize_plan(self, **kwargs: Any) -> None: ...
    def save_planned_episode(self, **kwargs: Any) -> StoredEpisode: ...
    def save_initial_planning_metadata(self, **kwargs: Any) -> None: ...
    def save_planning_context(self, **kwargs: Any) -> None: ...
    def list_recent_completed_summaries(
        self, *, limit: int
    ) -> tuple[RecentContentSummary, ...]: ...
    def get_planning_context(self, run_id: uuid.UUID) -> dict[str, Any]: ...
    def next_director_attempt(self, *, run_id: uuid.UUID, phase: str, slot: Slot | None) -> int: ...
    def replace_episode_plan(self, **kwargs: Any) -> None: ...
    def get_run(self, run_id: uuid.UUID) -> StoredRun: ...
    def list_episodes(self, run_id: uuid.UUID) -> tuple[StoredEpisode, ...]: ...
    def get_step(self, step_id: uuid.UUID) -> StoredStep: ...
    def get_prompt_for_step(
        self, step_id: uuid.UUID, *, purpose: PromptPurpose
    ) -> StoredPrompt: ...
    def set_step_status(self, step_id: uuid.UUID, target: StepStatus, **kwargs: Any) -> None: ...
    def set_run_status(self, run_id: uuid.UUID, target: RunStatus) -> None: ...
    def record_review(self, **kwargs: Any) -> uuid.UUID: ...
    def save_pipeline_settings(self, **kwargs: Any) -> None: ...
    def get_pipeline_settings(self, run_id: uuid.UUID) -> PipelineSettings: ...


class ProductionStore(Protocol):
    """媒体生产、恢复、审核与交付所需的持久化能力。"""

    def create_step_with_prompt_intent(self, **kwargs: Any) -> tuple[StoredStep, uuid.UUID]: ...
    def save_prompt(self, **kwargs: Any) -> uuid.UUID: ...
    def fail_step(self, step_id: uuid.UUID, **kwargs: Any) -> None: ...
    def next_step_attempt(
        self, *, episode_id: uuid.UUID, kind: StepKind, operation_key: str
    ) -> int: ...
    def get_prompt_overrides(self, episode_id: uuid.UUID) -> dict[str, str]: ...
    def get_prompt_override_state(self, episode_id: uuid.UUID) -> dict[str, Any]: ...
    def save_prompt_overrides(self, **kwargs: Any) -> None: ...
    def get_run(self, run_id: uuid.UUID) -> StoredRun: ...
    def get_pipeline_settings(self, run_id: uuid.UUID) -> PipelineSettings: ...
    def get_episode(self, run_id: uuid.UUID, slot: Slot) -> StoredEpisode: ...
    def list_episodes(self, run_id: uuid.UUID) -> tuple[StoredEpisode, ...]: ...
    def get_step(self, step_id: uuid.UUID) -> StoredStep: ...
    def latest_retryable_step(self, episode_id: uuid.UUID) -> StoredStep | None: ...
    def get_prompt_for_step(
        self, step_id: uuid.UUID, *, purpose: PromptPurpose
    ) -> StoredPrompt: ...
    def list_resumable_steps(self, run_id: uuid.UUID | None) -> tuple[StoredStep, ...]: ...
    def list_assets(self, **kwargs: Any) -> tuple[StoredAsset, ...]: ...
    def find_reusable_asset(self, **kwargs: Any) -> StoredAsset | None: ...
    def set_episode_status(self, episode_id: uuid.UUID, target: EpisodeStatus) -> None: ...
    def set_run_status(self, run_id: uuid.UUID, target: RunStatus) -> None: ...
    def set_step_status(self, step_id: uuid.UUID, target: StepStatus, **kwargs: Any) -> None: ...
    def reopen_video_step_for_local_recovery(self, step_id: uuid.UUID) -> None: ...
    def patch_step_snapshot(self, step_id: uuid.UUID, patch: dict[str, Any]) -> None: ...
    def find_step_by_provider_task_id(self, provider_task_id: str) -> StoredStep | None: ...
    def save_asset(self, **kwargs: Any) -> StoredAsset: ...
    def patch_asset_metadata(self, asset_id: uuid.UUID, patch: dict[str, Any]) -> None: ...
    def record_review(self, **kwargs: Any) -> uuid.UUID: ...
    def commit_asset_review(self, **kwargs: Any) -> ReviewCommitResult: ...
    def asset_detail(self, asset_id: uuid.UUID) -> StoredAsset: ...
    def episode_detail(self, episode_id: uuid.UUID) -> dict[str, Any]: ...
    def next_delivery_revision(self, run_id: uuid.UUID) -> int: ...
    def save_delivery(self, **kwargs: Any) -> uuid.UUID: ...
    def create_video_sequence(self, **kwargs: Any) -> StoredVideoSequence: ...
    def get_video_sequence(self, sequence_id: uuid.UUID) -> StoredVideoSequence: ...
    def list_video_sequences(self, episode_id: uuid.UUID) -> tuple[StoredVideoSequence, ...]: ...
    def update_video_sequence(self, **kwargs: Any) -> StoredVideoSequence: ...
    def next_video_sequence_revision(self, episode_id: uuid.UUID) -> int: ...
    def select_video_sequence(
        self,
        sequence_id: uuid.UUID,
        *,
        revoke_confirmed_outcome: bool,
        keep_confirmed_outcome: bool,
    ) -> VideoSequenceSelectionResult: ...


class QueryStore(Protocol):
    """CLI和HTTP共享查询所需的只读能力。"""

    def workflow_graph(self, run_id: uuid.UUID) -> dict[str, Any]: ...
    def list_run_summaries(self, limit: int, offset: int) -> list[dict[str, Any]]: ...
    def prompt_detail(self, prompt_id: uuid.UUID) -> dict[str, Any]: ...
    def asset_detail(self, asset_id: uuid.UUID) -> StoredAsset: ...
    def episode_detail(self, episode_id: uuid.UUID) -> dict[str, Any]: ...
    def step_detail(self, step_id: uuid.UUID) -> dict[str, Any]: ...
    def step_trace(self, step_id: uuid.UUID) -> dict[str, Any]: ...
    def list_assets(self, **kwargs: Any) -> tuple[StoredAsset, ...]: ...
    def get_planning_context(self, run_id: uuid.UUID) -> dict[str, Any]: ...
    def get_prompt_overrides(self, episode_id: uuid.UUID) -> dict[str, str]: ...
    def get_prompt_override_state(self, episode_id: uuid.UUID) -> dict[str, Any]: ...
    def get_pipeline_settings(self, run_id: uuid.UUID) -> PipelineSettings: ...
    def list_delivery_packages(self, run_id: uuid.UUID) -> list[dict[str, Any]]: ...
    def delivery_package_detail(self, package_id: uuid.UUID) -> dict[str, Any]: ...
    def health(self) -> dict[str, Any]: ...
    def list_video_sequences(self, episode_id: uuid.UUID) -> tuple[StoredVideoSequence, ...]: ...


class StudioStore(Protocol):
    """创作台人工编辑（剧本/日导演/流水线开关）所需的持久化能力。"""

    def get_run(self, run_id: uuid.UUID) -> StoredRun: ...
    def episode_detail(self, episode_id: uuid.UUID) -> dict[str, Any]: ...
    def get_planning_context(self, run_id: uuid.UUID) -> dict[str, Any]: ...
    def replace_episode_plan(self, **kwargs: Any) -> None: ...
    def update_day_brief(self, **kwargs: Any) -> None: ...
    def save_pipeline_settings(self, **kwargs: Any) -> None: ...
    def get_pipeline_settings(self, run_id: uuid.UUID) -> PipelineSettings: ...
    def list_episodes(self, run_id: uuid.UUID) -> tuple[StoredEpisode, ...]: ...
    def get_outcome_source(self, run_id: uuid.UUID, slot: Slot) -> dict[str, Any]: ...
    def save_accepted_outcome(
        self,
        *,
        run_id: uuid.UUID,
        slot: Slot,
        outcome: AcceptedOutcome,
    ) -> None: ...
    def set_run_status(self, run_id: uuid.UUID, target: RunStatus) -> None: ...


class WorkflowRepository(PlanningStore, ProductionStore, QueryStore, StudioStore, Protocol):
    """基础设施组合实现遵循的完整能力集合；Application不直接依赖它。"""


class AssetStore(Protocol):
    """下载和本地不可变媒体的所有权边界。"""

    def download(
        self,
        url: str,
        *,
        suffix: str,
    ) -> LandedAsset: ...

    def import_local(self, path: Path) -> LandedAsset: ...

    def crop_local(
        self,
        path: Path,
        *,
        box: tuple[int, int, int, int],
    ) -> LandedAsset: ...

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

    def extract_video_range(
        self,
        *,
        source_path: Path,
        start_ms: int,
        end_ms: int,
    ) -> LandedAsset: ...

    def build_delivery(
        self,
        *,
        content_date: date,
        run_id: uuid.UUID,
        revision: int,
        items: tuple[tuple[Slot, StoredAsset], ...],
    ) -> DeliveryBuild: ...


class MediaProbe(Protocol):
    """技术媒体检查边界。"""

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


class ReviewFrameExtractor(Protocol):
    """视频语义诊断所需的均匀抽帧边界。"""

    def extract_review_frames(
        self,
        source: StoredAsset,
        *,
        count: int,
    ) -> tuple[Path, ...]: ...

    def extract_frames_at(
        self,
        source: StoredAsset,
        *,
        timestamps_ms: tuple[int, ...],
    ) -> tuple[Path, ...]: ...
