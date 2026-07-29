"""总导演与三个时段导演的顺序规划用例。

一次全天规划固定产生四个独立 Ark 调用：DayBrief、morning、noon、evening。
每个 Prompt 和结构化输出都先落入工作流，单个时段失败不会要求重做全天方向。
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any, TypeVar

from pydantic import ValidationError

from ..domain.contracts import (
    DayBrief,
    DailyProductionPlan,
    EpisodeDirectorDraft,
    EpisodePlan,
    Slot,
    SlotBrief,
    StrictModel,
)
from ..domain.prompts import (
    compile_day_director_prompt,
    compile_episode_director_prompt,
    summarize_episode_state,
)
from ..domain.rules import (
    hard_failures,
    select_video_input_mode,
    validate_episode_against_brief,
    validate_plan_gate,
)
from ..domain.workflow import RunStatus, StepKind, StepStatus
from .ports import DirectorGateway, GatewayError, StoredStep, WorkflowRepository

ContractT = TypeVar("ContractT", bound=StrictModel)


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


class PlanningService:
    """总方向、时段细化、确定性硬门和局部重规划。"""

    def __init__(
        self,
        *,
        repository: WorkflowRepository,
        director: DirectorGateway,
        provider_name: str,
    ) -> None:
        self._repository = repository
        self._director = director
        self._provider_name = provider_name

    def plan_day(
        self,
        *,
        target_date: date,
        planning_context: str,
        candidate_count: int,
        allow_paid_generation: bool,
    ) -> PlanningResult:
        """按总导演→早→中→晚生成一份可执行全天方案。"""

        self._check_paid(allow_paid_generation)
        if candidate_count != 1:
            raise ValueError(
                "分层导演模式固定生成一份DayBrief；DAILY_PLAN_CANDIDATE_COUNT必须为1"
            )

        run_id = self._repository.create_draft_run(target_date)
        day_prompt = compile_day_director_prompt(
            target_date=target_date,
            planning_context=planning_context,
        )
        day_brief, day_step, day_prompt_id = self._invoke_director(
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
            self._repository.set_run_status(run_id, RunStatus.FAILED)
            raise RuntimeError("DayBrief内容日期与目标日期不一致")

        drafts: dict[str, dict[str, Any]] = {}
        self._save_context(
            run_id,
            day_brief,
            day_step.id,
            day_prompt_id,
            drafts,
        )
        episodes: list[EpisodePlan] = []
        for slot_brief in day_brief.slots:
            episode, _, _ = self._generate_episode(
                run_id=run_id,
                day_brief=day_brief,
                slot_brief=slot_brief,
                previous=tuple(episodes),
                parent_step_id=day_step.id,
                parent_prompt_id=day_prompt_id,
                retry_reason=None,
            )
            episodes.append(episode)
            drafts[episode.slot.value] = episode.model_dump(mode="json")
            self._save_context(
                run_id,
                day_brief,
                day_step.id,
                day_prompt_id,
                drafts,
            )

        plan = self._assemble_plan(day_brief, episodes)
        self._repository.finalize_plan(
            run_id=run_id,
            plan=plan,
            selected_candidate=1,
        )
        return PlanningResult(
            run_id=run_id,
            selected_candidate=1,
            candidate_count=1,
            plan=plan,
        )

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
            RunStatus.ARCHIVED.value,
        }:
            raise ValueError(f"Run状态{stored_run.status}不允许重规划")
        context = self._repository.get_planning_context(run_id)
        if "dayBrief" not in context:
            raise ValueError("该Run没有分层导演DayBrief，不能局部重规划")
        day_brief = DayBrief.model_validate(context["dayBrief"])
        drafts = {
            key: EpisodePlan.model_validate(value)
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
        episode, _, attempt = self._generate_episode(
            run_id=run_id,
            day_brief=day_brief,
            slot_brief=slot_brief,
            previous=previous,
            parent_step_id=day_step_id,
            parent_prompt_id=day_prompt_id,
            retry_reason=reason,
        )
        drafts[slot.value] = episode
        serialized = {
            key: value.model_dump(mode="json") for key, value in drafts.items()
        }
        self._save_context(
            run_id,
            day_brief,
            day_step_id,
            day_prompt_id,
            serialized,
        )

        if stored_run.plan is not None:
            self._assemble_plan(
                day_brief,
                [
                    episode if item.slot is slot else item
                    for item in stored_run.plan.episodes
                ],
            )
            self._repository.replace_episode_plan(
                run_id=run_id,
                episode=episode,
            )
        elif all(item.value in drafts for item in Slot):
            plan = self._assemble_plan(
                day_brief,
                [drafts[item.value] for item in Slot],
            )
            self._repository.finalize_plan(
                run_id=run_id,
                plan=plan,
                selected_candidate=1,
            )
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
    ) -> tuple[EpisodePlan, StoredStep, int]:
        attempt = self._repository.next_director_attempt(
            run_id=run_id,
            phase="episode",
            slot=slot_brief.slot,
        )
        prompt = compile_episode_director_prompt(
            day_brief=day_brief,
            slot_brief=slot_brief,
            previous_state_summaries=tuple(
                summarize_episode_state(item) for item in previous
            ),
            retry_reason=retry_reason,
        )
        draft, step, _ = self._invoke_director(
            run_id=run_id,
            episode_id=None,
            parent_step_id=parent_step_id,
            parent_prompt_id=parent_prompt_id,
            phase="episode",
            slot=slot_brief.slot,
            attempt=attempt,
            prompt=prompt,
            contract=EpisodeDirectorDraft,
        )
        episode = draft.finalize(slot_brief.slot)
        episode = episode.model_copy(
            update={"video_input_mode": select_video_input_mode(episode)}
        )
        issues = validate_episode_against_brief(
            episode,
            day_brief=day_brief,
            slot_brief=slot_brief,
        )
        failures = hard_failures(issues)
        if failures:
            self._repository.record_review(
                step_id=step.id,
                asset_id=None,
                source="technical",
                decision="rejected",
                reason="; ".join(item.message for item in failures),
                warnings=[],
                evidence={"phase": "episode_contract"},
            )
            raise RuntimeError("; ".join(item.message for item in failures))
        return episode, step, attempt

    def _invoke_director(
        self,
        *,
        run_id: uuid.UUID,
        episode_id: uuid.UUID | None,
        parent_step_id: uuid.UUID | None,
        parent_prompt_id: uuid.UUID | None,
        phase: str,
        slot: Slot | None,
        attempt: int,
        prompt: str,
        contract: type[ContractT],
    ) -> tuple[ContractT, StoredStep, uuid.UUID]:
        """持久化收费意图和Prompt后，执行一次结构化导演调用。"""

        input_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        summary: dict[str, Any] = {"phase": phase}
        if slot is not None:
            summary["slot"] = slot.value
        step = self._repository.create_step_intent(
            run_id=run_id,
            episode_id=episode_id,
            parent_step_id=parent_step_id,
            kind=StepKind.DIRECTOR,
            attempt=attempt,
            provider=self._provider_name,
            model=self._director.model,
            input_hash=input_hash,
            request_summary=summary,
        )
        prompt_id = self._repository.save_prompt(
            step_id=step.id,
            parent_prompt_id=parent_prompt_id,
            purpose="director",
            model=self._director.model,
            text=prompt,
        )
        if step.status is StepStatus.SUCCEEDED:
            saved = step.request_summary.get("directorOutput")
            if not isinstance(saved, dict):
                raise RuntimeError("导演步骤已成功但缺少持久化输出")
            return contract.model_validate(saved), step, prompt_id
        self._repository.set_step_status(step.id, StepStatus.SUBMITTING)
        try:
            result = self._director.generate_structured(
                prompt=prompt,
                schema=contract.model_json_schema(),
                output_name=contract.__name__,
            )
            parsed = contract.model_validate(result.payload)
        except ValidationError as exc:
            self._repository.fail_step(
                step.id,
                code="invalid_director_output",
                message=_validation_summary(exc),
            )
            raise RuntimeError(
                f"{contract.__name__}未通过结构校验: {_validation_summary(exc)}"
            ) from exc
        except GatewayError as exc:
            self._repository.fail_step(
                step.id,
                code=exc.code,
                message=str(exc),
                submission_unknown=exc.submission_unknown,
            )
            if exc.submission_unknown:
                raise RuntimeError(
                    "导演提交结果未知，必须先对账，禁止重复调用"
                ) from exc
            raise
        self._repository.finish_director_step(
            step_id=step.id,
            response_id=result.response_id,
            request_hash=result.request_hash,
            output=result.payload,
        )
        return parsed, self._repository.get_step(step.id), prompt_id

    def _assemble_plan(
        self,
        day_brief: DayBrief,
        episodes: list[EpisodePlan],
    ) -> DailyProductionPlan:
        plan = DailyProductionPlan(
            content_date=day_brief.content_date,
            theme=day_brief.theme,
            day_context=day_brief.day_context,
            shared_elements=day_brief.shared_elements,
            episodes=episodes,
        )
        failures = hard_failures(
            validate_plan_gate(plan, expected_date=day_brief.content_date)
        )
        if failures:
            raise RuntimeError("; ".join(item.message for item in failures))
        return plan

    def _save_context(
        self,
        run_id: uuid.UUID,
        day_brief: DayBrief,
        day_step_id: uuid.UUID,
        day_prompt_id: uuid.UUID,
        drafts: dict[str, dict[str, Any]],
    ) -> None:
        self._repository.save_planning_context(
            run_id=run_id,
            day_brief=day_brief,
            day_step_id=day_step_id,
            day_prompt_id=day_prompt_id,
            episode_drafts=drafts,
        )

    @staticmethod
    def _check_paid(allow_paid_generation: bool) -> None:
        if not allow_paid_generation:
            raise ValueError("导演规划需要显式提供--allow-paid-generation")


def _validation_summary(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors(include_input=False, include_url=False)[:8]:
        location = ".".join(str(item) for item in error["loc"]) or "$"
        parts.append(f"{location}:{error['msg']}")
    return "；".join(parts)
