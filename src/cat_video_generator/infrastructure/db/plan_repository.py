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

from ...domain.contracts import (
    AcceptedOutcome,
    CrossSlotReference,
    CrossSlotReferenceTarget,
    DailyProductionPlan,
    EpisodePlan,
    ProjectOutlineV3,
    Slot,
    StoryConnection,
    StoryProjectInput,
)
from ...domain.pipeline import PipelineSettings
from ...domain.workflow import (
    EpisodeStatus,
    RunStatus,
    StepStatus,
    transition_episode,
    transition_run,
)
from .models import Asset, Episode, ProductionRun, WorkflowStep
from .query_repository import required_record as _required
from .records import ensure_current_contract


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
    ) -> None:
        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            run = _required(session, ProductionRun, run_id)
            ensure_current_contract(run)
            run.status = transition_run(
                RunStatus(run.status),
                RunStatus.PLANNED,
            ).value
            run.planning_json = {
                **run.planning_json,
                "projectInput": plan.project_input.model_dump(mode="json"),
                "projectOutline": (
                    None if plan.outline is None else plan.outline.model_dump(mode="json")
                ),
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
            ensure_current_contract(run)
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
        project_input: StoryProjectInput,
        episode_drafts: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """保存生活故事项目输入；已有剧本项目不会因此产生Director Step。"""

        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            run = _required(session, ProductionRun, run_id)
            ensure_current_contract(run)
            run.planning_json = {
                **run.planning_json,
                "planningMetadata": planning_metadata,
                "projectInput": project_input.model_dump(mode="json"),
                "episodeDrafts": episode_drafts or {},
            }

    def save_planning_context(
        self,
        *,
        run_id: uuid.UUID,
        project_input: StoryProjectInput,
        project_outline: ProjectOutlineV3,
        project_outline_step_id: uuid.UUID,
        project_outline_prompt_id: uuid.UUID,
        episode_drafts: dict[str, dict[str, Any]],
        planning_metadata: dict[str, Any],
    ) -> None:
        """保存可恢复的主题扩写上下文，不要求三个Episode已经全部成功。"""

        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            run = _required(session, ProductionRun, run_id)
            ensure_current_contract(run)
            run.planning_json = {
                **run.planning_json,
                "projectInput": project_input.model_dump(mode="json"),
                "projectOutline": project_outline.model_dump(mode="json"),
                "projectOutlineDirectorStepId": str(project_outline_step_id),
                "projectOutlineDirectorPromptId": str(project_outline_prompt_id),
                "episodeDrafts": episode_drafts,
                "planningMetadata": planning_metadata,
            }

    def get_planning_context(self, run_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:  # type: ignore[attr-defined]
            run = _required(session, ProductionRun, run_id)
            ensure_current_contract(run)
            return dict(run.planning_json)

    def replace_episode_plan(
        self,
        *,
        run_id: uuid.UUID,
        episode: EpisodePlan,
        project_outline: ProjectOutlineV3 | None = None,
        acknowledge_downstream_replacement: bool = False,
    ) -> None:
        """原子替换时段脚本及可选的总导演边界。"""

        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            run = _required(session, ProductionRun, run_id)
            ensure_current_contract(run)
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
            if project_outline is not None:
                planning_json["projectOutline"] = project_outline.model_dump(mode="json")
            run.planning_json = planning_json
            if RunStatus(run.status) is RunStatus.READY:
                run.status = transition_run(RunStatus.READY, RunStatus.GENERATING).value

    def get_prompt_overrides(self, episode_id: uuid.UUID) -> dict[str, str]:
        """只返回已显式启用、已确认且与当前脚本匹配的Prompt覆盖。"""

        with self._sessions() as session:  # type: ignore[attr-defined]
            row = _required(session, Episode, episode_id)
            ensure_current_contract(
                _required(session, ProductionRun, row.production_run_id)
            )
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
            ensure_current_contract(
                _required(session, ProductionRun, row.production_run_id)
            )
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
            ensure_current_contract(
                _required(session, ProductionRun, row.production_run_id)
            )
            row.prompt_overrides_json = {
                "enabled": enabled,
                "stale": False,
                "sourceScriptSha256": _script_sha256(row.script_json),
                "values": overrides or {},
            }

    def update_project_outline(
        self,
        *,
        run_id: uuid.UUID,
        project_outline: ProjectOutlineV3,
    ) -> None:
        """人工确认主题扩写边界；已有剧本项目不需要这个确认节点。"""

        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            run = _required(session, ProductionRun, run_id)
            ensure_current_contract(run)
            raw_input = run.planning_json.get("projectInput")
            if not isinstance(raw_input, dict):
                raise ValueError("该项目缺少StoryProjectInput")
            project_input = StoryProjectInput.model_validate(raw_input)
            if project_input.input_mode.value != "theme_expand":
                raise ValueError("已有剧本项目不需要确认总导演大纲")
            if project_outline.content_date != run.content_date:
                raise ValueError("ProjectOutlineV3日期不能改写项目固定日期")
            if project_outline.theme != project_input.theme:
                raise ValueError("ProjectOutlineV3主题不能改写项目主题")
            run.planning_json = {
                **run.planning_json,
                "projectOutline": project_outline.model_dump(mode="json"),
                "episodeDrafts": {},
                "projectOutlineConfirmedAt": datetime.now(timezone.utc).isoformat(),
            }

    def save_episode_source(
        self,
        *,
        run_id: uuid.UUID,
        slot: Slot,
        source: str,
    ) -> StoryProjectInput:
        """在时段导演收费前保存用户原文；已规划时段不可被静默改写。"""

        normalized = source.strip()
        if len(normalized) < 4:
            raise ValueError("时段原始剧本至少需要4个字符")
        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            run = _required(session, ProductionRun, run_id)
            ensure_current_contract(run)
            existing = session.execute(
                select(Episode.id).where(
                    Episode.production_run_id == run_id,
                    Episode.slot == slot.value,
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise ValueError(f"{slot.value}已经完成镜头化规划，不能覆盖其原始剧本")
            raw_input = run.planning_json.get("projectInput")
            if not isinstance(raw_input, dict):
                raise ValueError("该项目缺少StoryProjectInput")
            project_input = StoryProjectInput.model_validate(raw_input)
            if project_input.input_mode.value != "episode_scripts":
                raise ValueError("只有已有剧本项目允许逐时段保存原始剧本")
            sources = project_input.episode_sources.model_copy(update={slot.value: normalized})
            updated = project_input.model_copy(update={"episode_sources": sources})
            run.planning_json = {
                **run.planning_json,
                "projectInput": updated.model_dump(mode="json"),
            }
            return updated

    def save_story_connection(
        self,
        *,
        run_id: uuid.UUID,
        slot: Slot,
        connection: StoryConnection,
    ) -> None:
        """保存用户确认的可选关联卡；关联卡与是否加载是同一个明确决定。"""

        if slot is Slot.MORNING:
            raise ValueError("上午没有前序时段，不能保存剧情关联卡")
        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            run = _required(session, ProductionRun, run_id)
            ensure_current_contract(run)
            outcomes = run.planning_json.get("acceptedOutcomes", {}) or {}
            required_slots = [
                previous.value
                for previous in Slot
                if previous.sort_order < slot.sort_order
            ]
            if any(previous not in outcomes for previous in required_slots):
                raise ValueError("前序实际结果卡尚未全部确认，不能建立剧情关联")
            episodes = session.execute(
                select(Episode.slot).where(
                    Episode.production_run_id == run_id,
                    Episode.sort_order >= slot.sort_order,
                )
            ).scalars().all()
            if episodes:
                raise ValueError("目标时段已经完成镜头化；请先重置该时段后再修改关联卡")
            connections = dict(run.planning_json.get("storyConnections", {}))
            connections[slot.value] = connection.model_dump(mode="json", by_alias=True)
            run.planning_json = {
                **run.planning_json,
                "storyConnections": connections,
            }

    def save_cross_slot_references(
        self,
        *,
        run_id: uuid.UUID,
        slot: Slot,
        references: tuple[CrossSlotReference, ...],
    ) -> None:
        """保存用户显式选择的前序媒体，不自动扩张为所有匹配素材。"""

        if slot is Slot.MORNING and references:
            raise ValueError("上午没有前序时段，不能引用前序媒体")
        asset_ids = [reference.asset_id for reference in references]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("同一前序媒体只能选择一次")
        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            run = _required(session, ProductionRun, run_id)
            ensure_current_contract(run)
            assets = {
                asset.id: asset
                for asset in session.execute(
                    select(Asset).where(Asset.id.in_(asset_ids))
                ).scalars()
            }
            if len(assets) != len(asset_ids):
                raise ValueError("存在无法读取的前序媒体")
            for reference in references:
                asset = assets[reference.asset_id]
                if asset.status not in {"approved", "ready"}:
                    raise ValueError(f"素材{asset.id}尚未批准")
                if asset.media_type not in {"image", "video"}:
                    raise ValueError(f"素材{asset.id}的模态不支持跨时段引用")
                if (
                    reference.apply_to
                    in {
                        CrossSlotReferenceTarget.OPENING_ANCHOR,
                        CrossSlotReferenceTarget.BOTH,
                    }
                    and asset.media_type != "image"
                ):
                    raise ValueError(f"素材{asset.id}不是图片，不能用于开场锚点")
                if asset.scope == "canon":
                    continue
                if asset.production_run_id != run_id or asset.episode_id is None:
                    raise ValueError(f"素材{asset.id}不属于当前项目的前序时段")
                source_episode = _required(session, Episode, asset.episode_id)
                if source_episode.sort_order >= slot.sort_order:
                    raise ValueError(f"素材{asset.id}不是目标时段之前的媒体")
            values = dict(run.planning_json.get("crossSlotReferences", {}))
            values[slot.value] = [
                reference.model_dump(mode="json", by_alias=True)
                for reference in references
            ]
            run.planning_json = {
                **run.planning_json,
                "crossSlotReferences": values,
            }

    def save_accepted_outcome(
        self,
        *,
        run_id: uuid.UUID,
        slot: Slot,
        outcome: AcceptedOutcome,
    ) -> None:
        """原子保存用户确认结果；后续导演不会隐式读取这张结果卡。"""

        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            run = _required(session, ProductionRun, run_id)
            ensure_current_contract(run)
            episode = session.execute(
                select(Episode).where(
                    Episode.production_run_id == run_id,
                    Episode.slot == slot.value,
                )
            ).scalar_one_or_none()
            if episode is None or EpisodeStatus(episode.status) is not EpisodeStatus.READY:
                raise ValueError("只有人工批准的最终视频才能确认时段结果")
            stale_slots = set(run.planning_json.get("staleSlots", []))
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
            ensure_current_contract(run)
            run.pipeline_settings_json = settings.model_dump(
                mode="json",
                by_alias=True,
            )

    def get_pipeline_settings(self, run_id: uuid.UUID) -> PipelineSettings:
        """读取Run持久化的流水线设置。"""

        with self._sessions() as session:  # type: ignore[attr-defined]
            run = _required(session, ProductionRun, run_id)
            ensure_current_contract(run)
            raw = run.pipeline_settings_json
        if not raw:
            return PipelineSettings()
        return PipelineSettings.model_validate(raw)
