"""总导演与三个时段导演的顺序规划用例。

auto_day一次产生DayBrief、morning、noon、evening四个独立Ark调用；
guided_sequential先生成DayBrief，后续只在前一时段成片结果被人工确认后调用当前导演。
每个Prompt和结构化输出都先落入工作流，单个时段失败不会要求重做全天方向。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ..domain.contracts import (
    AcceptedOutcome,
    ActivityFocusMode,
    DailyProductionPlan,
    DayBrief,
    DurationMode,
    EpisodePlan,
    EpisodeScript,
    RecentContentSummary,
    RunCreativeControls,
    Slot,
    SlotBrief,
)
from ..domain.pipeline import PipelineSettings, PlanningMode
from ..domain.prompts import (
    PromptCompilationError,
    compile_day_director_prompt,
    compile_day_structuring_prompt,
    compile_episode_adaptation_prompt,
    compile_episode_director_prompt,
    compile_video_prompt_preview,
    summarize_episode_state,
)
from ..domain.rules import (
    hard_failures,
    validate_episode_against_brief,
    validate_episode_cooldown,
    validate_plan_gate,
)
from ..domain.story_patterns import StoryPattern
from ..domain.user_story import UserStory, parse_user_story
from ..domain.visual_profiles import (
    CreativeProfileOverride,
    SeriesVisualProfile,
    StyleProfile,
)
from ..domain.workflow import EpisodeStatus, RunStatus
from .director_execution import DirectorCandidateRejected, DirectorInvoker
from .event_seeds import EventSeedCatalog
from .ports import DirectorGateway, PlanningStore, StoredStep


@dataclass(frozen=True, slots=True)
class PlanningResult:
    """一次全天规划的可展示结果。"""

    run_id: uuid.UUID
    selected_candidate: int
    candidate_count: int
    plan: DailyProductionPlan


@dataclass(frozen=True, slots=True)
class EpisodeReplanResult:
    """单个时段局部重规划结果。"""

    run_id: uuid.UUID
    slot: Slot
    attempt: int
    episode: EpisodePlan


@dataclass(frozen=True, slots=True)
class SlotPlanningResult:
    """顺序人工模式一次只落库一个已解锁时段。"""

    run_id: uuid.UUID
    slot: Slot
    attempt: int
    episode: EpisodePlan


@dataclass(frozen=True, slots=True)
class DayBriefPause:
    """dayBrief阶段为manual时的断点结果：DayBrief已落库，Run保持draft。"""

    run_id: uuid.UUID
    day_brief: DayBrief


class PlanningReviewRequired(RuntimeError):
    """时段脚本的业务语义不合格，需要人工决定是否重新规划。"""

    def __init__(
        self,
        *,
        run_id: uuid.UUID,
        slot: Slot,
        errors: tuple[str, ...],
    ) -> None:
        super().__init__(f"Run {run_id} 的 {slot.value} 时段需要人工规划审核：" + "；".join(errors))
        self.run_id = run_id
        self.slot = slot
        self.errors = errors


class PlanningService:
    """总方向、时段细化、确定性硬门和局部重规划。"""

    def __init__(
        self,
        *,
        repository: PlanningStore,
        director: DirectorGateway,
        provider_name: str,
        series_profile: SeriesVisualProfile,
        style_profile: StyleProfile,
        event_seed_catalog: EventSeedCatalog | None = None,
        video_resolution: str = "720p",
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
        self._video_resolution = video_resolution

    def plan_day(
        self,
        *,
        target_date: date,
        planning_context: str,
        candidate_count: int,
        allow_paid_generation: bool,
        creative_profile: CreativeProfileOverride | None = None,
        stop_after_day_brief: bool = False,
        pipeline_settings: PipelineSettings | None = None,
        story_mode: str = "auto",
        creative_controls: RunCreativeControls | None = None,
    ) -> PlanningResult | DayBriefPause:
        """创建Run并按规划方式生成DayBrief或完整全天计划。

        ``stop_after_day_brief`` 用于创作台dayBrief阶段的手动开关：
        DayBrief落库后直接断点返回（Run保持draft），人工编辑后由
        ``resume_planning`` 续跑；流水线开关随本次提交一次性持久化。
        ``story_mode`` 为hybrid扩写开关：auto自动识别"主题+剧本1/2/3"
        完整剧情输入并切换为结构化改编（情节以用户原文为准），create强制
        创作，expand要求必须识别到完整剧情。
        """

        self._check_paid(allow_paid_generation)
        if candidate_count != 1:
            raise ValueError("分层导演模式固定生成一份DayBrief；DAILY_PLAN_CANDIDATE_COUNT必须为1")
        if story_mode not in {"auto", "create", "expand"}:
            raise ValueError("story_mode只支持auto/create/expand")
        user_story = None if story_mode == "create" else parse_user_story(planning_context)
        if story_mode == "expand" and user_story is None:
            raise ValueError("扩写模式未识别到“剧本1/剧本2/剧本3”三个段落")

        controls = creative_controls or RunCreativeControls()
        series_profile = (creative_profile or CreativeProfileOverride()).apply_to(
            self._series_profile
        )
        recent_summaries = self._repository.list_recent_completed_summaries(limit=6)
        event_seeds = self._event_seed_catalog.select(
            series_profile_hash=series_profile.fingerprint(),
            content_date=target_date,
            planning_revision=1,
            planning_context=planning_context,
        )
        story_patterns = self._event_seed_catalog.select_patterns(
            series_profile_hash=series_profile.fingerprint(),
            content_date=target_date,
            planning_revision=1,
            recent_pattern_ids=(),
        )
        planning_metadata = {
            "planningRevision": 1,
            "seriesProfileHash": series_profile.fingerprint(),
            "seriesProfile": series_profile.model_dump(mode="json"),
            "recentSummaries": [item.model_dump(mode="json") for item in recent_summaries],
            "eventSeeds": [item.model_dump(mode="json") for item in event_seeds],
            "storyPatterns": {
                slot.value: pattern.model_dump(mode="json")
                for slot, pattern in story_patterns.items()
            },
            # 扩写模式的用户原文随元数据冻结，resume/replan继续走改编路径。
            "userStory": (user_story.model_dump(mode="json") if user_story is not None else None),
            "creativeControls": controls.model_dump(mode="json"),
        }
        run_id = self._repository.create_draft_run(target_date)
        settings = pipeline_settings or PipelineSettings(
            allow_paid_generation=allow_paid_generation
        )
        self._repository.save_pipeline_settings(
            run_id=run_id,
            settings=settings,
        )
        try:
            if user_story is not None:
                day_prompt = compile_day_structuring_prompt(
                    user_story=user_story,
                    target_date=target_date,
                    series_profile=series_profile,
                    style_profile=self._style_profile,
                    creative_controls=controls,
                )
            else:
                day_prompt = compile_day_director_prompt(
                    target_date=target_date,
                    planning_context=planning_context,
                    recent_summaries=recent_summaries,
                    event_seeds=tuple(
                        f"[{item.seed_id}|{','.join(slot.value for slot in item.slots)}]"
                        f"{item.direction}"
                        for item in event_seeds
                    ),
                    story_patterns=story_patterns,
                    series_profile=series_profile,
                    style_profile=self._style_profile,
                    creative_controls=controls,
                )
            day_brief, day_step, day_prompt_id, _ = self._director_invoker.invoke(
                run_id=run_id,
                episode_id=None,
                parent_step_id=None,
                parent_prompt_id=None,
                phase="day",
                slot=None,
                attempt=1,
                prompt=day_prompt,
                contract=DayBrief,
            )
            if day_brief.content_date != target_date:
                raise RuntimeError("DayBrief内容日期与目标日期不一致")
            _validate_day_brief_controls(day_brief, controls)

            drafts: dict[str, dict[str, Any]] = {}
            self._save_context(
                run_id,
                day_brief,
                day_step.id,
                day_prompt_id,
                drafts,
                planning_metadata,
            )
            if (
                settings.planning_mode is PlanningMode.GUIDED_SEQUENTIAL
                or stop_after_day_brief
            ):
                return DayBriefPause(run_id=run_id, day_brief=day_brief)
            return self._complete_plan(
                run_id=run_id,
                day_brief=day_brief,
                day_step_id=day_step.id,
                day_prompt_id=day_prompt_id,
                drafts=drafts,
                planning_metadata=planning_metadata,
                recent_summaries=recent_summaries,
            )
        except PlanningReviewRequired:
            self._repository.set_run_status(run_id, RunStatus.PLANNING_REVIEW)
            raise
        except Exception:
            # 初始规划任一阶段失败都必须结束draft，避免它被误认为可继续生成的候选。
            # Step仍保留精确失败或submission_unknown状态，后续不得盲目重复POST。
            self._repository.set_run_status(run_id, RunStatus.FAILED)
            raise

    def resume_planning(
        self,
        run_id: uuid.UUID,
        *,
        allow_paid_generation: bool,
    ) -> PlanningResult:
        """复用已成功DayBrief，只补齐失败或尚未生成的时段导演。"""

        self._check_paid(allow_paid_generation)
        settings = self._repository.get_pipeline_settings(run_id)
        if settings.planning_mode is PlanningMode.GUIDED_SEQUENTIAL:
            raise ValueError("顺序人工模式请从Web显式规划当前已解锁时段")
        stored_run = self._repository.get_run(run_id)
        if stored_run.plan is not None:
            return PlanningResult(run_id, 1, 1, stored_run.plan)
        if stored_run.status not in {RunStatus.DRAFT.value, RunStatus.FAILED.value}:
            raise ValueError(f"Run状态{stored_run.status}不允许恢复初始规划")
        context = self._repository.get_planning_context(run_id)
        if "dayBrief" not in context:
            raise ValueError("没有已成功DayBrief，不能恢复规划")
        day_brief = DayBrief.model_validate(context["dayBrief"])
        drafts = dict(context.get("episodeDrafts", {}))
        metadata = dict(context.get("planningMetadata", {}))
        try:
            return self._complete_plan(
                run_id=run_id,
                day_brief=day_brief,
                day_step_id=uuid.UUID(context["dayDirectorStepId"]),
                day_prompt_id=uuid.UUID(context["dayDirectorPromptId"]),
                drafts=drafts,
                planning_metadata=metadata,
                recent_summaries=_parse_recent_summaries(metadata.get("recentSummaries", ())),
            )
        except PlanningReviewRequired:
            self._repository.set_run_status(run_id, RunStatus.PLANNING_REVIEW)
            raise
        except Exception:
            self._repository.set_run_status(run_id, RunStatus.FAILED)
            raise

    def plan_slot(
        self,
        run_id: uuid.UUID,
        *,
        slot: Slot,
        allow_paid_generation: bool,
    ) -> SlotPlanningResult:
        """在顺序人工模式中只规划一个已解锁时段。"""

        self._check_paid(allow_paid_generation)
        settings = self._repository.get_pipeline_settings(run_id)
        if settings.planning_mode is not PlanningMode.GUIDED_SEQUENTIAL:
            raise ValueError("auto_day模式由全天规划入口一次生成三个时段")
        stored_run = self._repository.get_run(run_id)
        if stored_run.status in {RunStatus.READY.value, RunStatus.DELIVERED.value}:
            raise ValueError(f"Run状态{stored_run.status}不允许继续规划")
        context = self._repository.get_planning_context(run_id)
        if "dayBrief" not in context:
            raise ValueError("请先完成并确认全天总导演")
        if "dayBriefConfirmedAt" not in context:
            raise ValueError("请先在Web保存并确认全天总导演边界")
        day_brief = DayBrief.model_validate(context["dayBrief"])
        existing = self._repository.list_episodes(run_id)
        if any(item.plan.slot is slot for item in existing):
            raise ValueError(f"{slot.value}时段已经规划")
        expected = list(Slot)[len(existing)] if len(existing) < 3 else None
        if expected is None or slot is not expected:
            expected_label = "无" if expected is None else expected.value
            raise ValueError(f"顺序模式当前只能规划{expected_label}时段")

        outcomes = _parse_accepted_outcomes(context.get("acceptedOutcomes", {}))
        previous_slots = tuple(
            item for item in Slot if item.sort_order < slot.sort_order
        )
        missing = tuple(item.value for item in previous_slots if item not in outcomes)
        if missing:
            raise ValueError("请先批准视频并确认结果卡：" + "、".join(missing))
        history = tuple(
            _accepted_outcome_summary(item, outcomes[item]) for item in previous_slots
        )
        slot_brief = next(item for item in day_brief.slot_briefs if item.slot is slot)
        metadata = dict(context.get("planningMetadata", {}))
        series_profile = _series_profile_from_metadata(metadata, self._series_profile)
        episode, _, attempt = self._generate_episode(
            run_id=run_id,
            day_brief=day_brief,
            slot_brief=slot_brief,
            previous=tuple(item.plan for item in existing),
            previous_state_summaries=history,
            parent_step_id=uuid.UUID(context["dayDirectorStepId"]),
            parent_prompt_id=uuid.UUID(context["dayDirectorPromptId"]),
            retry_reason=None,
            recent_summaries=_parse_recent_summaries(
                metadata.get("recentSummaries", ())
            ),
            story_pattern=_story_patterns_from_metadata(metadata).get(slot),
            series_profile=series_profile,
            user_episode_text=_user_episode_text(
                _user_story_from_metadata(metadata),
                slot,
            ),
        )
        candidate = [*(item.plan for item in existing), episode]
        if len(candidate) == 3:
            self._assemble_plan(
                run_id,
                day_brief,
                candidate,
                series_profile=series_profile,
            )
        self._repository.save_planned_episode(run_id=run_id, episode=episode)
        return SlotPlanningResult(run_id, slot, attempt, episode)

    def _complete_plan(
        self,
        *,
        run_id: uuid.UUID,
        day_brief: DayBrief,
        day_step_id: uuid.UUID,
        day_prompt_id: uuid.UUID,
        drafts: dict[str, dict[str, Any]],
        planning_metadata: dict[str, Any],
        recent_summaries: tuple[RecentContentSummary, ...],
    ) -> PlanningResult:
        """顺序补齐三个Episode并原子形成可生成的全天方案。"""

        series_profile = _series_profile_from_metadata(
            planning_metadata,
            self._series_profile,
        )
        story_patterns = _story_patterns_from_metadata(planning_metadata)
        user_story = _user_story_from_metadata(planning_metadata)
        episodes: list[EpisodePlan] = []
        for slot_brief in day_brief.slot_briefs:
            saved = drafts.get(slot_brief.slot.value)
            if saved is not None:
                episode = EpisodePlan(
                    slot=slot_brief.slot,
                    script=EpisodeScript.model_validate(saved),
                )
            else:
                episode, _, _ = self._generate_episode(
                    run_id=run_id,
                    day_brief=day_brief,
                    slot_brief=slot_brief,
                    previous=tuple(episodes),
                    parent_step_id=day_step_id,
                    parent_prompt_id=day_prompt_id,
                    retry_reason=None,
                    recent_summaries=recent_summaries,
                    story_pattern=story_patterns.get(slot_brief.slot),
                    series_profile=series_profile,
                    user_episode_text=_user_episode_text(user_story, slot_brief.slot),
                )
                drafts[episode.slot.value] = episode.script.model_dump(mode="json")
                self._save_context(
                    run_id,
                    day_brief,
                    day_step_id,
                    day_prompt_id,
                    drafts,
                    planning_metadata,
                )
            episodes.append(episode)

        plan = self._assemble_plan(
            run_id,
            day_brief,
            episodes,
            series_profile=series_profile,
        )
        self._repository.finalize_plan(
            run_id=run_id,
            plan=plan,
            selected_candidate=1,
        )
        return PlanningResult(run_id, 1, 1, plan)

    def replan_episode(
        self,
        run_id: uuid.UUID,
        *,
        slot: Slot,
        reason: str,
        allow_paid_generation: bool,
    ) -> EpisodeReplanResult:
        """只重做一个时段导演，不重写DayBrief和其他时段。"""

        self._check_paid(allow_paid_generation)
        if len(reason.strip()) < 4:
            raise ValueError("局部重规划必须提供具体原因")
        stored_run = self._repository.get_run(run_id)
        if stored_run.status in {
            RunStatus.DELIVERED.value,
            RunStatus.READY.value,
        }:
            raise ValueError(f"Run状态{stored_run.status}不允许重规划")
        stored_episodes = self._repository.list_episodes(run_id)
        stored_episode = next(
            (item for item in stored_episodes if item.plan.slot is slot),
            None,
        )
        settings = self._repository.get_pipeline_settings(run_id)
        if (
            settings.planning_mode is PlanningMode.GUIDED_SEQUENTIAL
            and any(item.plan.slot.sort_order > slot.sort_order for item in stored_episodes)
        ):
            raise ValueError("后续时段已依赖当前结果，不能原位重规划前序时段")
        # 局部重规划只作废当前Episode的下游媒体；其他已经确认的时段保持不变。
        if stored_episode is not None and stored_episode.status not in {
            EpisodeStatus.PLANNED,
            EpisodeStatus.FAILED,
        }:
            self._repository.set_episode_status(stored_episode.id, EpisodeStatus.FAILED)
        context = self._repository.get_planning_context(run_id)
        if "dayBrief" not in context:
            raise ValueError("该Run没有分层导演DayBrief，不能局部重规划")
        day_brief = DayBrief.model_validate(context["dayBrief"])
        drafts = {
            key: EpisodePlan(slot=Slot(key), script=EpisodeScript.model_validate(value))
            for key, value in context.get("episodeDrafts", {}).items()
        }
        drafts.update({item.plan.slot.value: item.plan for item in stored_episodes})

        slot_brief = next(item for item in day_brief.slot_briefs if item.slot is slot)
        previous = tuple(
            drafts[item.value]
            for item in Slot
            if item.sort_order < slot.sort_order and item.value in drafts
        )
        day_step_id = uuid.UUID(context["dayDirectorStepId"])
        day_prompt_id = uuid.UUID(context["dayDirectorPromptId"])
        metadata = context.get("planningMetadata", {})
        series_profile = _series_profile_from_metadata(metadata, self._series_profile)
        story_patterns = _story_patterns_from_metadata(metadata)
        try:
            accepted = _parse_accepted_outcomes(context.get("acceptedOutcomes", {}))
            guided_history = tuple(
                _accepted_outcome_summary(item, accepted[item])
                for item in Slot
                if item.sort_order < slot.sort_order and item in accepted
            )
            episode, _, attempt = self._generate_episode(
                run_id=run_id,
                day_brief=day_brief,
                slot_brief=slot_brief,
                previous=previous,
                previous_state_summaries=(
                    guided_history
                    if settings.planning_mode is PlanningMode.GUIDED_SEQUENTIAL
                    else None
                ),
                parent_step_id=day_step_id,
                parent_prompt_id=day_prompt_id,
                retry_reason=reason,
                recent_summaries=_parse_recent_summaries(
                    metadata.get(
                        "recentSummaries",
                        (),
                    )
                ),
                story_pattern=story_patterns.get(slot),
                series_profile=series_profile,
                user_episode_text=_user_episode_text(
                    _user_story_from_metadata(metadata),
                    slot,
                ),
            )
        except PlanningReviewRequired:
            if (
                settings.planning_mode is not PlanningMode.GUIDED_SEQUENTIAL
                and stored_run.status
                in {RunStatus.DRAFT.value, RunStatus.FAILED.value}
            ):
                self._repository.set_run_status(
                    run_id,
                    RunStatus.PLANNING_REVIEW,
                )
            raise
        drafts[slot.value] = episode
        serialized = {key: value.script.model_dump(mode="json") for key, value in drafts.items()}
        self._save_context(
            run_id,
            day_brief,
            day_step_id,
            day_prompt_id,
            serialized,
            context.get("planningMetadata", {}),
        )

        if stored_episode is not None:
            if len(drafts) == 3:
                self._assemble_plan(
                    run_id,
                    day_brief,
                    [drafts[item.value] for item in Slot],
                    series_profile=series_profile,
                )
            self._repository.replace_episode_plan(
                run_id=run_id,
                episode=episode,
            )
            if stored_run.status == RunStatus.PLANNING_REVIEW.value:
                self._repository.set_run_status(run_id, RunStatus.PLANNED)
        elif settings.planning_mode is PlanningMode.GUIDED_SEQUENTIAL:
            self._repository.save_planned_episode(run_id=run_id, episode=episode)
        elif all(item.value in drafts for item in Slot):
            plan = self._assemble_plan(
                run_id,
                day_brief,
                [drafts[item.value] for item in Slot],
                series_profile=series_profile,
            )
            self._repository.finalize_plan(
                run_id=run_id,
                plan=plan,
                selected_candidate=1,
            )
        elif stored_run.status == RunStatus.PLANNING_REVIEW.value:
            # 人工指定的时段已经修复，但后续时段尚未生成。回到draft后，
            # resume-planning只补齐剩余时段，不会再次调用已修复的导演。
            self._repository.set_run_status(run_id, RunStatus.DRAFT)
        return EpisodeReplanResult(run_id, slot, attempt, episode)

    def _generate_episode(
        self,
        *,
        run_id: uuid.UUID,
        day_brief: DayBrief,
        slot_brief: SlotBrief,
        previous: tuple[EpisodePlan, ...],
        previous_state_summaries: tuple[str, ...] | None = None,
        parent_step_id: uuid.UUID,
        parent_prompt_id: uuid.UUID,
        retry_reason: str | None,
        recent_summaries: tuple[RecentContentSummary, ...],
        story_pattern: StoryPattern | None,
        series_profile: SeriesVisualProfile,
        user_episode_text: str | None = None,
    ) -> tuple[EpisodePlan, StoredStep, int]:
        history = (
            previous_state_summaries
            if previous_state_summaries is not None
            else tuple(summarize_episode_state(item) for item in previous)
        )
        first_attempt = self._repository.next_director_attempt(
            run_id=run_id,
            phase="episode",
            slot=slot_brief.slot,
        )
        rejected_candidate: dict[str, Any] | None = None
        validation_errors: tuple[str, ...] = ()
        repair_of_step_id: uuid.UUID | None = None
        # 原始调用+一次带反馈修复：契约拒绝与语义不合格共享同一修复预算，
        # 每次都把确定性错误回灌给导演，而不是一次失败就把整天打入人工审核。
        max_repairs = 1
        for repair_index in range(max_repairs + 1):
            attempt = first_attempt + repair_index
            if user_episode_text is not None:
                # 扩写模式：改编用户原文，契约规则不变，情节不得增删。
                prompt = compile_episode_adaptation_prompt(
                    user_episode_text=user_episode_text,
                    day_brief=day_brief,
                    slot_brief=slot_brief,
                    previous_state_summaries=history,
                    retry_reason=retry_reason,
                    rejected_candidate=rejected_candidate,
                    validation_errors=validation_errors,
                    series_profile=series_profile,
                    style_profile=self._style_profile,
                )
            else:
                prompt = compile_episode_director_prompt(
                    day_brief=day_brief,
                    slot_brief=slot_brief,
                    previous_state_summaries=history,
                    retry_reason=retry_reason,
                    rejected_candidate=rejected_candidate,
                    validation_errors=validation_errors,
                    story_pattern=story_pattern,
                    series_profile=series_profile,
                    style_profile=self._style_profile,
                )
            try:
                draft, step, _, normalizations = self._director_invoker.invoke(
                    run_id=run_id,
                    episode_id=None,
                    parent_step_id=parent_step_id,
                    parent_prompt_id=parent_prompt_id,
                    phase="episode",
                    slot=slot_brief.slot,
                    attempt=attempt,
                    prompt=prompt,
                    contract=EpisodeScript,
                    repair_of_step_id=repair_of_step_id,
                )
                if normalizations:
                    # 归一化修正留审计痕：候选意图明确，仅表述被程序修正。
                    self._repository.record_review(
                        step_id=step.id,
                        asset_id=None,
                        source="technical",
                        decision="approved",
                        reason="导演候选经归一化修正后通过契约校验",
                        warnings=[
                            {"code": "normalized", "message": item} for item in normalizations
                        ],
                        evidence={"phase": "normalization"},
                    )
            except DirectorCandidateRejected as exc:
                rejected_candidate = exc.candidate
                validation_errors = exc.errors
                repair_of_step_id = exc.step.id
                if repair_index < max_repairs:
                    continue
                raise PlanningReviewRequired(
                    run_id=run_id,
                    slot=slot_brief.slot,
                    errors=validation_errors,
                ) from exc

            episode = EpisodePlan(slot=slot_brief.slot, script=draft)
            issues = (
                *validate_episode_against_brief(
                    episode,
                    day_brief=day_brief,
                    slot_brief=slot_brief,
                    series_profile=series_profile,
                ),
                *validate_episode_cooldown(episode, recent_summaries),
            )
            failures = hard_failures(issues)
            prompt_error: str | None = None
            try:
                compile_video_prompt_preview(
                    episode,
                    resolution=self._video_resolution,
                    style_profile=self._style_profile,
                    series_profile=series_profile,
                )
            except PromptCompilationError as exc:
                prompt_error = str(exc)
            if not failures and prompt_error is None:
                return episode, step, attempt
            validation_errors = (
                *(item.message for item in failures),
                *((prompt_error,) if prompt_error is not None else ()),
            )
            rejected_candidate = episode.script.model_dump(mode="json")
            repair_of_step_id = step.id
            self._repository.record_review(
                step_id=step.id,
                asset_id=None,
                source="technical",
                decision="rejected",
                reason="; ".join(validation_errors),
                warnings=[
                    {
                        "code": item.code,
                        "message": item.message,
                    }
                    for item in issues
                    if item not in failures
                ],
                evidence={
                    "phase": "episode_contract",
                    # Ark 已经成功返回可解析对象。这里属于剧情语义不合格，不能把
                    # 业务拒绝当作传输失败而自动产生第二次收费调用。
                    "providerStatus": "succeeded",
                    "contractStatus": "parsed",
                    "semanticReviewStatus": "rejected",
                    "autoRepairScheduled": repair_index < max_repairs,
                },
            )
            if repair_index < max_repairs:
                # 语义不合格同样带确定性错误反馈再给一次机会；反馈包含规则消息，
                # 导演修复这类错误的成功率远高于让人工介入一次整天报废。
                continue
            raise PlanningReviewRequired(
                run_id=run_id,
                slot=slot_brief.slot,
                errors=validation_errors,
            )
        raise AssertionError("每个时段最多执行一次原始导演调用和一次结构修复")

    def _assemble_plan(
        self,
        run_id: uuid.UUID,
        day_brief: DayBrief,
        episodes: list[EpisodePlan],
        *,
        series_profile: SeriesVisualProfile,
    ) -> DailyProductionPlan:
        plan = DailyProductionPlan(
            day_brief=day_brief,
            episodes=episodes,
        )
        failures = hard_failures(
            (
                *validate_plan_gate(
                    plan,
                    expected_date=day_brief.content_date,
                    series_profile=series_profile,
                ),
            )
        )
        if failures:
            raise PlanningReviewRequired(
                run_id=run_id,
                slot=Slot.EVENING,
                errors=tuple(item.message for item in failures),
            )
        return plan

    def _save_context(
        self,
        run_id: uuid.UUID,
        day_brief: DayBrief,
        day_step_id: uuid.UUID,
        day_prompt_id: uuid.UUID,
        drafts: dict[str, dict[str, Any]],
        planning_metadata: dict[str, Any],
    ) -> None:
        self._repository.save_planning_context(
            run_id=run_id,
            day_brief=day_brief,
            day_step_id=day_step_id,
            day_prompt_id=day_prompt_id,
            episode_drafts=drafts,
            planning_metadata=planning_metadata,
        )

    @staticmethod
    def _check_paid(allow_paid_generation: bool) -> None:
        if not allow_paid_generation:
            raise ValueError("导演规划需要显式提供--allow-paid-generation")


def _parse_recent_summaries(
    values: object,
) -> tuple[RecentContentSummary, ...]:
    """从当前planning_json恢复严格的结构化近期摘要。"""

    if not isinstance(values, (list, tuple)):
        return ()
    result: list[RecentContentSummary] = []
    for value in values:
        result.append(RecentContentSummary.model_validate(value))
    return tuple(result)


def _series_profile_from_metadata(
    metadata: dict[str, Any],
    fallback: SeriesVisualProfile,
) -> SeriesVisualProfile:
    """恢复本Run冻结的人格档案，保证续跑不会受后来默认值变化影响。"""

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


def _user_story_from_metadata(metadata: dict[str, Any]) -> UserStory | None:
    """从冻结的规划元数据恢复用户完整剧情；创作模式返回None。"""

    value = metadata.get("userStory")
    if not isinstance(value, dict):
        return None
    return UserStory.model_validate(value)


def _validate_day_brief_controls(
    day_brief: DayBrief,
    controls: RunCreativeControls,
) -> None:
    """总导演只能解析adaptive，不能改写用户固定的主次关系和时长档。"""

    for brief in day_brief.slot_briefs:
        control = next(item for item in controls.slot_controls if item.slot is brief.slot)
        requested_focus = controls.requested_focus(brief.slot)
        if (
            requested_focus is not ActivityFocusMode.ADAPTIVE
            and brief.activity_focus.value != requested_focus.value
        ):
            raise ValueError(f"总导演改写了{brief.slot.value}固定活动焦点")
        if (
            control.duration_mode is not DurationMode.ADAPTIVE
            and brief.duration_band.value != control.duration_mode.value
        ):
            raise ValueError(f"总导演改写了{brief.slot.value}固定时长档")


def _user_episode_text(user_story: UserStory | None, slot: Slot) -> str | None:
    """按时段顺序取用户写好的该集剧情原文。"""

    if user_story is None:
        return None
    return user_story.episodes[slot.sort_order - 1]


def _parse_accepted_outcomes(value: object) -> dict[Slot, AcceptedOutcome]:
    if not isinstance(value, dict):
        return {}
    return {
        Slot(raw_slot): AcceptedOutcome.model_validate(raw_outcome)
        for raw_slot, raw_outcome in value.items()
        if isinstance(raw_outcome, dict)
    }


def _accepted_outcome_summary(slot: Slot, outcome: AcceptedOutcome) -> str:
    inherited = "、".join(outcome.carry_forward) or "无必须延续对象"
    excluded = "、".join(outcome.do_not_carry_forward) or "无"
    return (
        f"{slot.value}已由用户观看并确认：{outcome.summary}；"
        f"下一时段必须延续：{inherited}；不得继承偶发生成内容：{excluded}。"
    )
