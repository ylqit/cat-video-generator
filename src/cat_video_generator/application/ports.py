"""Application层允许依赖的外部边界协议。

这里只为数据库、Ark、资产存储和媒体探测等真正的生命周期边界建立协议，
不为路径拼接或单次函数转发创建抽象。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from ..domain.contracts import (
    DailyProductionPlan,
    EpisodePlan,
    RecentContentSummary,
    Slot,
)
from ..domain.rendering import VideoInputPlan
from ..domain.workflow import EpisodeStatus, RunStatus, StepKind, StepStatus


@dataclass(frozen=True, slots=True)
class DirectorResult:
    """Ark导演的一次结构化输出。"""

    payload: dict[str, Any]
    response_id: str
    model: str
    request_hash: str


@dataclass(frozen=True, slots=True)
class ImageResult:
    """同步图片生成结果。"""

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


@dataclass(frozen=True, slots=True)
class VisualReviewResult:
    """Ark视觉审核的结构化判断；证据不包含原图Base64。"""

    identity_ok: bool
    style_ok: bool
    world_state_ok: bool
    scene_topology_ok: bool
    confidence: float
    violations: tuple[str, ...]
    evidence: tuple[str, ...]
    response_id: str
    model: str
    request_hash: str


@dataclass(frozen=True, slots=True)
class VideoDiagnosticResult:
    """抽帧序列的非阻断语义诊断；最终批准权仍属于人工内容审核。"""

    identity_ok: bool
    style_ok: bool
    world_continuity_ok: bool
    narrative_order_ok: bool
    confidence: float
    violations: tuple[str, ...]
    evidence: tuple[str, ...]
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


@dataclass(frozen=True, slots=True)
class StoredPrompt:
    """数据库中一次真实供应商调用对应的不可变Prompt。"""

    id: uuid.UUID
    step_id: uuid.UUID
    purpose: str
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
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.submission_unknown = submission_unknown


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
        input_paths: tuple[Path, ...],
    ) -> VideoTaskResult: ...

    def get_video_task(self, task_id: str) -> VideoTaskResult: ...


class VisualReviewGateway(Protocol):
    """关键帧语义审核的独立Ark边界。"""

    @property
    def review_model(self) -> str: ...

    def review_keyframe(
        self,
        *,
        prompt: str,
        image_path: Path,
    ) -> VisualReviewResult: ...

    def diagnose_video_frames(
        self,
        *,
        prompt: str,
        frame_paths: tuple[Path, ...],
    ) -> VideoDiagnosticResult: ...


class PlanningStore(Protocol):
    """导演规划所需的最小持久化能力。"""

    def create_draft_run(self, content_date: date) -> uuid.UUID: ...
    def create_step_intent(self, **kwargs: Any) -> StoredStep: ...
    def save_prompt(self, **kwargs: Any) -> uuid.UUID: ...
    def finish_director_step(self, **kwargs: Any) -> None: ...
    def fail_director_step(self, **kwargs: Any) -> None: ...
    def fail_step(self, step_id: uuid.UUID, **kwargs: Any) -> None: ...
    def finalize_plan(self, **kwargs: Any) -> None: ...
    def save_planning_context(self, **kwargs: Any) -> None: ...
    def list_recent_completed_summaries(
        self, *, limit: int
    ) -> tuple[RecentContentSummary, ...]: ...
    def get_planning_context(self, run_id: uuid.UUID) -> dict[str, Any]: ...
    def next_director_attempt(
        self, *, run_id: uuid.UUID, phase: str, slot: Slot | None
    ) -> int: ...
    def replace_episode_plan(self, **kwargs: Any) -> None: ...
    def get_run(self, run_id: uuid.UUID) -> StoredRun: ...
    def get_step(self, step_id: uuid.UUID) -> StoredStep: ...
    def set_step_status(self, step_id: uuid.UUID, target: StepStatus, **kwargs: Any) -> None: ...
    def set_run_status(self, run_id: uuid.UUID, target: RunStatus) -> None: ...
    def record_review(self, **kwargs: Any) -> uuid.UUID: ...


class ProductionStore(Protocol):
    """媒体生产、恢复、审核与交付所需的持久化能力。"""

    def create_step_intent(self, **kwargs: Any) -> StoredStep: ...
    def save_prompt(self, **kwargs: Any) -> uuid.UUID: ...
    def fail_step(self, step_id: uuid.UUID, **kwargs: Any) -> None: ...
    def next_step_attempt(
        self, *, episode_id: uuid.UUID, kind: StepKind, operation_key: str
    ) -> int: ...
    def replace_episode_plan(self, **kwargs: Any) -> None: ...
    def get_prompt_overrides(self, episode_id: uuid.UUID) -> dict[str, str]: ...
    def save_prompt_overrides(self, **kwargs: Any) -> None: ...
    def get_run(self, run_id: uuid.UUID) -> StoredRun: ...
    def get_episode(self, run_id: uuid.UUID, slot: Slot) -> StoredEpisode: ...
    def list_episodes(self, run_id: uuid.UUID) -> tuple[StoredEpisode, ...]: ...
    def get_step(self, step_id: uuid.UUID) -> StoredStep: ...
    def latest_retryable_step(self, episode_id: uuid.UUID) -> StoredStep | None: ...
    def get_prompt_for_step(
        self, step_id: uuid.UUID, *, purpose: str
    ) -> StoredPrompt: ...
    def list_resumable_steps(
        self, run_id: uuid.UUID | None
    ) -> tuple[StoredStep, ...]: ...
    def list_assets(self, **kwargs: Any) -> tuple[StoredAsset, ...]: ...
    def find_reusable_asset(self, **kwargs: Any) -> StoredAsset | None: ...
    def set_episode_status(
        self, episode_id: uuid.UUID, target: EpisodeStatus
    ) -> None: ...
    def set_run_status(self, run_id: uuid.UUID, target: RunStatus) -> None: ...
    def set_step_status(self, step_id: uuid.UUID, target: StepStatus, **kwargs: Any) -> None: ...
    def save_asset(self, **kwargs: Any) -> StoredAsset: ...
    def select_video_asset(self, **kwargs: Any) -> None: ...
    def record_review(self, **kwargs: Any) -> uuid.UUID: ...
    def commit_asset_review(self, **kwargs: Any) -> ReviewCommitResult: ...
    def set_asset_status(self, asset_id: uuid.UUID, status: str) -> None: ...
    def asset_detail(self, asset_id: uuid.UUID) -> StoredAsset: ...
    def episode_detail(self, episode_id: uuid.UUID) -> dict[str, Any]: ...
    def next_delivery_revision(self, run_id: uuid.UUID) -> int: ...
    def save_delivery(self, **kwargs: Any) -> uuid.UUID: ...


class QueryStore(Protocol):
    """CLI和HTTP共享查询所需的只读能力。"""

    def workflow_graph(self, run_id: uuid.UUID) -> dict[str, Any]: ...
    def list_run_summaries(self, limit: int, offset: int) -> list[dict[str, Any]]: ...
    def prompt_detail(self, prompt_id: uuid.UUID) -> dict[str, Any]: ...
    def asset_detail(self, asset_id: uuid.UUID) -> StoredAsset: ...
    def episode_detail(self, episode_id: uuid.UUID) -> dict[str, Any]: ...
    def step_detail(self, step_id: uuid.UUID) -> dict[str, Any]: ...
    def list_assets(self, **kwargs: Any) -> tuple[StoredAsset, ...]: ...
    def get_prompt_overrides(self, episode_id: uuid.UUID) -> dict[str, str]: ...
    def list_delivery_packages(self, run_id: uuid.UUID) -> list[dict[str, Any]]: ...
    def delivery_package_detail(self, package_id: uuid.UUID) -> dict[str, Any]: ...
    def health(self) -> dict[str, Any]: ...


class WorkflowRepository(PlanningStore, ProductionStore, QueryStore, Protocol):
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

    def inspect_reference(
        self,
        path: Path,
        *,
        media_type: str,
    ) -> dict[str, Any]: ...

    def inspect_video(
        self,
        path: Path,
        *,
        expected_duration_seconds: int,
        expected_resolution: str,
        minimum_duration_seconds: int = 8,
        maximum_duration_seconds: int = 15,
        duration_tolerance_ms: int = 1000,
    ) -> dict[str, Any]: ...


class ReviewFrameExtractor(Protocol):
    """只为最终视频语义诊断抽帧，不承担拼接或转码。"""

    def extract_review_frames(
        self,
        source: StoredAsset,
        *,
        count: int,
    ) -> tuple[Path, ...]: ...
