"""三时段生产状态机编排。

本模块只决定按1/2/3推进哪些Episode以及何时切换Run状态。参考资产、关键帧、
Seedance任务和媒体QC分别由专用Application Service持有。
"""

from __future__ import annotations

import uuid
from typing import Any

from ..domain.contracts import Slot
from ..domain.rules import hard_failures, validate_input_gate
from ..domain.workflow import EpisodeStatus, RunStatus, StepKind, StepStatus
from .ports import StoredEpisode, WorkflowRepository
from .resolution_comparison import ResolutionComparisonService
from .video_execution import VideoExecutionService
from .visual_preparation import VisualPreparationService


class ProductionService:
    """把全天或单时段安全推进到人工内容审核。"""

    def __init__(
        self,
        *,
        repository: WorkflowRepository,
        visual_preparation: VisualPreparationService,
        video_execution: VideoExecutionService,
        resolution_comparison: ResolutionComparisonService | None = None,
    ) -> None:
        self._repository = repository
        self._visual_preparation = visual_preparation
        self._video_execution = video_execution
        self._resolution_comparison = resolution_comparison

    def run_day(
        self,
        run_id: uuid.UUID,
        *,
        slot: Slot | None,
        allow_paid_generation: bool,
        allow_unverified_keyframes: bool = False,
        allow_multi_clip: bool = False,
    ) -> dict[str, Any]:
        """生成指定Episode或按固定顺序推进全天。"""

        if not allow_paid_generation:
            raise ValueError("媒体生成需要显式提供--allow-paid-generation")
        stored_run = self._repository.get_run(run_id)
        if stored_run.plan is None:
            raise ValueError("Run尚未形成可执行方案")
        if stored_run.status in {RunStatus.ARCHIVED.value, RunStatus.DELIVERED.value}:
            raise ValueError(f"Run状态{stored_run.status}不允许继续生成")
        episodes = (
            (self._repository.get_episode(run_id, slot),)
            if slot is not None
            else self._repository.list_episodes(run_id)
        )
        if stored_run.status == RunStatus.PLANNED.value or (
            stored_run.status == RunStatus.FAILED.value
            and any(item.status is not EpisodeStatus.FAILED for item in episodes)
        ):
            self._repository.set_run_status(run_id, RunStatus.GENERATING)
        results = [
            self._run_episode(
                episode,
                allow_unverified_keyframes=allow_unverified_keyframes,
                allow_multi_clip=allow_multi_clip,
            )
            for episode in episodes
        ]
        current = self._repository.list_episodes(run_id)
        if all(item.status is EpisodeStatus.READY for item in current):
            self._repository.set_run_status(run_id, RunStatus.READY)
        elif any(item.status is EpisodeStatus.CONTENT_REVIEW for item in current):
            self._repository.set_run_status(run_id, RunStatus.REVIEWING)
        return {"runId": str(run_id), "episodes": results}

    def resume(self, run_id: uuid.UUID | None) -> list[dict[str, Any]]:
        """恢复已有异步视频任务，不创建新的供应商任务。"""

        results: list[dict[str, Any]] = []
        for step in self._repository.list_resumable_steps(run_id):
            if step.status is StepStatus.SUBMISSION_UNKNOWN:
                results.append(
                    {
                        "stepId": str(step.id),
                        "status": step.status.value,
                        "nextAction": "人工对账Ark任务列表",
                    }
                )
                continue
            if step.kind is not StepKind.VIDEO or not step.provider_task_id:
                continue
            episode = next(
                item
                for item in self._repository.list_episodes(step.run_id)
                if item.id == step.episode_id
            )
            if step.request_summary.get("generationStrategy") == "resolution_comparison":
                if self._resolution_comparison is None:
                    raise RuntimeError("恢复分辨率对比任务需要对比服务")
                results.append(self._resolution_comparison.resume_step(episode, step))
            else:
                results.append(self._video_execution.resume_step(episode, step))
        return results

    def _run_episode(
        self,
        episode: StoredEpisode,
        *,
        allow_unverified_keyframes: bool,
        allow_multi_clip: bool,
    ) -> dict[str, Any]:
        if episode.status in {EpisodeStatus.CONTENT_REVIEW, EpisodeStatus.READY}:
            return _episode_result(episode, "无需重复生成")
        if episode.status is EpisodeStatus.FAILED:
            failed_step = self._repository.latest_retryable_step(episode.id)
            if failed_step is not None:
                result = _episode_result(
                    episode,
                    "存在失败步骤；run-day不会隐式创建新的收费attempt",
                )
                result["failedStepId"] = str(failed_step.id)
                result["operationKey"] = failed_step.request_summary.get(
                    "operationKey"
                )
                result["nextAction"] = (
                    f"cvg retry-step {failed_step.id} --reason <原因>"
                )
                return result
        inputs = self._visual_preparation.prepare(
            episode,
            allow_unverified_keyframes=allow_unverified_keyframes,
        )
        if inputs is None:
            return _episode_result(
                self._repository.get_episode(
                    episode.run_id,
                    episode.plan.slot,
                ),
                "关键帧等待人工语义审核",
            )
        issues = validate_input_gate(
            episode.plan,
            (asset.role for asset in inputs)
            if episode.plan.video_input_mode.value == "multimodal_reference"
            else ("person", "cat", "style"),
        )
        failures = hard_failures(issues)
        if failures:
            raise ValueError("; ".join(item.message for item in failures))

        refreshed = self._repository.get_episode(
            episode.run_id,
            episode.plan.slot,
        )
        if refreshed.status in {
            EpisodeStatus.PLANNED,
            EpisodeStatus.FAILED,
            EpisodeStatus.PREPARING_VISUALS,
        }:
            self._repository.set_episode_status(
                refreshed.id,
                EpisodeStatus.VIDEO_PENDING,
            )
        refreshed = self._repository.get_episode(
            episode.run_id,
            episode.plan.slot,
        )
        return self._video_execution.execute(
            refreshed,
            inputs,
            allow_multi_clip=allow_multi_clip,
        )


def _episode_result(episode: StoredEpisode, message: str) -> dict[str, Any]:
    return {
        "episodeId": str(episode.id),
        "slot": episode.plan.slot.value,
        "status": episode.status.value,
        "message": message,
    }
