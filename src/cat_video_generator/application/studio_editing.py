"""主题创作台的人工编辑用例：剧本、日导演输出与流水线开关。

人工编辑是显式意图，因此只重跑与DayBrief的一致性、Prompt可编译性和整盘硬门，
**不重跑冷却校验**（``validate_episode_cooldown`` 是给自动生成路径用的）。
所有结构化校验在写库前完成——读路径对 ``script_json`` 直接
``EpisodeScript.model_validate``，非法JSON会崩所有读路径，绝不允许落库。
方案未定稿（draft）的Run还没有Episode行，其剧本只能经"编辑日导演→
resume_planning重生成"修改，本服务的剧本编辑只面向已定稿方案。
"""

from __future__ import annotations

import uuid
from typing import Any

from ..domain.contracts import (
    DailyProductionPlan,
    DayBrief,
    EpisodePlan,
    EpisodeScript,
    Slot,
)
from ..domain.pipeline import PipelineSettings
from ..domain.prompts import (
    PromptCompilationError,
    compile_video_prompt_preview,
)
from ..domain.rules import (
    hard_failures,
    validate_episode_against_brief,
    validate_plan_gate,
)
from ..domain.visual_profiles import SeriesVisualProfile, StyleProfile
from ..domain.workflow import RunStatus
from .ports import StudioStore


class StudioEditingService:
    """校验并落库创作台的人工编辑，不触发任何供应商调用。"""

    def __init__(
        self,
        *,
        repository: StudioStore,
        series_profile: SeriesVisualProfile,
        style_profile: StyleProfile,
        video_resolution: str = "720p",
    ) -> None:
        self._repository = repository
        self._series_profile = series_profile
        self._style_profile = style_profile
        self._video_resolution = video_resolution

    def update_episode_script(
        self,
        episode_id: uuid.UUID,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """结构化编辑单集剧本；校验全部通过才落库，失败不触碰任何数据。"""

        detail = self._repository.episode_detail(episode_id)
        status = str(detail["status"])
        if status not in {"planned", "video_pending", "failed"}:
            raise ValueError(
                f"该集状态{status}已进入媒体生产，不能编辑剧本；请先人工拒绝相关资产回到failed"
            )
        script = EpisodeScript.model_validate(payload)
        run_id = uuid.UUID(str(detail["runId"]))
        slot = Slot(str(detail["slot"]))
        context = self._repository.get_planning_context(run_id)
        if "dayBrief" not in context:
            raise ValueError("该Run没有DayBrief，不能校验剧本编辑")
        day_brief = DayBrief.model_validate(context["dayBrief"])
        slot_brief = next(item for item in day_brief.slots if item.slot is slot)
        episode = EpisodePlan(slot=slot, script=script)

        errors = [
            item.message
            for item in hard_failures(
                validate_episode_against_brief(
                    episode,
                    day_brief=day_brief,
                    slot_brief=slot_brief,
                    series_profile=self._series_profile,
                )
            )
        ]
        try:
            compile_video_prompt_preview(
                episode,
                resolution=self._video_resolution,
                style_profile=self._style_profile,
                series_profile=self._series_profile,
            )
        except PromptCompilationError as exc:
            errors.append(str(exc))

        stored_run = self._repository.get_run(run_id)
        if stored_run.plan is None:
            # 已定稿方案才有Episode行（finalize原子插入），此处仅为防御。
            raise ValueError("方案未定稿的剧本请编辑日导演后恢复规划重生成")
        candidate_plan = DailyProductionPlan(
            day_brief=day_brief,
            episodes=[episode if item.slot is slot else item for item in stored_run.plan.episodes],
        )
        errors.extend(
            item.message
            for item in hard_failures(
                validate_plan_gate(
                    candidate_plan,
                    expected_date=day_brief.content_date,
                    series_profile=self._series_profile,
                )
            )
        )
        if errors:
            raise ValueError("；".join(errors))

        self._repository.replace_episode_plan(run_id=run_id, episode=episode)
        return {
            "episodeId": str(episode_id),
            "slot": slot.value,
            "saved": True,
            "promptOverridesKept": bool(detail.get("promptOverrides")),
        }

    def update_day_brief(
        self,
        run_id: uuid.UUID,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """编辑日导演输出；仅方案未定稿可用，落库后清空全部时段草稿。"""

        stored_run = self._repository.get_run(run_id)
        if stored_run.plan is not None:
            raise ValueError("方案已定稿，日导演输出只能经局部重规划调整")
        if stored_run.status not in {
            RunStatus.DRAFT.value,
            RunStatus.PLANNING_REVIEW.value,
            RunStatus.FAILED.value,
        }:
            raise ValueError(f"Run状态{stored_run.status}不允许编辑日导演输出")
        day_brief = DayBrief.model_validate(payload)
        self._repository.update_day_brief(run_id=run_id, day_brief=day_brief)
        return {
            "runId": str(run_id),
            "saved": True,
            "episodeDraftsCleared": True,
        }

    def update_pipeline_settings(
        self,
        run_id: uuid.UUID,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """更新流水线阶段开关；本质是"下一次推进决策"的输入，任何状态可改。"""

        self._repository.get_run(run_id)
        settings = PipelineSettings.model_validate(payload)
        self._repository.save_pipeline_settings(run_id=run_id, settings=settings)
        return {
            "runId": str(run_id),
            "pipelineSettings": settings.model_dump(mode="json", by_alias=True),
        }
