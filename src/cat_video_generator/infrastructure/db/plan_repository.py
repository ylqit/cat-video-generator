"""全天方案与局部重规划的PostgreSQL持久化。

本Mixin拥有Run计划JSON和三个Episode的同步写入，不处理收费任务或媒体。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from ...domain.contracts import DailyProductionPlan, DayBrief, EpisodePlan
from ...domain.workflow import (
    EpisodeStatus,
    RunStatus,
    transition_episode,
    transition_run,
)
from .models import Episode, ProductionRun
from .query_repository import required_record as _required


class PlanPersistenceMixin:
    """要求宿主提供``_sessions``的方案持久化实现。"""

    def finalize_plan(
        self,
        *,
        run_id: uuid.UUID,
        plan: DailyProductionPlan,
        selected_candidate: int,
    ) -> None:
        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            run = _required(session, ProductionRun, run_id)
            run.status = transition_run(
                RunStatus(run.status),
                RunStatus.PLANNED,
            ).value
            run.theme = plan.theme
            run.context_json = {
                **run.context_json,
                "dayContext": plan.day_context,
            }
            run.plan_json = plan.model_dump(mode="json")
            run.selected_candidate = selected_candidate
            existing = session.execute(
                select(Episode.id).where(Episode.production_run_id == run_id)
            ).first()
            if existing is not None:
                return
            session.add_all(
                Episode(
                    production_run_id=run_id,
                    slot=episode.slot.value,
                    sort_order=episode.slot.sort_order,
                    title=episode.title,
                    script_json=episode.model_dump(mode="json"),
                    video_input_mode=episode.video_input_mode.value,
                    status=EpisodeStatus.PLANNED.value,
                )
                for episode in plan.episodes
            )

    def save_planning_context(
        self,
        *,
        run_id: uuid.UUID,
        day_brief: DayBrief,
        day_step_id: uuid.UUID,
        day_prompt_id: uuid.UUID,
        episode_drafts: dict[str, dict[str, Any]],
        planning_metadata: dict[str, Any],
    ) -> None:
        """保存可恢复的导演上下文，不要求三个Episode已经全部成功。"""

        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            run = _required(session, ProductionRun, run_id)
            run.theme = day_brief.theme
            run.context_json = {
                **run.context_json,
                "dayBrief": day_brief.model_dump(mode="json"),
                "dayDirectorStepId": str(day_step_id),
                "dayDirectorPromptId": str(day_prompt_id),
                "episodeDrafts": episode_drafts,
                "planningMetadata": planning_metadata,
            }

    def get_planning_context(self, run_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:  # type: ignore[attr-defined]
            return dict(_required(session, ProductionRun, run_id).context_json)

    def replace_episode_plan(
        self,
        *,
        run_id: uuid.UUID,
        episode: EpisodePlan,
    ) -> None:
        """局部重规划只替换指定时段脚本，旧步骤和媒体继续保留审计。"""

        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            run = _required(session, ProductionRun, run_id)
            row = session.execute(
                select(Episode).where(
                    Episode.production_run_id == run_id,
                    Episode.slot == episode.slot.value,
                )
            ).scalar_one()
            current = EpisodeStatus(row.status)
            if current not in {EpisodeStatus.PLANNED, EpisodeStatus.FAILED}:
                raise ValueError(
                    f"{episode.slot.value}状态{current.value}不允许局部重规划"
                )
            if current is EpisodeStatus.FAILED:
                row.status = transition_episode(
                    current,
                    EpisodeStatus.PLANNED,
                ).value
            row.title = episode.title
            row.script_json = episode.model_dump(mode="json")
            row.video_input_mode = episode.video_input_mode.value
            if run.plan_json is None:
                raise ValueError("Run尚未形成完整方案")
            plan = DailyProductionPlan.model_validate(run.plan_json)
            payload = plan.model_dump(mode="json")
            payload["episodes"] = [
                (
                    episode.model_dump(mode="json")
                    if item.slot is episode.slot
                    else item.model_dump(mode="json")
                )
                for item in plan.episodes
            ]
            run.plan_json = DailyProductionPlan.model_validate(payload).model_dump(
                mode="json"
            )
