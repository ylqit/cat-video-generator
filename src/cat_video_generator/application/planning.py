"""总导演与三个时段导演的顺序规划用例。

一次全天规划固定产生四个独立 Ark 调用：DayBrief、morning、noon、evening。
每个 Prompt 和结构化输出都先落入工作流，单个时段失败不会要求重做全天方向。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ..domain.contracts import (
    DailyProductionPlan,
    DayBrief,
    EpisodePlan,
    EpisodeScript,
    RecentContentSummary,
    Slot,
    SlotBrief,
)
from ..domain.pipeline import PipelineSettings
from ..domain.prompts import (
    PromptCompilationError,
    compile_day_director_prompt,
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
from ..domain.visual_profiles import (
    SeriesVisualProfile,
    StyleProfile,
)
from ..domain.workflow import RunStatus
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
        super().__init__(
            f"Run {run_id} 的 {slot.value} 时段需要人工规划审核：" + "；".join(errors)
        )
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
        stop_after_day_brief: bool = False,
        pipeline_settings: PipelineSettings | None = None,
    ) -> PlanningResult | DayBriefPause:
        """按总导演→早→中→晚生成一份可执行全天方案。

        ``stop_after_day_brief`` 用于创作台dayBrief阶段的手动开关：
        DayBrief落库后直接断点返回（Run保持draft），人工编辑后由
        ``resume_planning`` 续跑；流水线开关随本次提交一次性持久化。
        """

        self._check_paid(allow_paid_generation)
        if candidate_count != 1:
            raise ValueError("分层导演模式固定生成一份DayBrief；DAILY_PLAN_CANDIDATE_COUNT必须为1")

        recent_summaries = self._repository.list_recent_completed_summaries(limit=6)
        event_seeds = self._event_seed_catalog.select(
            series_profile_hash=self._series_profile.fingerprint(),
            content_date=target_date,
            planning_revision=1,
            planning_context=planning_context,
        )
        planning_metadata = {
            "planningRevision": 1,
            "seriesProfileHash": self._series_profile.fingerprint(),
            "recentSummaries": [item.model_dump(mode="json") for item in recent_summaries],
            "eventSeeds": [item.model_dump(mode="json") for item in event_seeds],
        }
        run_id = self._repository.create_draft_run(target_date)
        self._repository.save_pipeline_settings(
            run_id=run_id,
            settings=pipeline_settings
            or PipelineSettings(allow_paid_generation=allow_paid_generation),
        )
        try:
            day_prompt = compile_day_director_prompt(
                target_date=target_date,
                planning_context=planning_context,
                recent_summaries=recent_summaries,
                event_seeds=tuple(
                    f"[{item.seed_id}|{','.join(slot.value for slot in item.slots)}]"
                    f"{item.direction}"
                    for item in event_seeds
                ),
                series_profile=self._series_profile,
            )
            day_brief, day_step, day_prompt_id = self._director_invoker.invoke(
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

            drafts: dict[str, dict[str, Any]] = {}
            self._save_context(
                run_id,
                day_brief,
                day_step.id,
                day_prompt_id,
                drafts,
                planning_metadata,
            )
            if stop_after_day_brief:
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

        episodes: list[EpisodePlan] = []
        for slot_brief in day_brief.slots:
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
        context = self._repository.get_planning_context(run_id)
        if "dayBrief" not in context:
            raise ValueError("该Run没有分层导演DayBrief，不能局部重规划")
        day_brief = DayBrief.model_validate(context["dayBrief"])
        drafts = {
            key: EpisodePlan(slot=Slot(key), script=EpisodeScript.model_validate(value))
            for key, value in context.get("episodeDrafts", {}).items()
        }
        if stored_run.plan is not None:
            drafts.update({item.slot.value: item for item in stored_run.plan.episodes})

        slot_brief = next(item for item in day_brief.slots if item.slot is slot)
        previous = tuple(
            drafts[item.value]
            for item in Slot
            if item.sort_order < slot.sort_order and item.value in drafts
        )
        day_step_id = uuid.UUID(context["dayDirectorStepId"])
        day_prompt_id = uuid.UUID(context["dayDirectorPromptId"])
        try:
            episode, _, attempt = self._generate_episode(
                run_id=run_id,
                day_brief=day_brief,
                slot_brief=slot_brief,
                previous=previous,
                parent_step_id=day_step_id,
                parent_prompt_id=day_prompt_id,
                retry_reason=reason,
                recent_summaries=_parse_recent_summaries(
                    context.get("planningMetadata", {}).get(
                        "recentSummaries",
                        (),
                    )
                ),
            )
        except PlanningReviewRequired:
            if stored_run.status != RunStatus.PLANNING_REVIEW.value:
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

        if stored_run.plan is not None:
            self._assemble_plan(
                run_id,
                day_brief,
                [episode if item.slot is slot else item for item in stored_run.plan.episodes],
            )
            self._repository.replace_episode_plan(
                run_id=run_id,
                episode=episode,
            )
            if stored_run.status == RunStatus.PLANNING_REVIEW.value:
                self._repository.set_run_status(run_id, RunStatus.PLANNED)
        elif all(item.value in drafts for item in Slot):
            plan = self._assemble_plan(
                run_id,
                day_brief,
                [drafts[item.value] for item in Slot],
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
        parent_step_id: uuid.UUID,
        parent_prompt_id: uuid.UUID,
        retry_reason: str | None,
        recent_summaries: tuple[RecentContentSummary, ...],
    ) -> tuple[EpisodePlan, StoredStep, int]:
        first_attempt = self._repository.next_director_attempt(
            run_id=run_id,
            phase="episode",
            slot=slot_brief.slot,
        )
        rejected_candidate: dict[str, Any] | None = None
        validation_errors: tuple[str, ...] = ()
        repair_of_step_id: uuid.UUID | None = None
        for repair_index in range(2):
            attempt = first_attempt + repair_index
            prompt = compile_episode_director_prompt(
                day_brief=day_brief,
                slot_brief=slot_brief,
                previous_state_summaries=tuple(summarize_episode_state(item) for item in previous),
                retry_reason=retry_reason,
                rejected_candidate=rejected_candidate,
                validation_errors=validation_errors,
                series_profile=self._series_profile,
            )
            try:
                draft, step, _ = self._director_invoker.invoke(
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
            except DirectorCandidateRejected as exc:
                rejected_candidate = exc.candidate
                validation_errors = exc.errors
                repair_of_step_id = exc.step.id
                if repair_index == 0:
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
                    series_profile=self._series_profile,
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
                    "autoRepairScheduled": False,
                },
            )
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
                    series_profile=self._series_profile,
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
