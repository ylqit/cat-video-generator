"""全天方案与局部重规划的PostgreSQL持久化。

本Mixin拥有Run计划JSON和三个Episode的同步写入，不处理收费任务或媒体。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from ...domain.contracts import DailyProductionPlan, DayBrief, EpisodePlan, Slot
from ...domain.pipeline import PipelineSettings
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
            run.planning_json = {
                **run.planning_json,
                "dayBrief": plan.day_brief.model_dump(mode="json"),
                "selectedCandidate": selected_candidate,
            }
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
                    script_json=episode.script.model_dump(mode="json"),
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
            run.planning_json = {
                **run.planning_json,
                "dayBrief": day_brief.model_dump(mode="json"),
                "dayDirectorStepId": str(day_step_id),
                "dayDirectorPromptId": str(day_prompt_id),
                "episodeDrafts": episode_drafts,
                "planningMetadata": planning_metadata,
            }

    def get_planning_context(self, run_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:  # type: ignore[attr-defined]
            return dict(_required(session, ProductionRun, run_id).planning_json)

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
            row.script_json = episode.script.model_dump(mode="json")
            drafts = dict(run.planning_json.get("episodeDrafts", {}))
            drafts[episode.slot.value] = episode.script.model_dump(mode="json")
            run.planning_json = {**run.planning_json, "episodeDrafts": drafts}

    def get_prompt_overrides(self, episode_id: uuid.UUID) -> dict[str, str]:
        """读取页面编辑后的Prompt覆盖；缺省为空字典。"""

        with self._sessions() as session:  # type: ignore[attr-defined]
            row = _required(session, Episode, episode_id)
            raw = row.prompt_overrides_json or {}
            return {
                str(key): str(value)
                for key, value in raw.items()
                if isinstance(value, str) and value.strip()
            }

    def save_prompt_overrides(
        self,
        *,
        episode_id: uuid.UUID,
        overrides: dict[str, str] | None,
    ) -> None:
        """持久化页面编辑的Prompt覆盖；``None``或空字典表示清除。"""

        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            row = _required(session, Episode, episode_id)
            row.prompt_overrides_json = overrides or None

    def update_day_brief(
        self,
        *,
        run_id: uuid.UUID,
        day_brief: DayBrief,
    ) -> None:
        """人工编辑日导演输出；旧的时段草稿与新Brief错配，必须清空。"""

        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            run = _required(session, ProductionRun, run_id)
            run.planning_json = {
                **run.planning_json,
                "dayBrief": day_brief.model_dump(mode="json"),
                "episodeDrafts": {},
            }

    def update_episode_draft(
        self,
        *,
        run_id: uuid.UUID,
        slot: Slot,
        script: dict[str, Any],
    ) -> None:
        """方案未定稿时把人工编辑的时段脚本写回可恢复草稿。"""

        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            run = _required(session, ProductionRun, run_id)
            drafts = dict(run.planning_json.get("episodeDrafts", {}))
            drafts[slot.value] = script
            run.planning_json = {**run.planning_json, "episodeDrafts": drafts}

    def save_pipeline_settings(
        self,
        *,
        run_id: uuid.UUID,
        settings: PipelineSettings,
    ) -> None:
        """持久化流水线阶段开关与一次性付费授权。"""

        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            run = _required(session, ProductionRun, run_id)
            run.pipeline_settings_json = settings.model_dump(
                mode="json",
                by_alias=True,
            )

    def get_pipeline_settings(self, run_id: uuid.UUID) -> PipelineSettings:
        """读取流水线设置；历史Run缺省列按legacy_default返回。"""

        with self._sessions() as session:  # type: ignore[attr-defined]
            raw = _required(session, ProductionRun, run_id).pipeline_settings_json
        if not raw:
            return PipelineSettings.legacy_default()
        return PipelineSettings.model_validate(raw)
