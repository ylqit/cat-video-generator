"""工作流状态及唯一合法转换入口。

Application Service 只能通过本模块推进状态；CLI、Repository和Ark Gateway
都不能绕过这里直接决定业务状态。
"""

from __future__ import annotations

from enum import StrEnum
from typing import TypeVar


class WorkflowTransitionError(ValueError):
    """请求的状态转换不属于当前工作流。"""


class RunStatus(StrEnum):
    DRAFT = "draft"
    PLANNING_REVIEW = "planning_review"
    PLANNED = "planned"
    GENERATING = "generating"
    REVIEWING = "reviewing"
    READY = "ready"
    DELIVERED = "delivered"
    FAILED = "failed"


class EpisodeStatus(StrEnum):
    PLANNED = "planned"
    PREPARING_VISUALS = "preparing_visuals"
    VIDEO_PENDING = "video_pending"
    VIDEO_GENERATING = "video_generating"
    MEDIA_QC = "media_qc"
    CONTENT_REVIEW = "content_review"
    READY = "ready"
    FAILED = "failed"


class StepKind(StrEnum):
    DIRECTOR = "director"
    IMAGE = "image"
    VIDEO = "video"


class StepStatus(StrEnum):
    PENDING = "pending"
    SUBMITTING = "submitting"
    SUBMISSION_UNKNOWN = "submission_unknown"
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


_RUN_TRANSITIONS = {
    RunStatus.DRAFT: {
        RunStatus.PLANNING_REVIEW,
        RunStatus.PLANNED,
        RunStatus.FAILED,
    },
    RunStatus.PLANNING_REVIEW: {
        RunStatus.DRAFT,
        RunStatus.PLANNED,
        RunStatus.FAILED,
    },
    RunStatus.PLANNED: {
        RunStatus.GENERATING,
        RunStatus.FAILED,
    },
    RunStatus.GENERATING: {
        RunStatus.REVIEWING,
        RunStatus.READY,
        RunStatus.FAILED,
    },
    RunStatus.REVIEWING: {
        RunStatus.GENERATING,
        RunStatus.READY,
        RunStatus.FAILED,
    },
    RunStatus.READY: {
        RunStatus.DELIVERED,
        RunStatus.FAILED,
    },
    RunStatus.DELIVERED: set(),
    # 初始导演链失败后可复用已成功的DayBrief继续补齐Episode，再回到planned。
    RunStatus.FAILED: {
        RunStatus.PLANNING_REVIEW,
        RunStatus.PLANNED,
        RunStatus.GENERATING,
    },
}

_EPISODE_TRANSITIONS = {
    EpisodeStatus.PLANNED: {
        EpisodeStatus.PREPARING_VISUALS,
        EpisodeStatus.VIDEO_PENDING,
        EpisodeStatus.FAILED,
    },
    EpisodeStatus.PREPARING_VISUALS: {
        EpisodeStatus.VIDEO_PENDING,
        EpisodeStatus.FAILED,
    },
    EpisodeStatus.VIDEO_PENDING: {
        EpisodeStatus.VIDEO_GENERATING,
        EpisodeStatus.FAILED,
    },
    EpisodeStatus.VIDEO_GENERATING: {
        EpisodeStatus.MEDIA_QC,
        EpisodeStatus.FAILED,
    },
    EpisodeStatus.MEDIA_QC: {
        EpisodeStatus.CONTENT_REVIEW,
        EpisodeStatus.FAILED,
    },
    EpisodeStatus.CONTENT_REVIEW: {
        EpisodeStatus.READY,
        EpisodeStatus.FAILED,
    },
    EpisodeStatus.READY: set(),
    EpisodeStatus.FAILED: {
        EpisodeStatus.PLANNED,
        EpisodeStatus.PREPARING_VISUALS,
        EpisodeStatus.VIDEO_PENDING,
    },
}

_STEP_TRANSITIONS = {
    StepStatus.PENDING: {
        StepStatus.SUBMITTING,
        StepStatus.RUNNING,
        StepStatus.FAILED,
    },
    StepStatus.SUBMITTING: {
        StepStatus.SUBMISSION_UNKNOWN,
        StepStatus.QUEUED,
        StepStatus.AWAITING_REVIEW,
        StepStatus.SUCCEEDED,
        StepStatus.FAILED,
    },
    StepStatus.SUBMISSION_UNKNOWN: {
        StepStatus.QUEUED,
        StepStatus.RUNNING,
        StepStatus.SUCCEEDED,
        StepStatus.FAILED,
        StepStatus.CANCELLED,
    },
    StepStatus.QUEUED: {
        StepStatus.RUNNING,
        StepStatus.SUCCEEDED,
        StepStatus.FAILED,
        StepStatus.EXPIRED,
        StepStatus.CANCELLED,
    },
    StepStatus.RUNNING: {
        StepStatus.SUCCEEDED,
        StepStatus.AWAITING_REVIEW,
        StepStatus.FAILED,
        StepStatus.EXPIRED,
        StepStatus.CANCELLED,
    },
    StepStatus.AWAITING_REVIEW: {
        StepStatus.SUCCEEDED,
        StepStatus.FAILED,
    },
    StepStatus.SUCCEEDED: set(),
    StepStatus.FAILED: set(),
    StepStatus.EXPIRED: set(),
    StepStatus.CANCELLED: set(),
}

StatusT = TypeVar("StatusT", RunStatus, EpisodeStatus, StepStatus)


def _transition(
    current: StatusT,
    target: StatusT,
    transitions: dict[StatusT, set[StatusT]],
) -> StatusT:
    if target == current:
        return current
    if target not in transitions[current]:
        raise WorkflowTransitionError(
            f"非法状态转换: {current.value} -> {target.value}"
        )
    return target


def transition_run(current: RunStatus, target: RunStatus) -> RunStatus:
    """校验并返回Run目标状态，不产生数据库副作用。"""

    return _transition(current, target, _RUN_TRANSITIONS)


def transition_episode(
    current: EpisodeStatus,
    target: EpisodeStatus,
) -> EpisodeStatus:
    """校验并返回Episode目标状态，不产生数据库副作用。"""

    return _transition(current, target, _EPISODE_TRANSITIONS)


def transition_step(current: StepStatus, target: StepStatus) -> StepStatus:
    """校验并返回Step目标状态。

    `submission_unknown`不能回到`submitting`，因为响应未知时再次POST可能
    产生第二次付费任务；恢复流程必须先通过供应商任务列表完成对账。
    """

    return _transition(current, target, _STEP_TRANSITIONS)
