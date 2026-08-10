"""收费媒体节点的显式重试、继续查询与视频对账。"""

from __future__ import annotations

import uuid
from typing import Any

from ..domain.workflow import RunStatus, StepKind, StepStatus
from .ports import ProductionStore
from .video_execution import VideoExecutionService
from .visual_preparation import VisualPreparationService


class RetryService:
    """只重做用户点名的终止 Step，永不替 run-day 猜测重试。"""

    def __init__(
        self,
        *,
        repository: ProductionStore,
        visual_preparation: VisualPreparationService,
        video_execution: VideoExecutionService,
    ) -> None:
        self._repository = repository
        self._visual_preparation = visual_preparation
        self._video_execution = video_execution

    def retry_step(
        self,
        step_id: uuid.UUID,
        *,
        reason: str,
        allow_paid_generation: bool,
        acknowledge_duplicate_billing: bool = False,
        restart_from_beginning: bool = False,
    ) -> dict[str, Any]:
        """验证状态和费用许可后创建 attempt+1。"""

        if len(reason.strip()) < 4:
            raise ValueError("retry-step 必须提供具体人工原因")
        step = self._repository.get_step(step_id)
        unknown_image = (
            step.status is StepStatus.SUBMISSION_UNKNOWN
            and step.kind is StepKind.IMAGE
            and acknowledge_duplicate_billing
        )
        if step.status is StepStatus.SUBMISSION_UNKNOWN and not unknown_image:
            raise ValueError("视频 submission_unknown 必须先对账；图片需确认潜在重复计费")
        if not unknown_image and step.status not in {
            StepStatus.FAILED,
            StepStatus.EXPIRED,
            StepStatus.CANCELLED,
        }:
            raise ValueError("retry-step 只接受 failed、expired 或 cancelled 步骤")
        if step.episode_id is None:
            raise ValueError("当前 retry-step 只支持 Episode 媒体步骤")
        if not allow_paid_generation:
            raise ValueError("Ark 图片或视频重试需要显式付费许可")
        if step.kind is StepKind.IMAGE:
            supported = step.operation_key in {"image:look", "image:opening_anchor"}
        else:
            supported = step.kind is StepKind.VIDEO and (
                step.operation_key == "video:single_pass"
                or step.operation_key in {"video:extend:2", "video:extend:3"}
            )
        if not supported:
            raise ValueError(f"operationKey={step.operation_key!r} 不支持显式重试")
        episode = next(
            item
            for item in self._repository.list_episodes(step.run_id)
            if item.id == step.episode_id
        )
        if self._repository.get_run(step.run_id).status == RunStatus.FAILED.value:
            self._repository.set_run_status(step.run_id, RunStatus.GENERATING)
        overrides = self._repository.get_prompt_overrides(episode.id)
        if step.kind is StepKind.IMAGE:
            target = step.operation_key.removeprefix("image:")
            asset = self._visual_preparation.retry_image(
                step.id,
                reason=reason,
                prompt_override=overrides.get(target),
                duplicate_billing_risk_accepted=acknowledge_duplicate_billing,
            )
            return {
                "stepId": str(asset.step_id),
                "operationKey": step.operation_key,
                "assetIds": [str(asset.id)],
                "status": asset.status,
            }
        return self._video_execution.retry_video(
            episode,
            step,
            reason=reason,
            prompt_override=overrides.get("video"),
            restart_from_beginning=restart_from_beginning,
        )

    def resume_step(self, step_id: uuid.UUID) -> dict[str, Any]:
        step = self._repository.get_step(step_id)
        return self._video_execution.resume_step(self._episode(step), step)

    def reconciliation_candidates(self, step_id: uuid.UUID) -> tuple[dict[str, Any], ...]:
        return self._video_execution.reconciliation_candidates(self._repository.get_step(step_id))

    def reconcile_step(
        self,
        step_id: uuid.UUID,
        *,
        provider_task_id: str,
    ) -> dict[str, Any]:
        step = self._repository.get_step(step_id)
        return self._video_execution.reconcile_step(
            self._episode(step),
            step,
            provider_task_id=provider_task_id,
        )

    def _episode(self, step):
        if step.episode_id is None:
            raise ValueError("只有 Episode 视频步骤支持恢复或对账")
        return next(
            item
            for item in self._repository.list_episodes(step.run_id)
            if item.id == step.episode_id
        )
