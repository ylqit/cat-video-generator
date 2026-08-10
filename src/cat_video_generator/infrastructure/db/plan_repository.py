"""全天方案与局部重规划的PostgreSQL持久化。

本Mixin拥有Run计划JSON和三个Episode的同步写入，不处理收费任务或媒体。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select

from ...domain.contracts import DailyProductionPlan, DayBrief, EpisodePlan
from ...domain.pipeline import PipelineSettings
from ...domain.workflow import (
    EpisodeStatus,
    RunStatus,
    transition_episode,
    transition_run,
)
from .models import Episode, ProductionRun
from .query_repository import required_record as _required


def _script_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


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
        day_brief: DayBrief | None = None,
    ) -> None:
        """原子替换时段脚本及可选的总导演边界。"""

        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            run = _required(session, ProductionRun, run_id)
            row = session.execute(
                select(Episode).where(
                    Episode.production_run_id == run_id,
                    Episode.slot == episode.slot.value,
                )
            ).scalar_one()
            current = EpisodeStatus(row.status)
            if current not in {
                EpisodeStatus.PLANNED,
                EpisodeStatus.VIDEO_PENDING,
                EpisodeStatus.FAILED,
            }:
                raise ValueError(f"{episode.slot.value}状态{current.value}不允许局部重规划")
            if current is EpisodeStatus.VIDEO_PENDING:
                # 开场锚点批准后、视频尚未提交前仍允许人工修正剧本。旧视觉资产和审核
                # 保持不可变审计，新剧本会形成新的输入哈希并重新进入视觉准备。
                current = transition_episode(current, EpisodeStatus.FAILED)
                row.status = current.value
            if current is EpisodeStatus.FAILED:
                row.status = transition_episode(
                    current,
                    EpisodeStatus.PLANNED,
                ).value
            row.script_json = episode.script.model_dump(mode="json")
            # 覆盖正文仍作为创作草稿保留，但脚本变化后必须显式重新确认；生产读取只会返回
            # enabled且非stale的覆盖，因此不会把旧Prompt误用到新Episode。
            if row.prompt_overrides_json:
                row.prompt_overrides_json = {
                    **row.prompt_overrides_json,
                    "stale": True,
                }
            drafts = dict(run.planning_json.get("episodeDrafts", {}))
            drafts[episode.slot.value] = episode.script.model_dump(mode="json")
            planning_json = {**run.planning_json, "episodeDrafts": drafts}
            if day_brief is not None:
                planning_json["dayBrief"] = day_brief.model_dump(mode="json")
            run.planning_json = planning_json

    def get_prompt_overrides(self, episode_id: uuid.UUID) -> dict[str, str]:
        """只返回已显式启用、已确认且与当前脚本匹配的Prompt覆盖。"""

        with self._sessions() as session:  # type: ignore[attr-defined]
            row = _required(session, Episode, episode_id)
            raw = row.prompt_overrides_json or {}
            stale = bool(raw.get("stale")) or raw.get("sourceScriptSha256") != _script_sha256(
                row.script_json
            )
            if not raw.get("enabled") or stale:
                return {}
            values = raw.get("values")
            if not isinstance(values, dict):
                return {}
            return {
                str(key): str(value)
                for key, value in values.items()
                if isinstance(value, str) and value.strip()
            }

    def get_prompt_override_state(self, episode_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:  # type: ignore[attr-defined]
            row = _required(session, Episode, episode_id)
            raw = row.prompt_overrides_json or {}
            values = raw.get("values") if isinstance(raw.get("values"), dict) else {}
            source_hash = raw.get("sourceScriptSha256")
            return {
                "enabled": bool(raw.get("enabled", False)),
                "stale": bool(raw.get("stale", False))
                or (source_hash is not None and source_hash != _script_sha256(row.script_json)),
                "sourceScriptSha256": source_hash,
                "values": {
                    str(key): str(value)
                    for key, value in values.items()
                    if isinstance(value, str)
                },
            }

    def save_prompt_overrides(
        self,
        *,
        episode_id: uuid.UUID,
        overrides: dict[str, str] | None,
        enabled: bool,
    ) -> None:
        """保存高级Prompt覆盖，并绑定保存时的结构化脚本哈希。"""

        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            row = _required(session, Episode, episode_id)
            row.prompt_overrides_json = {
                "enabled": enabled,
                "stale": False,
                "sourceScriptSha256": _script_sha256(row.script_json),
                "values": overrides or {},
            }

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
        """读取Run持久化的流水线设置。"""

        with self._sessions() as session:  # type: ignore[attr-defined]
            raw = _required(session, ProductionRun, run_id).pipeline_settings_json
        if not raw:
            return PipelineSettings()
        return PipelineSettings.model_validate(raw)
