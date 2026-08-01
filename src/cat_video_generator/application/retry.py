"""终态步骤的显式重试用例。

本用例只接受用户点名的旧Step，不扫描失败任务，也不替``run-day``自动重试。
它负责许可、安全状态和operationKey分派；各生产服务仍拥有自己的请求重建逻辑。
"""

from __future__ import annotations

import uuid
from typing import Any

from ..domain.workflow import RunStatus, StepKind, StepStatus
from .ports import ProductionStore
from .video_execution import VideoExecutionService
from .visual_preparation import VisualPreparationService


class RetryService:
    """从不可变旧Step创建一个带来源记录的新attempt。"""

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
    ) -> dict[str, Any]:
        """验证终态和费用许可后，精确重做原业务操作。"""

        if len(reason.strip()) < 4:
            raise ValueError("retry-step必须提供具体人工原因")
        step = self._repository.get_step(step_id)
        if step.status is StepStatus.SUBMISSION_UNKNOWN:
            raise ValueError("submission_unknown只能先对账，禁止创建新attempt")
        if step.status not in {
            StepStatus.FAILED,
            StepStatus.EXPIRED,
            StepStatus.CANCELLED,
        }:
            raise ValueError(
                "retry-step只接受FAILED、EXPIRED或CANCELLED步骤"
            )
        if step.episode_id is None:
            raise ValueError("当前retry-step只支持Episode媒体步骤")
        operation_key = step.operation_key
        paid = step.kind in {StepKind.IMAGE, StepKind.VIDEO}
        if paid and not allow_paid_generation:
            raise ValueError("Ark图片或视频重试需要--allow-paid-generation")
        supported = (
            (step.kind is StepKind.IMAGE and operation_key.startswith("image:"))
            or (step.kind is StepKind.VIDEO and operation_key == "video:single_pass")
        )
        if not supported:
            raise ValueError(f"步骤operationKey={operation_key!r}不支持显式重试")
        episode = next(
            item
            for item in self._repository.list_episodes(step.run_id)
            if item.id == step.episode_id
        )
        stored_run = self._repository.get_run(step.run_id)
        if (
            stored_run.status == RunStatus.FAILED.value
        ):
            self._repository.set_run_status(step.run_id, RunStatus.GENERATING)
        if step.kind is StepKind.IMAGE and operation_key.startswith("image:"):
            asset = self._visual_preparation.retry_image(
                episode,
                step,
                reason=reason,
            )
            return {
                "stepId": str(step_id),
                "operationKey": operation_key,
                "assetId": str(asset.id),
                "status": asset.status,
            }
        if step.kind is StepKind.VIDEO and operation_key == "video:single_pass":
            return self._video_execution.retry_video(
                episode,
                step,
                reason=reason,
            )
        raise AssertionError("受支持的重试操作必须在上方分支完成")
