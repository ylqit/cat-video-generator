"""终态步骤的显式重试用例。

本用例只接受用户点名的旧Step，不扫描失败任务，也不替``run-day``自动重试。
它负责许可、安全状态和operationKey分派；各生产服务仍拥有自己的请求重建逻辑。
"""

from __future__ import annotations

import uuid
from typing import Any

from ..domain.workflow import EpisodeStatus, RunStatus, StepKind, StepStatus
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
        acknowledge_duplicate_billing: bool = False,
    ) -> dict[str, Any]:
        """验证终态和费用许可后，精确重做原业务操作。"""

        if len(reason.strip()) < 4:
            raise ValueError("retry-step必须提供具体人工原因")
        step = self._repository.get_step(step_id)
        unknown_image_retry = (
            step.status is StepStatus.SUBMISSION_UNKNOWN
            and step.kind is StepKind.IMAGE
            and acknowledge_duplicate_billing
        )
        if step.status is StepStatus.SUBMISSION_UNKNOWN and not unknown_image_retry:
            raise ValueError(
                "视频submission_unknown必须先对账；图片需确认潜在重复计费后才能再生成"
            )
        if not unknown_image_retry and step.status not in {
            StepStatus.FAILED,
            StepStatus.EXPIRED,
            StepStatus.CANCELLED,
        }:
            raise ValueError("retry-step只接受FAILED、EXPIRED或CANCELLED步骤")
        if step.episode_id is None:
            raise ValueError("当前retry-step只支持Episode媒体步骤")
        operation_key = step.operation_key
        paid = step.kind in {StepKind.IMAGE, StepKind.VIDEO}
        if paid and not allow_paid_generation:
            raise ValueError("Ark图片或视频重试需要--allow-paid-generation")
        supported = (step.kind is StepKind.IMAGE and operation_key.startswith("image:")) or (
            step.kind is StepKind.VIDEO and operation_key == "video:single_pass"
        )
        if not supported:
            raise ValueError(f"步骤operationKey={operation_key!r}不支持显式重试")
        episode = next(
            item
            for item in self._repository.list_episodes(step.run_id)
            if item.id == step.episode_id
        )
        stored_run = self._repository.get_run(step.run_id)
        if stored_run.status == RunStatus.FAILED.value:
            self._repository.set_run_status(step.run_id, RunStatus.GENERATING)
        if step.kind is StepKind.IMAGE and operation_key == "image:look":
            look = self._visual_preparation.retry_look(
                episode,
                step,
                reason=reason,
                duplicate_billing_risk_accepted=acknowledge_duplicate_billing,
            )
            return {
                "stepId": str(look.step_id),
                "operationKey": operation_key,
                "assetIds": [str(look.id)],
                "status": look.status,
            }
        if step.kind is StepKind.IMAGE and operation_key == "image:storyboard":
            storyboard = self._visual_preparation.retry_storyboard(
                episode,
                step,
                reason=reason,
                duplicate_billing_risk_accepted=acknowledge_duplicate_billing,
            )
            approved = all(
                asset.status in {"approved", "ready"} for asset in storyboard
            )
            if approved:
                self._repository.set_episode_status(
                    episode.id,
                    EpisodeStatus.VIDEO_PENDING,
                )
            new_step_ids = {asset.step_id for asset in storyboard if asset.step_id is not None}
            if len(new_step_ids) != 1:
                raise RuntimeError("故事板重试结果必须属于同一个新Step")
            return {
                "stepId": str(next(iter(new_step_ids))),
                "operationKey": operation_key,
                "assetIds": [str(asset.id) for asset in storyboard],
                "status": "approved" if approved else "pending",
            }
        if step.kind is StepKind.VIDEO and operation_key == "video:single_pass":
            return self._video_execution.retry_video(
                episode,
                step,
                reason=reason,
            )
        raise AssertionError("受支持的重试操作必须在上方分支完成")

    def resume_step(self, step_id: uuid.UUID) -> dict[str, Any]:
        """继续查询一个已有Task ID的视频步骤，不产生新的供应商POST。"""

        step = self._repository.get_step(step_id)
        if step.episode_id is None:
            raise ValueError("只有Episode视频步骤支持继续查询")
        episode = next(
            item
            for item in self._repository.list_episodes(step.run_id)
            if item.id == step.episode_id
        )
        return self._video_execution.resume_step(episode, step)

    def reconciliation_candidates(self, step_id: uuid.UUID) -> tuple[dict[str, Any], ...]:
        """读取Ark近期任务供用户对账；此操作不修改Step。"""

        return self._video_execution.reconciliation_candidates(
            self._repository.get_step(step_id)
        )

    def reconcile_step(
        self,
        step_id: uuid.UUID,
        *,
        provider_task_id: str,
    ) -> dict[str, Any]:
        """绑定用户确认的Ark Task ID并继续原任务。"""

        step = self._repository.get_step(step_id)
        if step.episode_id is None:
            raise ValueError("只有Episode视频步骤支持对账")
        episode = next(
            item
            for item in self._repository.list_episodes(step.run_id)
            if item.id == step.episode_id
        )
        return self._video_execution.reconcile_step(
            episode,
            step,
            provider_task_id=provider_task_id,
        )
