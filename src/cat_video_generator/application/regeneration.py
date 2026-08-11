"""节点级显式重生成。

本用例按节点种类分派总导演、图片或整条视频的新attempt，保留旧输出和正式资产；
失败恢复仍由RetryService负责，submission_unknown仍只能对账。
"""

from __future__ import annotations

import uuid
from typing import Any

from ..domain.workflow import RunStatus, StepKind, StepStatus
from .planning import PlanningService
from .ports import ProductionStore
from .video_execution import VideoExecutionService
from .visual_preparation import VisualPreparationService


class RegenerationService:
    def __init__(
        self,
        *,
        repository: ProductionStore,
        planning: PlanningService,
        visual_preparation: VisualPreparationService,
        video_execution: VideoExecutionService,
    ) -> None:
        self._repository = repository
        self._planning = planning
        self._visual_preparation = visual_preparation
        self._video_execution = video_execution

    def regenerate_step(
        self,
        step_id: uuid.UUID,
        *,
        reason: str,
        prompt_override: str | None,
        allow_paid_generation: bool,
        acknowledge_downstream_replacement: bool,
    ) -> dict[str, Any]:
        if len(reason.strip()) < 4:
            raise ValueError("节点重生成必须填写具体原因")
        if not allow_paid_generation:
            raise ValueError("节点重生成需要显式付费许可")
        step = self._repository.get_step(step_id)
        if self._repository.get_run(step.run_id).status == RunStatus.DELIVERED.value:
            raise ValueError("已交付Run不可重新生成节点；请新建Run保留交付不可变性")
        if step.status is StepStatus.SUBMISSION_UNKNOWN:
            raise ValueError("submission_unknown必须先查询或对账，不能创建新attempt")
        if step.kind is StepKind.DIRECTOR:
            if step.episode_id is not None:
                raise ValueError("时段导演节点请使用重新规划")
            return self._planning.regenerate_day_brief(
                step.id,
                reason=reason,
                prompt_override=prompt_override,
                allow_paid_generation=True,
            )
        if step.episode_id is None:
            raise ValueError("媒体节点必须属于Episode")
        episode = next(
            item
            for item in self._repository.list_episodes(step.run_id)
            if item.id == step.episode_id
        )
        if step.kind is StepKind.IMAGE:
            asset = self._visual_preparation.regenerate_image(
                step.id,
                reason=reason,
                prompt_override=prompt_override,
            )
            return {
                "stepId": str(asset.step_id),
                "assetId": str(asset.id),
                "status": asset.status,
            }
        if not acknowledge_downstream_replacement:
            raise ValueError("整条视频重生成必须确认新版本不会覆盖既有正式视频")
        return self._video_execution.regenerate_video(
            episode,
            step,
            reason=reason,
            prompt_override=prompt_override,
        )
