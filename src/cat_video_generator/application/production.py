"""全天三时段生产编排。

本服务只决定何时准备视觉、何时提交视频以及何时切换 Run 状态。图片和视频
供应商生命周期分别由专用服务拥有，失败节点不会被 run-day 隐式付费重试。
"""

from __future__ import annotations

import uuid
from typing import Any

from ..domain.contracts import Slot
from ..domain.pipeline import PlanningMode
from ..domain.workflow import EpisodeStatus, RunStatus, StepKind, StepStatus
from .ports import ProductionStore, StoredEpisode
from .video_execution import VideoExecutionService
from .visual_preparation import VisualPreparationService


class ProductionService:
    """按 morning/noon/evening 顺序推进视觉锚点和最终视频。"""

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

    def prepare_visuals_only(
        self,
        run_id: uuid.UUID,
        *,
        slot: Slot | None = None,
        prompt_overrides: dict[str, dict[str, str]] | None = None,
        allow_paid_generation: bool,
    ) -> dict[str, Any]:
        """只生成定妆图和开场锚点，绝不创建 Seedance 任务。"""

        if not allow_paid_generation:
            raise ValueError("视觉生成需要显式付费许可")
        self._require_plan(run_id, slot=slot)
        results: list[dict[str, Any]] = []
        for episode in self._episodes(run_id, slot):
            overrides = dict(self._repository.get_prompt_overrides(episode.id))
            overrides.update((prompt_overrides or {}).get(episode.plan.slot.value, {}))
            anchor = self._visual_preparation.prepare(
                episode,
                prompt_overrides=overrides or None,
            )
            refreshed = self._repository.get_episode(run_id, episode.plan.slot)
            results.append(
                {
                    "episodeId": str(refreshed.id),
                    "slot": refreshed.plan.slot.value,
                    "status": refreshed.status.value,
                    "visualReady": anchor is not None,
                    "message": "视觉锚点已就绪" if anchor is not None else "图片等待人工审核",
                }
            )
        return {"runId": str(run_id), "episodes": results}

    def save_prompt_overrides(
        self,
        episode_id: uuid.UUID,
        *,
        overrides: dict[str, str] | None,
        enabled: bool,
    ) -> None:
        """保存尚未提交的定妆、开场锚点和视频 Prompt 覆盖。"""

        allowed = {"look", "opening_anchor", "video"}
        unknown = set(overrides or {}) - allowed
        if unknown:
            raise ValueError(f"不支持的 Prompt 覆盖键: {', '.join(sorted(unknown))}")
        cleaned = {key: value.strip() for key, value in (overrides or {}).items() if value.strip()}
        self._repository.save_prompt_overrides(
            episode_id=episode_id,
            overrides=cleaned or None,
            enabled=enabled,
        )

    def run_day(
        self,
        run_id: uuid.UUID,
        *,
        slot: Slot | None,
        allow_paid_generation: bool,
    ) -> dict[str, Any]:
        """生成指定时段或全天；已失败的收费节点只返回 retry-step。"""

        if not allow_paid_generation:
            raise ValueError("媒体生成需要显式付费许可")
        stored_run = self._require_plan(run_id, slot=slot)
        if stored_run.status == RunStatus.DELIVERED.value:
            raise ValueError("已交付 Run 不允许继续生成")
        episodes = self._episodes(run_id, slot)
        if stored_run.status in {
            RunStatus.PLANNED.value,
            RunStatus.FAILED.value,
            RunStatus.REVIEWING.value,
        }:
            self._repository.set_run_status(run_id, RunStatus.GENERATING)
        results = [self._run_episode(episode) for episode in episodes]
        current = self._repository.list_episodes(run_id)
        if len(current) == 3 and all(item.status is EpisodeStatus.READY for item in current):
            target = (
                RunStatus.READY
                if self._repository.get_pipeline_settings(run_id).planning_mode
                is PlanningMode.AUTO_DAY
                else RunStatus.REVIEWING
            )
            self._repository.set_run_status(run_id, target)
        elif any(item.status is EpisodeStatus.CONTENT_REVIEW for item in current):
            self._repository.set_run_status(run_id, RunStatus.REVIEWING)
        return {"runId": str(run_id), "episodes": results}

    def resume(self, run_id: uuid.UUID | None) -> list[dict[str, Any]]:
        """恢复已有异步视频任务，不创建新的供应商 POST。"""

        results: list[dict[str, Any]] = []
        for step in self._repository.list_resumable_steps(run_id):
            if step.status is StepStatus.SUBMISSION_UNKNOWN:
                results.append(
                    {
                        "stepId": str(step.id),
                        "status": step.status.value,
                        "nextAction": "查询 Ark 任务并人工对账",
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
            results.append(self._video_execution.resume_step(episode, step))
        return results

    def _run_episode(self, episode: StoredEpisode) -> dict[str, Any]:
        if episode.status in {EpisodeStatus.CONTENT_REVIEW, EpisodeStatus.READY}:
            return _episode_result(episode, "无需重复生成")

        # 审核通过的开场锚点是视频输入的事实来源。一次较晚但失败的图片尝试
        # 只保留在尝试历史中，不能遮蔽已经批准且仍属于当前 Episode 的资产，
        # 更不能因此让 run-day 隐式再次调用 Seedream。
        try:
            anchor = self._visual_preparation.approved_opening_anchor(episode)
        except RuntimeError:
            anchor = None

        if anchor is None and episode.status is EpisodeStatus.FAILED:
            failed_step = self._repository.latest_retryable_step(episode.id)
            if failed_step is not None:
                result = _episode_result(episode, "存在失败步骤，run-day 不会隐式付费重试")
                result.update(
                    failedStepId=str(failed_step.id),
                    operationKey=failed_step.operation_key,
                    nextAction=f"cvg retry-step {failed_step.id} --reason <原因>",
                )
                return result
        overrides = self._repository.get_prompt_overrides(episode.id)
        if anchor is None:
            anchor = self._visual_preparation.prepare(
                episode,
                prompt_overrides=overrides or None,
            )
        if anchor is None:
            refreshed = self._repository.get_episode(episode.run_id, episode.plan.slot)
            return _episode_result(refreshed, "定妆图或开场锚点等待人工审核")
        refreshed = self._repository.get_episode(episode.run_id, episode.plan.slot)
        if refreshed.status in {
            EpisodeStatus.PLANNED,
            EpisodeStatus.FAILED,
            EpisodeStatus.PREPARING_VISUALS,
        }:
            self._repository.set_episode_status(refreshed.id, EpisodeStatus.VIDEO_PENDING)
        refreshed = self._repository.get_episode(episode.run_id, episode.plan.slot)
        return self._video_execution.execute(
            refreshed,
            anchor,
            prompt_override=overrides.get("video"),
        )

    def _require_plan(self, run_id: uuid.UUID, *, slot: Slot | None):
        stored_run = self._repository.get_run(run_id)
        if slot is not None:
            self._repository.get_episode(run_id, slot)
        elif stored_run.plan is None:
            raise ValueError("Run 尚未形成可执行方案")
        return stored_run

    def _episodes(self, run_id: uuid.UUID, slot: Slot | None) -> tuple[StoredEpisode, ...]:
        return (
            (self._repository.get_episode(run_id, slot),)
            if slot is not None
            else self._repository.list_episodes(run_id)
        )


def _episode_result(episode: StoredEpisode, message: str) -> dict[str, Any]:
    return {
        "episodeId": str(episode.id),
        "slot": episode.plan.slot.value,
        "status": episode.status.value,
        "message": message,
    }
