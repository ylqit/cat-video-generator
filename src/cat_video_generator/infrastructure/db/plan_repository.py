"""全天方案与局部重规划的PostgreSQL持久化。

本Mixin拥有Run计划JSON和三个Episode的同步写入，不处理收费任务或媒体。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from ...domain.contracts import AcceptedOutcome, DailyProductionPlan, DayBrief, EpisodePlan, Slot
from ...domain.pipeline import PipelineSettings
from ...domain.workflow import (
    EpisodeStatus,
    RunStatus,
    StepStatus,
    transition_episode,
    transition_run,
)
from .models import Episode, ProductionRun, WorkflowStep
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

    def save_planned_episode(
        self,
        *,
        run_id: uuid.UUID,
        episode: EpisodePlan,
    ):
        """顺序模式原子落库单个时段；已存在时段不会被静默覆盖。"""

        from .records import stored_episode

        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            run = _required(session, ProductionRun, run_id)
            existing = session.execute(
                select(Episode).where(
                    Episode.production_run_id == run_id,
                    Episode.slot == episode.slot.value,
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise ValueError(f"{episode.slot.value}时段已经规划，不能重复创建")
            row = Episode(
                production_run_id=run_id,
                slot=episode.slot.value,
                sort_order=episode.slot.sort_order,
                script_json=episode.script.model_dump(mode="json"),
                status=EpisodeStatus.PLANNED.value,
            )
            session.add(row)
            drafts = dict(run.planning_json.get("episodeDrafts", {}))
            drafts[episode.slot.value] = episode.script.model_dump(mode="json")
            run.planning_json = {**run.planning_json, "episodeDrafts": drafts}
            current = RunStatus(run.status)
            if current in {
                RunStatus.DRAFT,
                RunStatus.PLANNING_REVIEW,
                RunStatus.FAILED,
            }:
                run.status = transition_run(current, RunStatus.PLANNED).value
            session.flush()
            return stored_episode(row)

    def save_initial_planning_metadata(
        self,
        *,
        run_id: uuid.UUID,
        planning_metadata: dict[str, Any],
        planning_context: str,
        story_mode: str,
    ) -> None:
        """在调用总导演前保存可重建输入，避免契约拒绝后只能新建整条Run。"""

        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            run = _required(session, ProductionRun, run_id)
            run.planning_json = {
                **run.planning_json,
                "planningMetadata": planning_metadata,
                "planningRequest": {
                    "planningContext": planning_context,
                    "storyMode": story_mode,
                },
            }

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
        acknowledge_downstream_replacement: bool = False,
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
            active_step = session.execute(
                select(WorkflowStep.id).where(
                    WorkflowStep.episode_id == row.id,
                    WorkflowStep.status.in_(
                        tuple(
                            item.value
                            for item in (
                                StepStatus.PENDING,
                                StepStatus.SUBMITTING,
                                StepStatus.QUEUED,
                                StepStatus.RUNNING,
                                StepStatus.AWAITING_REVIEW,
                            )
                        )
                    ),
                )
            ).scalar_one_or_none()
            if active_step is not None:
                raise ValueError("该时段仍有活动中的Provider或审核节点，不能同时重新规划")
            if current not in {EpisodeStatus.PLANNED, EpisodeStatus.FAILED}:
                if not acknowledge_downstream_replacement:
                    raise ValueError("该时段已有下游结果，必须显式确认替换后才能重新规划")
                current = transition_episode(current, EpisodeStatus.FAILED)
                row.status = current.value
            if current is EpisodeStatus.FAILED:
                row.status = transition_episode(
                    current,
                    EpisodeStatus.PLANNED,
                ).value
            row.script_json = episode.script.model_dump(mode="json")
            row.selected_video_asset_id = None
            # 覆盖正文仍作为创作草稿保留，但脚本变化后必须显式重新确认；生产读取只会返回
            # enabled且非stale的覆盖，因此不会把旧Prompt误用到新Episode。
            if row.prompt_overrides_json:
                row.prompt_overrides_json = {
                    **row.prompt_overrides_json,
                    "stale": True,
                }
            drafts = dict(run.planning_json.get("episodeDrafts", {}))
            drafts[episode.slot.value] = episode.script.model_dump(mode="json")
            stale_nodes = set(run.planning_json.get("staleNodes", []))
            stale_nodes.update(
                {
                    f"{episode.slot.value}:look",
                    f"{episode.slot.value}:opening-anchor",
                    f"{episode.slot.value}:video",
                    f"{episode.slot.value}:review",
                    f"{episode.slot.value}:outcome",
                }
            )
            outcomes = dict(run.planning_json.get("acceptedOutcomes", {}))
            if episode.slot.value in outcomes:
                if not acknowledge_downstream_replacement:
                    raise ValueError("该时段结果卡已经确认，必须显式撤销后才能重新规划")
                outcomes.pop(episode.slot.value, None)
            planning_json = {
                **run.planning_json,
                "episodeDrafts": drafts,
                "staleNodes": sorted(stale_nodes),
                "acceptedOutcomes": outcomes,
            }
            if day_brief is not None:
                planning_json["dayBrief"] = day_brief.model_dump(mode="json")
            run.planning_json = planning_json
            if RunStatus(run.status) is RunStatus.READY:
                run.status = transition_run(RunStatus.READY, RunStatus.GENERATING).value

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
                "dayBriefConfirmedAt": datetime.now(timezone.utc).isoformat(),
            }

    def save_accepted_outcome(
        self,
        *,
        run_id: uuid.UUID,
        slot: Slot,
        outcome: AcceptedOutcome,
    ) -> None:
        """原子保存用户确认结果；较晚时段存在后禁止改写历史事实。"""

        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            run = _required(session, ProductionRun, run_id)
            episode = session.execute(
                select(Episode).where(
                    Episode.production_run_id == run_id,
                    Episode.slot == slot.value,
                )
            ).scalar_one_or_none()
            if episode is None or EpisodeStatus(episode.status) is not EpisodeStatus.READY:
                raise ValueError("只有人工批准的最终视频才能确认时段结果")
            later_slots = {
                str(value)
                for value in session.execute(
                    select(Episode.slot).where(
                        Episode.production_run_id == run_id,
                        Episode.sort_order > slot.sort_order,
                    )
                ).scalars()
            }
            stale_slots = set(run.planning_json.get("staleSlots", []))
            if later_slots and not later_slots.issubset(stale_slots):
                raise ValueError(
                    "后续时段已经读取当前结果；请先在视频版本选择中撤销旧结果卡并标记后续内容过期"
                )
            outcomes = dict(run.planning_json.get("acceptedOutcomes", {}))
            if slot.value in outcomes:
                raise ValueError("结果卡已经确认；如成片事实错误，请重做该时段而不是覆盖历史")
            outcomes[slot.value] = outcome.model_dump(mode="json", by_alias=True)
            stale_slots.discard(slot.value)
            stale_nodes = {
                item
                for item in run.planning_json.get("staleNodes", [])
                if not str(item).startswith(f"{slot.value}:")
            }
            run.planning_json = {
                **run.planning_json,
                "acceptedOutcomes": outcomes,
                "staleSlots": sorted(stale_slots),
                "staleNodes": sorted(stale_nodes),
            }
            if len(outcomes) == len(Slot):
                episodes = tuple(
                    session.execute(
                        select(Episode).where(Episode.production_run_id == run_id)
                    ).scalars()
                )
                if len(episodes) == len(Slot) and all(
                    EpisodeStatus(item.status) is EpisodeStatus.READY for item in episodes
                ):
                    run.status = transition_run(
                        RunStatus(run.status),
                        RunStatus.READY,
                    ).value

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
