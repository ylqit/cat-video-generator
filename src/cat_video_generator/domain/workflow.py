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


class PromptPurpose(StrEnum):
    """供应商调用及其审核使用的稳定Prompt用途。"""

    DIRECTOR = "director"
    STORYBOARD = "storyboard"
    STORYBOARD_REVIEW = "storyboard_review"
    VIDEO = "video"
    REVIEW = "review"


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


_PROMPT_PURPOSES_BY_STEP_KIND = {
    StepKind.DIRECTOR: frozenset({PromptPurpose.DIRECTOR}),
    StepKind.IMAGE: frozenset(
        {PromptPurpose.STORYBOARD, PromptPurpose.STORYBOARD_REVIEW}
    ),
    StepKind.VIDEO: frozenset({PromptPurpose.VIDEO, PromptPurpose.REVIEW}),
}

_GENERATION_PROMPT_BY_STEP_KIND = {
    StepKind.DIRECTOR: PromptPurpose.DIRECTOR,
    StepKind.IMAGE: PromptPurpose.STORYBOARD,
    StepKind.VIDEO: PromptPurpose.VIDEO,
}


def validate_prompt_purpose(
    kind: StepKind,
    purpose: PromptPurpose,
    *,
    generation_intent: bool = False,
) -> PromptPurpose:
    """校验Step和Prompt用途的对应关系。

    生成意图只能绑定该Step的主Prompt；审核Prompt在供应商媒体落盘后追加，
    防止把审核文本误当成一次新的收费生成输入。
    """

    allowed = _PROMPT_PURPOSES_BY_STEP_KIND[kind]
    if purpose not in allowed:
        raise ValueError(f"{kind.value}步骤不允许purpose={purpose.value}")
    if generation_intent and _GENERATION_PROMPT_BY_STEP_KIND[kind] is not purpose:
        expected = _GENERATION_PROMPT_BY_STEP_KIND[kind].value
        raise ValueError(f"{kind.value}步骤的生成Prompt必须是{expected}")
    return purpose


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
