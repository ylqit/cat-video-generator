"""三时段生产状态机编排。

本模块只决定按1/2/3推进哪些Episode以及何时切换Run状态。参考资产、关键帧、
Seedance任务和媒体QC分别由专用Application Service持有。
"""

from __future__ import annotations

import uuid
from typing import Any

from ..domain.contracts import Slot
from ..domain.workflow import EpisodeStatus, RunStatus, StepKind, StepStatus
from .ports import ProductionStore, StoredEpisode
from .video_execution import VideoExecutionService
from .visual_preparation import VisualPreparationService


class ProductionService:
    """把全天或单时段安全推进到人工内容审核。"""

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

    def prepare_storyboards_only(
        self,
        run_id: uuid.UUID,
        *,
        slot: Slot | None = None,
        prompt_overrides: dict[str, dict[str, str]] | None = None,
        allow_paid_generation: bool,
    ) -> dict[str, Any]:
        """创作台只生成整组故事板，绝不提交Seedance视频任务。"""

        if not allow_paid_generation:
            raise ValueError("故事板生成需要显式提供--allow-paid-generation")
        stored_run = self._repository.get_run(run_id)
        if stored_run.plan is None:
            raise ValueError("Run尚未形成可执行方案")
        episodes = (
            (self._repository.get_episode(run_id, slot),)
            if slot is not None
            else self._repository.list_episodes(run_id)
        )
        results: list[dict[str, Any]] = []
        for episode in episodes:
            slot_overrides = dict(self._repository.get_prompt_overrides(episode.id))
            slot_overrides.update((prompt_overrides or {}).get(episode.plan.slot.value, {}))
            assets = self._visual_preparation.prepare(
                episode,
                prompt_overrides=slot_overrides or None,
            )
            refreshed = self._repository.get_episode(run_id, episode.plan.slot)
            results.append(
                {
                    "episodeId": str(refreshed.id),
                    "slot": refreshed.plan.slot.value,
                    "status": refreshed.status.value,
                    "storyboardReady": assets is not None,
                    "message": ("故事板已就绪" if assets is not None else "故事板等待人工语义审核"),
                }
            )
        return {"runId": str(run_id), "episodes": results}

    def save_prompt_overrides(
        self,
        episode_id: uuid.UUID,
        *,
        overrides: dict[str, str] | None,
    ) -> None:
        """保存页面编辑的Prompt覆盖；仅校验键名，内容完全由调用方负责。"""

        allowed = {"storyboard", "video"}
        unknown = set(overrides or {}) - allowed
        if unknown:
            raise ValueError(f"不支持的Prompt覆盖键: {', '.join(sorted(unknown))}")
        cleaned = {key: value.strip() for key, value in (overrides or {}).items() if value.strip()}
        self._repository.save_prompt_overrides(
            episode_id=episode_id,
            overrides=cleaned or None,
        )

    def run_day(
        self,
        run_id: uuid.UUID,
        *,
        slot: Slot | None,
        allow_paid_generation: bool,
    ) -> dict[str, Any]:
        """生成指定Episode或按固定顺序推进全天。"""

        if not allow_paid_generation:
            raise ValueError("媒体生成需要显式提供--allow-paid-generation")
        stored_run = self._repository.get_run(run_id)
        if stored_run.plan is None:
            raise ValueError("Run尚未形成可执行方案")
        if stored_run.status == RunStatus.DELIVERED.value:
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
        results = [self._run_episode(episode) for episode in episodes]
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
            results.append(self._video_execution.resume_step(episode, step))
        return results

    def _run_episode(
        self,
        episode: StoredEpisode,
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
                result["operationKey"] = failed_step.operation_key
                result["nextAction"] = f"cvg retry-step {failed_step.id} --reason <原因>"
                return result
        # 创作台编辑过的Prompt覆盖随续跑一起传入，使哈希复用命中编辑版
        # 已生成帧，而不是回退编译出未编辑版本重新扣费。
        stored_overrides = self._repository.get_prompt_overrides(episode.id)
        if episode.status in {
            EpisodeStatus.VIDEO_PENDING,
            EpisodeStatus.VIDEO_GENERATING,
            EpisodeStatus.MEDIA_QC,
        }:
            # 视频阶段只消费已经批准并冻结的故事板。图片重试Prompt可以与最初编译文本
            # 不同，但这不应让视频入口回退到旧的rejected组图或隐式产生新图片费用。
            inputs = self._visual_preparation.approved_storyboard(episode)
        else:
            inputs = self._visual_preparation.prepare(
                episode,
                prompt_overrides=stored_overrides or None,
            )
        if inputs is None:
            return _episode_result(
                self._repository.get_episode(
                    episode.run_id,
                    episode.plan.slot,
                ),
                "故事板等待人工语义审核",
            )

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
            prompt_override=stored_overrides.get("video"),
        )


def _episode_result(episode: StoredEpisode, message: str) -> dict[str, Any]:
    return {
        "episodeId": str(episode.id),
        "slot": episode.plan.slot.value,
        "status": episode.status.value,
        "message": message,
    }
