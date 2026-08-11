"""生活故事项目与逐时段导演编排。

总导演只服务``theme_expand``；用户已有逐集剧本时先保存项目输入，直到明确规划
某个时段才产生导演费用。结果卡只负责解锁与可选建议；后续导演只读取用户明确
保存并启用的剧情关联卡正文。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ..domain.contracts import (
    AcceptedOutcome,
    ActivityFocus,
    ActivityFocusMode,
    ConnectionSuggestion,
    DailyProductionPlan,
    DurationBand,
    DurationMode,
    EpisodePlan,
    EpisodeScript,
    OutlineEpisode,
    ProjectOutlineV3,
    RecentContentSummary,
    RunCreativeControls,
    Slot,
    StoryConnection,
    StoryInputMode,
    StoryProjectInput,
)
from ..domain.pipeline import PipelineSettings, PlanningMode
from ..domain.prompts import (
    compile_connection_suggestion_prompt,
    compile_day_director_prompt,
    compile_day_repair_prompt,
    compile_episode_adaptation_prompt,
    compile_episode_director_prompt,
)
from ..domain.rules import (
    GateIssue,
    IssueLevel,
    hard_failures,
    validate_episode_cooldown,
    validate_episode_gate,
    validate_plan_gate,
)
from ..domain.story_patterns import StoryPattern
from ..domain.visual_profiles import CreativeProfileOverride, SeriesVisualProfile, StyleProfile
from ..domain.workflow import EpisodeStatus, PromptPurpose, RunStatus, StepStatus
from .director_execution import DirectorCandidateRejected, DirectorInvoker
from .event_seeds import EventSeedCatalog
from .ports import DirectorGateway, PlanningStore, StoredStep


@dataclass(frozen=True, slots=True)
class ProjectPlanningResult:
    """项目建立或三个时段全部规划后的结果。"""

    run_id: uuid.UUID
    project_input: StoryProjectInput
    outline: ProjectOutlineV3 | None = None
    plan: DailyProductionPlan | None = None


@dataclass(frozen=True, slots=True)
class ProjectOutlinePause:
    """主题扩写的总导演已经完成，等待人工确认项目大纲。"""

    run_id: uuid.UUID
    project_outline: ProjectOutlineV3


@dataclass(frozen=True, slots=True)
class SlotPlanningResult:
    run_id: uuid.UUID
    slot: Slot
    attempt: int
    episode: EpisodePlan


@dataclass(frozen=True, slots=True)
class ConnectionSuggestionResult:
    """一次显式付费的剧情关联建议；返回草稿，不自动启用或写入导演上下文。"""

    run_id: uuid.UUID
    slot: Slot
    attempt: int
    step_id: uuid.UUID
    suggestion: ConnectionSuggestion


@dataclass(frozen=True, slots=True)
class EpisodeReplanResult:
    run_id: uuid.UUID
    slot: Slot
    attempt: int
    episode: EpisodePlan


class PlanningReviewRequired(RuntimeError):
    """Provider已返回内容，但业务契约不允许继续进入媒体收费链路。"""

    def __init__(
        self,
        *,
        run_id: uuid.UUID,
        errors: tuple[str, ...],
        slot: Slot | None = None,
    ) -> None:
        scope = "项目大纲" if slot is None else f"{slot.value}时段"
        super().__init__(f"Run {run_id} 的{scope}需要人工审核：" + "；".join(errors))
        self.run_id = run_id
        self.slot = slot
        self.errors = errors


class PlanningService:
    """拥有项目创建、时段导演、局部重规划及其付费边界。"""

    def __init__(
        self,
        *,
        repository: PlanningStore,
        director: DirectorGateway,
        provider_name: str,
        series_profile: SeriesVisualProfile,
        style_profile: StyleProfile,
        event_seed_catalog: EventSeedCatalog | None = None,
    ) -> None:
        self._repository = repository
        self._director_invoker = DirectorInvoker(
            repository=repository,
            director=director,
            provider_name=provider_name,
        )
        self._event_seed_catalog = event_seed_catalog or EventSeedCatalog(Path("content/events"))
        self._series_profile = series_profile
        self._style_profile = style_profile

    def create_project(
        self,
        *,
        target_date: date,
        project_input: StoryProjectInput,
        allow_paid_generation: bool,
        creative_profile: CreativeProfileOverride | None = None,
        pipeline_settings: PipelineSettings | None = None,
        creative_controls: RunCreativeControls | None = None,
    ) -> ProjectPlanningResult | ProjectOutlinePause:
        """先冻结项目输入，再按输入模式决定是否调用总导演。"""

        controls = creative_controls or RunCreativeControls()
        settings = pipeline_settings or PipelineSettings(
            allowPaidGeneration=allow_paid_generation
        )
        requires_director = (
            project_input.input_mode is StoryInputMode.THEME_EXPAND
            or settings.planning_mode is PlanningMode.AUTO_DAY
        )
        if (
            settings.planning_mode is PlanningMode.AUTO_DAY
            and project_input.input_mode is StoryInputMode.EPISODE_SCRIPTS
            and project_input.episode_sources.populated_slots != tuple(Slot)
        ):
            raise ValueError("auto_day已有剧本模式必须提供完整morning、noon、evening原文")
        if requires_director:
            self._check_paid(allow_paid_generation)

        series_profile = (creative_profile or CreativeProfileOverride()).apply_to(
            self._series_profile
        )
        recent = self._repository.list_recent_completed_summaries(limit=6)
        seeds = self._event_seed_catalog.select(
            series_profile_hash=series_profile.fingerprint(),
            content_date=target_date,
            planning_revision=1,
            planning_context=project_input.theme,
        )
        patterns = self._event_seed_catalog.select_patterns(
            series_profile_hash=series_profile.fingerprint(),
            content_date=target_date,
            planning_revision=1,
        )
        metadata: dict[str, Any] = {
            "planningRevision": 1,
            "seriesProfileHash": series_profile.fingerprint(),
            "seriesProfile": series_profile.model_dump(mode="json"),
            "recentSummaries": [item.model_dump(mode="json") for item in recent],
            "eventSeeds": [item.model_dump(mode="json") for item in seeds],
            "storyPatterns": {
                slot.value: pattern.model_dump(mode="json")
                for slot, pattern in patterns.items()
            },
            "creativeControls": controls.model_dump(mode="json"),
        }
        run_id = self._repository.create_draft_run(target_date)
        self._repository.save_pipeline_settings(run_id=run_id, settings=settings)
        # 项目输入必须先于任何Ark调用落库；已有剧本guided路径至此没有收费Step。
        self._repository.save_initial_planning_metadata(
            run_id=run_id,
            planning_metadata=metadata,
            project_input=project_input,
        )

        try:
            outline: ProjectOutlineV3 | None = None
            parent_step_id: uuid.UUID | None = None
            parent_prompt_id: uuid.UUID | None = None
            if project_input.input_mode is StoryInputMode.THEME_EXPAND:
                outline, parent_step_id, parent_prompt_id = self._generate_outline(
                    run_id=run_id,
                    target_date=target_date,
                    project_input=project_input,
                    controls=controls,
                    metadata=metadata,
                    series_profile=series_profile,
                    recent=recent,
                    seeds=seeds,
                    patterns=patterns,
                )
                if settings.planning_mode is PlanningMode.GUIDED_SEQUENTIAL:
                    return ProjectOutlinePause(run_id, outline)
            elif settings.planning_mode is PlanningMode.GUIDED_SEQUENTIAL:
                return ProjectPlanningResult(run_id, project_input)

            plan = self._complete_auto_plan(
                run_id=run_id,
                target_date=target_date,
                project_input=project_input,
                outline=outline,
                parent_step_id=parent_step_id,
                parent_prompt_id=parent_prompt_id,
                metadata=metadata,
            )
            return ProjectPlanningResult(run_id, project_input, outline, plan)
        except PlanningReviewRequired:
            self._repository.set_run_status(run_id, RunStatus.PLANNING_REVIEW)
            raise
        except Exception:
            self._repository.set_run_status(run_id, RunStatus.FAILED)
            raise

    def update_episode_source(
        self,
        run_id: uuid.UUID,
        *,
        slot: Slot,
        source_text: str,
    ) -> StoryProjectInput:
        """保存尚未规划时段的用户原文，不产生Director Step或付费意图。"""

        context = self._repository.get_planning_context(run_id)
        project_input = _project_input_from_context(context)
        if project_input.input_mode is not StoryInputMode.EPISODE_SCRIPTS:
            raise ValueError("只有已有剧本项目允许逐时段编辑原文")
        return self._repository.save_episode_source(
            run_id=run_id,
            slot=slot,
            source=source_text,
        )

    def suggest_connection(
        self,
        run_id: uuid.UUID,
        *,
        slot: Slot,
        allow_paid_generation: bool,
    ) -> ConnectionSuggestionResult:
        """基于已确认结果卡生成可编辑建议，不自动保存、启用或触发后续导演。"""

        self._check_paid(allow_paid_generation)
        if slot is Slot.MORNING:
            raise ValueError("上午没有前序时段，不需要剧情关联建议")
        context = self._repository.get_planning_context(run_id)
        project_input = _project_input_from_context(context)
        if any(item.plan.slot is slot for item in self._repository.list_episodes(run_id)):
            raise ValueError("目标时段已经完成镜头化；关联建议需在导演前生成")
        outcomes = _accepted_outcomes_from_context(context)
        previous_slots = tuple(
            previous for previous in Slot if previous.sort_order < slot.sort_order
        )
        if any(previous not in outcomes for previous in previous_slots):
            raise ValueError("只有前序视频和结果卡全部确认后，才能生成剧情关联建议")
        prompt = compile_connection_suggestion_prompt(
            project_theme=project_input.theme,
            target_slot=slot,
            previous_outcomes=tuple(
                _accepted_outcome_summary(previous, outcomes[previous])
                for previous in previous_slots
            ),
            target_source_text=project_input.episode_sources.for_slot(slot),
            scene_route=project_input.scene_route,
        )
        attempt = self._repository.next_director_attempt(
            run_id=run_id,
            phase="connection",
            slot=slot,
        )
        suggestion, step, _, _ = self._director_invoker.invoke(
            run_id=run_id,
            episode_id=None,
            parent_step_id=None,
            parent_prompt_id=None,
            phase="connection",
            slot=slot,
            attempt=attempt,
            prompt=prompt,
            contract=ConnectionSuggestion,
        )
        return ConnectionSuggestionResult(
            run_id=run_id,
            slot=slot,
            attempt=attempt,
            step_id=step.id,
            suggestion=suggestion,
        )

    def plan_slot(
        self,
        run_id: uuid.UUID,
        *,
        slot: Slot,
        allow_paid_generation: bool,
        generate_from_theme: bool = False,
    ) -> SlotPlanningResult:
        """只规划顺序模式中当前已解锁的一个时段。"""

        self._check_paid(allow_paid_generation)
        settings = self._repository.get_pipeline_settings(run_id)
        if settings.planning_mode is not PlanningMode.GUIDED_SEQUENTIAL:
            raise ValueError("auto_day模式请使用resume_planning补齐全天计划")
        stored_run = self._repository.get_run(run_id)
        if stored_run.status in {RunStatus.READY.value, RunStatus.DELIVERED.value}:
            raise ValueError(f"Run状态{stored_run.status}不允许继续规划")

        context = self._repository.get_planning_context(run_id)
        project_input = _project_input_from_context(context)
        outline = _project_outline_from_context(context)
        if project_input.input_mode is StoryInputMode.THEME_EXPAND:
            if outline is None:
                raise ValueError("主题扩写项目尚未生成ProjectOutlineV3")
            if "projectOutlineConfirmedAt" not in context:
                raise ValueError("请先确认项目大纲，再规划Morning")

        existing = self._repository.list_episodes(run_id)
        if any(item.plan.slot is slot for item in existing):
            raise ValueError(f"{slot.value}时段已经规划")
        expected = list(Slot)[len(existing)] if len(existing) < len(Slot) else None
        if expected is None or slot is not expected:
            label = "无" if expected is None else expected.value
            raise ValueError(f"顺序模式当前只能规划{label}时段")

        outcomes = _accepted_outcomes_from_context(context)
        previous_slots = tuple(
            previous for previous in Slot if previous.sort_order < slot.sort_order
        )
        missing = tuple(previous.value for previous in previous_slots if previous not in outcomes)
        if missing:
            raise ValueError("请先批准视频并确认结果卡：" + "、".join(missing))

        metadata = _planning_metadata(context)
        source = project_input.episode_sources.for_slot(slot)
        if (
            project_input.input_mode is StoryInputMode.EPISODE_SCRIPTS
            and source is None
            and not generate_from_theme
        ):
            raise ValueError(
                f"{slot.value}尚未填写原始剧本；请先输入剧本，或明确选择AI根据主题生成"
            )
        episode, _, attempt = self._generate_episode(
            run_id=run_id,
            project_input=project_input,
            outline_episode=(None if outline is None else outline.episodes.for_slot(slot)),
            slot=slot,
            story_connection=_story_connection_from_context(context, slot),
            parent_step_id=_optional_uuid(context.get("projectOutlineDirectorStepId")),
            parent_prompt_id=_optional_uuid(context.get("projectOutlineDirectorPromptId")),
            retry_reason=None,
            recent_summaries=_recent_summaries_from_metadata(metadata),
            story_pattern=_story_patterns_from_metadata(metadata).get(slot),
            series_profile=_series_profile_from_metadata(metadata, self._series_profile),
            controls=_controls_from_metadata(metadata),
            user_episode_text=source,
        )
        candidate = [*(item.plan for item in existing), episode]
        if len(candidate) == len(Slot):
            self._assemble_plan(
                run_id=run_id,
                target_date=stored_run.content_date,
                project_input=project_input,
                outline=outline,
                episodes=candidate,
                series_profile=_series_profile_from_metadata(metadata, self._series_profile),
            )
        self._repository.save_planned_episode(run_id=run_id, episode=episode)
        if source is None:
            self._remember_generated_slot(run_id, context, metadata, slot)
        return SlotPlanningResult(run_id, slot, attempt, episode)

    def resume_planning(
        self,
        run_id: uuid.UUID,
        *,
        allow_paid_generation: bool,
    ) -> ProjectPlanningResult:
        """只为auto_day补齐缺失时段，成功Step通过幂等键直接复用。"""

        self._check_paid(allow_paid_generation)
        settings = self._repository.get_pipeline_settings(run_id)
        if settings.planning_mode is PlanningMode.GUIDED_SEQUENTIAL:
            raise ValueError("顺序人工模式请显式规划当前已解锁时段")
        stored_run = self._repository.get_run(run_id)
        if stored_run.plan is not None:
            return ProjectPlanningResult(
                run_id,
                stored_run.plan.project_input,
                stored_run.plan.outline,
                stored_run.plan,
            )
        if stored_run.status not in {
            RunStatus.DRAFT.value,
            RunStatus.PLANNING_REVIEW.value,
        }:
            raise ValueError(
                f"Run状态{stored_run.status}不允许自动续跑；失败收费节点必须显式重试或对账"
            )

        context = self._repository.get_planning_context(run_id)
        project_input = _project_input_from_context(context)
        outline = _project_outline_from_context(context)
        if project_input.input_mode is StoryInputMode.THEME_EXPAND and outline is None:
            raise ValueError("主题扩写项目没有可复用的ProjectOutlineV3")
        if (
            project_input.input_mode is StoryInputMode.EPISODE_SCRIPTS
            and project_input.episode_sources.populated_slots != tuple(Slot)
        ):
            raise ValueError("auto_day已有剧本模式必须先补齐Morning、Noon、Evening原文")
        metadata = _planning_metadata(context)
        context_drafts = context.get("episodeDrafts")
        if isinstance(context_drafts, dict):
            metadata["episodeDrafts"] = context_drafts
        plan = self._complete_auto_plan(
            run_id=run_id,
            target_date=stored_run.content_date,
            project_input=project_input,
            outline=outline,
            parent_step_id=_optional_uuid(context.get("projectOutlineDirectorStepId")),
            parent_prompt_id=_optional_uuid(context.get("projectOutlineDirectorPromptId")),
            metadata=metadata,
        )
        return ProjectPlanningResult(run_id, project_input, outline, plan)

    def replan_episode(
        self,
        run_id: uuid.UUID,
        *,
        slot: Slot,
        reason: str,
        allow_paid_generation: bool,
        acknowledge_downstream_replacement: bool = False,
        generate_from_theme: bool = False,
    ) -> EpisodeReplanResult:
        """新导演成功前保留原Episode和已生成媒体，避免外部失败污染当前事实。"""

        self._check_paid(allow_paid_generation)
        if len(reason.strip()) < 4:
            raise ValueError("局部重规划必须填写具体原因")
        stored_run = self._repository.get_run(run_id)
        if stored_run.status == RunStatus.DELIVERED.value:
            raise ValueError("已交付项目不允许重新规划")
        existing = self._repository.list_episodes(run_id)
        current = next((item for item in existing if item.plan.slot is slot), None)
        settings = self._repository.get_pipeline_settings(run_id)
        context = self._repository.get_planning_context(run_id)
        raw_connections = context.get("storyConnections")
        loaded_later_connections = (
            tuple(
                item.plan.slot.value
                for item in existing
                if item.plan.slot.sort_order > slot.sort_order
                and isinstance(raw_connections, dict)
                and isinstance(raw_connections.get(item.plan.slot.value), dict)
                and bool(raw_connections[item.plan.slot.value].get("useForDirector"))
            )
            if settings.planning_mode is PlanningMode.GUIDED_SEQUENTIAL
            else ()
        )
        if loaded_later_connections and not acknowledge_downstream_replacement:
            raise ValueError(
                "后续时段已经显式加载剧情关联卡；请确认下游影响后再重新规划："
                + "、".join(loaded_later_connections)
            )
        if (
            current is not None
            and current.status not in {EpisodeStatus.PLANNED, EpisodeStatus.FAILED}
            and not acknowledge_downstream_replacement
        ):
            raise ValueError(
                "该时段已经进入媒体生产；必须显式确认下游结果会过期后才能重新规划"
            )

        project_input = _project_input_from_context(context)
        outline = _project_outline_from_context(context)
        metadata = _planning_metadata(context)
        source = project_input.episode_sources.for_slot(slot)
        generated_slots = _generated_slots_from_metadata(metadata)
        if (
            project_input.input_mode is StoryInputMode.EPISODE_SCRIPTS
            and source is None
            and not generate_from_theme
            and slot not in generated_slots
        ):
            raise ValueError(
                f"{slot.value}没有用户剧本；请先保存原文，或明确选择AI根据主题生成"
            )

        outcomes = _accepted_outcomes_from_context(context)
        previous_slots = tuple(
            previous for previous in Slot if previous.sort_order < slot.sort_order
        )
        if settings.planning_mode is PlanningMode.GUIDED_SEQUENTIAL:
            missing = tuple(
                previous.value for previous in previous_slots if previous not in outcomes
            )
            if missing:
                raise ValueError("请先确认前序实际结果卡：" + "、".join(missing))
        episode, _, attempt = self._generate_episode(
            run_id=run_id,
            project_input=project_input,
            outline_episode=(None if outline is None else outline.episodes.for_slot(slot)),
            slot=slot,
            story_connection=_story_connection_from_context(context, slot),
            parent_step_id=_optional_uuid(context.get("projectOutlineDirectorStepId")),
            parent_prompt_id=_optional_uuid(context.get("projectOutlineDirectorPromptId")),
            retry_reason=reason,
            recent_summaries=_recent_summaries_from_metadata(metadata),
            story_pattern=_story_patterns_from_metadata(metadata).get(slot),
            series_profile=_series_profile_from_metadata(metadata, self._series_profile),
            controls=_controls_from_metadata(metadata),
            user_episode_text=source,
        )
        if current is None and settings.planning_mode is PlanningMode.GUIDED_SEQUENTIAL:
            self._repository.save_planned_episode(run_id=run_id, episode=episode)
        elif current is None:
            drafts = _episode_drafts_from_context(context)
            drafts[slot] = episode.script
            metadata["episodeDrafts"] = {
                key.value: value.model_dump(mode="json") for key, value in drafts.items()
            }
            self._persist_drafts(
                run_id=run_id,
                project_input=project_input,
                outline=outline,
                parent_step_id=_optional_uuid(context.get("projectOutlineDirectorStepId")),
                parent_prompt_id=_optional_uuid(context.get("projectOutlineDirectorPromptId")),
                metadata=metadata,
                drafts=drafts,
            )
            if all(item in drafts for item in Slot):
                plan = self._assemble_plan(
                    run_id=run_id,
                    target_date=stored_run.content_date,
                    project_input=project_input,
                    outline=outline,
                    episodes=[EpisodePlan(slot=item, script=drafts[item]) for item in Slot],
                    series_profile=_series_profile_from_metadata(metadata, self._series_profile),
                )
                self._repository.finalize_plan(run_id=run_id, plan=plan)
            elif stored_run.status == RunStatus.PLANNING_REVIEW.value:
                self._repository.set_run_status(run_id, RunStatus.DRAFT)
        else:
            self._repository.replace_episode_plan(
                run_id=run_id,
                episode=episode,
                project_outline=outline,
                acknowledge_downstream_replacement=acknowledge_downstream_replacement,
            )
        if source is None:
            self._remember_generated_slot(run_id, context, metadata, slot)
        if stored_run.status == RunStatus.PLANNING_REVIEW.value:
            self._repository.set_run_status(run_id, RunStatus.PLANNED)
        return EpisodeReplanResult(run_id, slot, attempt, episode)

    def regenerate_project_outline(
        self,
        step_id: uuid.UUID,
        *,
        reason: str,
        prompt_override: str | None,
        allow_paid_generation: bool,
    ) -> dict[str, Any]:
        """显式重做契约拒绝的ProjectOutline；未知提交绝不产生第二次POST。"""

        self._check_paid(allow_paid_generation)
        if len(reason.strip()) < 4:
            raise ValueError("项目大纲重执行必须填写具体原因")
        previous = self._repository.get_step(step_id)
        if (
            previous.episode_id is not None
            or previous.operation_key != "director:day"
            or previous.kind.value != "director"
        ):
            raise ValueError("只有项目总导演节点可使用该重执行入口")
        if previous.status is StepStatus.SUBMISSION_UNKNOWN:
            raise ValueError("submission_unknown必须先对账，不能创建新attempt")
        if previous.status is not StepStatus.FAILED:
            raise ValueError("项目大纲重执行只接受failed步骤")
        candidate = previous.input_snapshot.get("provider_output")
        if not isinstance(candidate, dict):
            raise ValueError("失败总导演步骤缺少可审计的Provider原始输出")

        original = self._repository.get_prompt_for_step(
            step_id,
            purpose=PromptPurpose.DIRECTOR,
        )
        prompt = compile_day_repair_prompt(
            original_prompt=prompt_override or original.text,
            rejected_candidate=candidate,
            validation_error=previous.error_message or reason,
        )
        attempt = self._repository.next_director_attempt(
            run_id=previous.run_id,
            phase="project_outline",
            slot=None,
        )
        try:
            outline, step, prompt_id, _ = self._director_invoker.invoke(
                run_id=previous.run_id,
                episode_id=None,
                parent_step_id=previous.id,
                parent_prompt_id=original.id,
                phase="project_outline",
                slot=None,
                attempt=attempt,
                prompt=prompt,
                contract=ProjectOutlineV3,
                repair_of_step_id=previous.id,
            )
        except DirectorCandidateRejected as exc:
            raise PlanningReviewRequired(
                run_id=previous.run_id,
                errors=exc.errors,
            ) from exc
        context = self._repository.get_planning_context(previous.run_id)
        project_input = _project_input_from_context(context)
        stored_run = self._repository.get_run(previous.run_id)
        try:
            _validate_outline(outline, stored_run.content_date, project_input)
        except ValueError as exc:
            raise PlanningReviewRequired(
                run_id=previous.run_id,
                errors=(str(exc),),
            ) from exc
        self._repository.save_planning_context(
            run_id=previous.run_id,
            project_input=project_input,
            project_outline=outline,
            project_outline_step_id=step.id,
            project_outline_prompt_id=prompt_id,
            episode_drafts={},
            planning_metadata=_planning_metadata(context),
        )
        if stored_run.status == RunStatus.FAILED.value:
            self._repository.set_run_status(previous.run_id, RunStatus.PLANNING_REVIEW)
        return {
            "runId": str(previous.run_id),
            "stepId": str(step.id),
            "attempt": attempt,
            "status": step.status.value,
        }

    def _generate_outline(
        self,
        *,
        run_id: uuid.UUID,
        target_date: date,
        project_input: StoryProjectInput,
        controls: RunCreativeControls,
        metadata: dict[str, Any],
        series_profile: SeriesVisualProfile,
        recent: tuple[RecentContentSummary, ...],
        seeds: tuple[Any, ...],
        patterns: dict[Slot, StoryPattern],
    ) -> tuple[ProjectOutlineV3, uuid.UUID, uuid.UUID]:
        prompt = compile_day_director_prompt(
            target_date=target_date,
            project_input=project_input,
            recent_summaries=recent,
            event_seeds=tuple(
                f"[{item.seed_id}|{','.join(slot.value for slot in item.slots)}]{item.direction}"
                for item in seeds
            ),
            story_patterns=patterns,
            creative_controls=controls,
            series_profile=series_profile,
            style_profile=self._style_profile,
        )
        attempt = self._repository.next_director_attempt(
            run_id=run_id,
            phase="project_outline",
            slot=None,
        )
        try:
            outline, step, prompt_id, _ = self._director_invoker.invoke(
                run_id=run_id,
                episode_id=None,
                parent_step_id=None,
                parent_prompt_id=None,
                phase="project_outline",
                slot=None,
                attempt=attempt,
                prompt=prompt,
                contract=ProjectOutlineV3,
            )
        except DirectorCandidateRejected as exc:
            raise PlanningReviewRequired(run_id=run_id, errors=exc.errors) from exc
        try:
            _validate_outline(outline, target_date, project_input)
        except ValueError as exc:
            raise PlanningReviewRequired(run_id=run_id, errors=(str(exc),)) from exc
        self._repository.save_planning_context(
            run_id=run_id,
            project_input=project_input,
            project_outline=outline,
            project_outline_step_id=step.id,
            project_outline_prompt_id=prompt_id,
            episode_drafts={},
            planning_metadata=metadata,
        )
        return outline, step.id, prompt_id

    def _complete_auto_plan(
        self,
        *,
        run_id: uuid.UUID,
        target_date: date,
        project_input: StoryProjectInput,
        outline: ProjectOutlineV3 | None,
        parent_step_id: uuid.UUID | None,
        parent_prompt_id: uuid.UUID | None,
        metadata: dict[str, Any],
    ) -> DailyProductionPlan:
        """顺序补齐auto_day草稿；前序预测Episode不会作为后续导演事实。"""

        series_profile = _series_profile_from_metadata(metadata, self._series_profile)
        drafts = _episode_drafts_from_metadata(metadata)
        patterns = _story_patterns_from_metadata(metadata)
        episodes: list[EpisodePlan] = []
        for slot in Slot:
            saved = drafts.get(slot)
            if saved is not None:
                episode = EpisodePlan(slot=slot, script=saved)
            else:
                episode, _, _ = self._generate_episode(
                    run_id=run_id,
                    project_input=project_input,
                    outline_episode=(None if outline is None else outline.episodes.for_slot(slot)),
                    slot=slot,
                    # auto_day尚无人工确认结果卡，禁止把前一个候选结尾当作事实。
                    story_connection=None,
                    parent_step_id=parent_step_id,
                    parent_prompt_id=parent_prompt_id,
                    retry_reason=None,
                    recent_summaries=_recent_summaries_from_metadata(metadata),
                    story_pattern=patterns.get(slot),
                    series_profile=series_profile,
                    controls=_controls_from_metadata(metadata),
                    user_episode_text=project_input.episode_sources.for_slot(slot),
                )
                drafts[slot] = episode.script
                metadata["episodeDrafts"] = {
                    key.value: value.model_dump(mode="json") for key, value in drafts.items()
                }
                self._persist_drafts(
                    run_id=run_id,
                    project_input=project_input,
                    outline=outline,
                    parent_step_id=parent_step_id,
                    parent_prompt_id=parent_prompt_id,
                    metadata=metadata,
                    drafts=drafts,
                )
            episodes.append(episode)

        plan = self._assemble_plan(
            run_id=run_id,
            target_date=target_date,
            project_input=project_input,
            outline=outline,
            episodes=episodes,
            series_profile=series_profile,
        )
        self._repository.finalize_plan(run_id=run_id, plan=plan)
        return plan

    def _generate_episode(
        self,
        *,
        run_id: uuid.UUID,
        project_input: StoryProjectInput,
        outline_episode: OutlineEpisode | None,
        slot: Slot,
        story_connection: StoryConnection | None,
        parent_step_id: uuid.UUID | None,
        parent_prompt_id: uuid.UUID | None,
        retry_reason: str | None,
        recent_summaries: tuple[RecentContentSummary, ...],
        story_pattern: StoryPattern | None,
        series_profile: SeriesVisualProfile,
        controls: RunCreativeControls,
        user_episode_text: str | None,
    ) -> tuple[EpisodePlan, StoredStep, int]:
        focus = _resolve_activity_focus(controls, slot)
        duration = _resolve_duration_band(controls, slot)
        if user_episode_text is not None:
            prompt = compile_episode_adaptation_prompt(
                user_episode_text=user_episode_text,
                project_theme=project_input.theme,
                scene_route=project_input.scene_route,
                slot=slot,
                activity_focus=focus,
                duration_band=duration,
                story_connection=story_connection,
                retry_reason=retry_reason,
                series_profile=series_profile,
                style_profile=self._style_profile,
            )
        else:
            # episode_scripts缺少原文时只有显式generate_from_theme入口会到达这里；
            # outline=None是诚实表达，不伪造一份总导演边界。
            prompt = compile_episode_director_prompt(
                project_theme=project_input.theme,
                scene_route=project_input.scene_route,
                slot=slot,
                outline_episode=outline_episode,
                activity_focus=focus,
                duration_band=duration,
                story_connection=story_connection,
                retry_reason=retry_reason,
                story_pattern=story_pattern,
                series_profile=series_profile,
                style_profile=self._style_profile,
            )
        attempt = self._repository.next_director_attempt(
            run_id=run_id,
            phase="episode",
            slot=slot,
        )
        try:
            draft, step, _, normalizations = self._director_invoker.invoke(
                run_id=run_id,
                episode_id=None,
                parent_step_id=parent_step_id,
                parent_prompt_id=parent_prompt_id,
                phase="episode",
                slot=slot,
                attempt=attempt,
                prompt=prompt,
                contract=EpisodeScript,
            )
        except DirectorCandidateRejected as exc:
            self._mark_planning_review_when_possible(run_id)
            raise PlanningReviewRequired(run_id=run_id, slot=slot, errors=exc.errors) from exc

        episode = EpisodePlan(slot=slot, script=draft)
        gate_issues = validate_episode_gate(episode, series_profile=series_profile)
        failures = list(hard_failures(gate_issues))
        if draft.activity_focus is not focus:
            failures.append(
                _control_failure(f"导演改写了{slot.value}固定活动焦点{focus.value}")
            )
        low, high = duration.range
        if not low <= draft.duration_seconds <= high:
            failures.append(
                _control_failure(
                    f"导演输出{draft.duration_seconds}秒，不在{duration.value}档{low}至{high}秒范围"
                )
            )
        warnings = [
            *(item for item in gate_issues if item not in failures),
            *validate_episode_cooldown(episode, recent_summaries),
            *(
                _normalization_warning(item)
                for item in normalizations
            ),
        ]
        if failures:
            messages = tuple(item.message for item in failures)
            self._repository.record_review(
                step_id=step.id,
                asset_id=None,
                source="technical",
                decision="rejected",
                reason="；".join(messages),
                warnings=[
                    {"code": item.code, "message": item.message} for item in warnings
                ],
                evidence={
                    "phase": "episode_contract",
                    "providerStatus": "succeeded",
                    "contractStatus": "parsed",
                    "semanticReviewStatus": "rejected",
                    "autoRepairScheduled": False,
                },
            )
            self._mark_planning_review_when_possible(run_id)
            raise PlanningReviewRequired(run_id=run_id, slot=slot, errors=messages)
        if warnings:
            self._repository.record_review(
                step_id=step.id,
                asset_id=None,
                source="technical",
                decision="pending",
                reason="导演候选可用于生产；以下内容仅供人工检查，不自动打回",
                warnings=[
                    {"code": item.code, "message": item.message} for item in warnings
                ],
                evidence={"phase": "episode_advisory", "blocking": False},
            )
        return episode, step, attempt

    def _assemble_plan(
        self,
        *,
        run_id: uuid.UUID,
        target_date: date,
        project_input: StoryProjectInput,
        outline: ProjectOutlineV3 | None,
        episodes: list[EpisodePlan],
        series_profile: SeriesVisualProfile,
    ) -> DailyProductionPlan:
        plan = DailyProductionPlan(
            content_date=target_date,
            project_input=project_input,
            outline=outline,
            episodes=episodes,
        )
        failures = hard_failures(
            validate_plan_gate(
                plan,
                expected_date=target_date,
                series_profile=series_profile,
            )
        )
        if failures:
            raise PlanningReviewRequired(
                run_id=run_id,
                slot=Slot.EVENING,
                errors=tuple(item.message for item in failures),
            )
        return plan

    def _persist_drafts(
        self,
        *,
        run_id: uuid.UUID,
        project_input: StoryProjectInput,
        outline: ProjectOutlineV3 | None,
        parent_step_id: uuid.UUID | None,
        parent_prompt_id: uuid.UUID | None,
        metadata: dict[str, Any],
        drafts: dict[Slot, EpisodeScript],
    ) -> None:
        serialized = {
            slot.value: script.model_dump(mode="json") for slot, script in drafts.items()
        }
        persisted_metadata = dict(metadata)
        persisted_metadata.pop("episodeDrafts", None)
        if outline is not None:
            if parent_step_id is None or parent_prompt_id is None:
                raise RuntimeError("主题扩写项目缺少总导演Step或Prompt引用")
            self._repository.save_planning_context(
                run_id=run_id,
                project_input=project_input,
                project_outline=outline,
                project_outline_step_id=parent_step_id,
                project_outline_prompt_id=parent_prompt_id,
                episode_drafts=serialized,
                planning_metadata=persisted_metadata,
            )
            return
        self._repository.save_initial_planning_metadata(
            run_id=run_id,
            planning_metadata=persisted_metadata,
            project_input=project_input,
            episode_drafts=serialized,
        )

    def _remember_generated_slot(
        self,
        run_id: uuid.UUID,
        context: dict[str, Any],
        metadata: dict[str, Any],
        slot: Slot,
    ) -> None:
        slots = _generated_slots_from_metadata(metadata)
        slots.add(slot)
        metadata["generatedFromThemeSlots"] = [item.value for item in Slot if item in slots]
        project_input = _project_input_from_context(context)
        outline = _project_outline_from_context(context)
        drafts = {
            item.plan.slot: item.plan.script for item in self._repository.list_episodes(run_id)
        }
        self._persist_drafts(
            run_id=run_id,
            project_input=project_input,
            outline=outline,
            parent_step_id=_optional_uuid(context.get("projectOutlineDirectorStepId")),
            parent_prompt_id=_optional_uuid(context.get("projectOutlineDirectorPromptId")),
            metadata=metadata,
            drafts=drafts,
        )

    def _mark_planning_review_when_possible(self, run_id: uuid.UUID) -> None:
        """只在状态机允许时把确定性导演拒绝投影为Run级人工审核。"""

        status = RunStatus(self._repository.get_run(run_id).status)
        if status in {
            RunStatus.DRAFT,
            RunStatus.PLANNED,
            RunStatus.GENERATING,
            RunStatus.REVIEWING,
            RunStatus.FAILED,
        }:
            self._repository.set_run_status(run_id, RunStatus.PLANNING_REVIEW)

    @staticmethod
    def _check_paid(allow_paid_generation: bool) -> None:
        if not allow_paid_generation:
            raise ValueError("导演调用需要显式确认allowPaidGeneration")


def _project_input_from_context(context: dict[str, Any]) -> StoryProjectInput:
    value = context.get("projectInput")
    if not isinstance(value, dict):
        raise ValueError("该Run缺少StoryProjectInput，不能继续V3生产")
    return StoryProjectInput.model_validate(value)


def _project_outline_from_context(context: dict[str, Any]) -> ProjectOutlineV3 | None:
    value = context.get("projectOutline")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("projectOutline不是合法对象")
    return ProjectOutlineV3.model_validate(value)


def _planning_metadata(context: dict[str, Any]) -> dict[str, Any]:
    value = context.get("planningMetadata")
    return dict(value) if isinstance(value, dict) else {}


def _recent_summaries_from_metadata(
    metadata: dict[str, Any],
) -> tuple[RecentContentSummary, ...]:
    values = metadata.get("recentSummaries")
    if not isinstance(values, list):
        return ()
    return tuple(
        RecentContentSummary.model_validate(value)
        for value in values
        if isinstance(value, dict)
    )


def _series_profile_from_metadata(
    metadata: dict[str, Any],
    fallback: SeriesVisualProfile,
) -> SeriesVisualProfile:
    value = metadata.get("seriesProfile")
    return SeriesVisualProfile.model_validate(value) if isinstance(value, dict) else fallback


def _story_patterns_from_metadata(
    metadata: dict[str, Any],
) -> dict[Slot, StoryPattern]:
    values = metadata.get("storyPatterns")
    if not isinstance(values, dict):
        return {}
    result: dict[Slot, StoryPattern] = {}
    for raw_slot, value in values.items():
        if isinstance(value, dict):
            result[Slot(raw_slot)] = StoryPattern.model_validate(value)
    return result


def _controls_from_metadata(metadata: dict[str, Any]) -> RunCreativeControls:
    value = metadata.get("creativeControls")
    return (
        RunCreativeControls.model_validate(value)
        if isinstance(value, dict)
        else RunCreativeControls()
    )


def _episode_drafts_from_metadata(
    metadata: dict[str, Any],
) -> dict[Slot, EpisodeScript]:
    values = metadata.get("episodeDrafts")
    if not isinstance(values, dict):
        return {}
    result: dict[Slot, EpisodeScript] = {}
    for raw_slot, value in values.items():
        if isinstance(value, dict):
            result[Slot(raw_slot)] = EpisodeScript.model_validate(value)
    return result


def _episode_drafts_from_context(
    context: dict[str, Any],
) -> dict[Slot, EpisodeScript]:
    values = context.get("episodeDrafts")
    if not isinstance(values, dict):
        return _episode_drafts_from_metadata(_planning_metadata(context))
    return {
        Slot(raw_slot): EpisodeScript.model_validate(value)
        for raw_slot, value in values.items()
        if isinstance(value, dict)
    }


def _accepted_outcomes_from_context(
    context: dict[str, Any],
) -> dict[Slot, AcceptedOutcome]:
    values = context.get("acceptedOutcomes")
    if not isinstance(values, dict):
        return {}
    return {
        Slot(raw_slot): AcceptedOutcome.model_validate(value)
        for raw_slot, value in values.items()
        if isinstance(value, dict)
    }


def _story_connection_from_context(
    context: dict[str, Any],
    slot: Slot,
) -> StoryConnection | None:
    if slot is Slot.MORNING:
        return None
    values = context.get("storyConnections")
    if not isinstance(values, dict):
        return None
    raw = values.get(slot.value)
    if not isinstance(raw, dict):
        return None
    connection = StoryConnection.model_validate(raw)
    return connection if connection.use_for_director else None


def _accepted_outcome_summary(slot: Slot, outcome: AcceptedOutcome) -> str:
    carry = "、".join(outcome.carry_forward) or "无必须延续对象"
    excluded = "、".join(outcome.do_not_carry_forward) or "无"
    return (
        f"{slot.value}已由用户观看并确认：{outcome.summary}；"
        f"下一时段必须延续：{carry}；不得继承偶发生成内容：{excluded}。"
    )


def _resolve_activity_focus(controls: RunCreativeControls, slot: Slot) -> ActivityFocus:
    requested = controls.requested_focus(slot)
    if requested is ActivityFocusMode.ADAPTIVE:
        return ActivityFocus.CAT_LEAD
    if requested is ActivityFocusMode.INHERIT:
        raise ValueError("继承后的时段活动焦点不能仍为inherit")
    return ActivityFocus(requested.value)


def _resolve_duration_band(controls: RunCreativeControls, slot: Slot) -> DurationBand:
    selected = next(item for item in controls.slot_controls if item.slot is slot)
    if selected.duration_mode is not DurationMode.ADAPTIVE:
        return DurationBand(selected.duration_mode.value)
    # 自适应只解析生产容量，不替导演编写动作。中午承载全天主要推进，其余时段保持短片。
    return DurationBand.MEDIUM if slot is Slot.NOON else DurationBand.SHORT


def _generated_slots_from_metadata(metadata: dict[str, Any]) -> set[Slot]:
    values = metadata.get("generatedFromThemeSlots")
    if not isinstance(values, list):
        return set()
    return {
        Slot(value)
        for value in values
        if isinstance(value, str) and value in {slot.value for slot in Slot}
    }


def _optional_uuid(value: object) -> uuid.UUID | None:
    if value is None:
        return None
    return uuid.UUID(str(value))


def _validate_outline(
    outline: ProjectOutlineV3,
    target_date: date,
    project_input: StoryProjectInput,
) -> None:
    if outline.content_date != target_date:
        raise ValueError("ProjectOutlineV3内容日期与项目日期不一致")
    if outline.theme != project_input.theme:
        raise ValueError("ProjectOutlineV3改写了用户主题")


def _control_failure(message: str) -> GateIssue:
    return GateIssue(
        gate="planning",
        code="creative_control_mismatch",
        message=message,
        level=IssueLevel.HARD,
    )


def _normalization_warning(message: str) -> GateIssue:
    return GateIssue(
        gate="planning",
        code="normalized",
        message=message,
        level=IssueLevel.WARNING,
    )
