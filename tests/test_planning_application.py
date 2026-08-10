"""总导演、三个顺序时段导演与一次结构修复。"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from datetime import date

import pytest

from cat_video_generator.application.planning import (
    DayBriefPause,
    PlanningReviewRequired,
    PlanningService,
)
from cat_video_generator.application.ports import DirectorResult, StoredRun, StoredStep
from cat_video_generator.domain.contracts import (
    ActivityFocusMode,
    DayBrief,
    RunCreativeControls,
    Slot,
    SlotCreativeControl,
)
from cat_video_generator.domain.pipeline import PipelineSettings
from cat_video_generator.domain.visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
)
from cat_video_generator.domain.workflow import RunStatus, StepKind, StepStatus


class Director:
    model = "planning-model"

    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.prompts: list[str] = []

    def generate_structured(self, *, prompt, schema, output_name):
        self.prompts.append(prompt)
        payload = self.payloads.pop(0)
        return DirectorResult(
            payload=payload,
            response_id=f"response-{len(self.prompts)}",
            model=self.model,
            request_hash=hashlib.sha256(prompt.encode()).hexdigest(),
        )


class PlanningRepository:
    def __init__(self) -> None:
        self.run_id = uuid.uuid4()
        self.steps: dict[uuid.UUID, StoredStep] = {}
        self.context: dict = {}
        self.plan = None
        self.run_status = RunStatus.DRAFT.value
        self.pipeline_settings = PipelineSettings()

    def create_draft_run(self, content_date: date) -> uuid.UUID:
        self.content_date = content_date
        return self.run_id

    def save_pipeline_settings(self, **kwargs):
        self.pipeline_settings = kwargs["settings"]

    def get_pipeline_settings(self, run_id):
        return self.pipeline_settings

    def list_recent_completed_summaries(self, *, limit: int):
        assert limit == 6
        return ()

    def create_step_with_prompt_intent(self, **kwargs):
        step = StoredStep(
            id=uuid.uuid4(),
            run_id=kwargs["run_id"],
            episode_id=kwargs["episode_id"],
            kind=kwargs["kind"],
            status=StepStatus.PENDING,
            attempt=kwargs["attempt"],
            provider_task_id=None,
            model=kwargs["model"],
            operation_key=kwargs["operation_key"],
            input_snapshot=kwargs["input_snapshot"],
        )
        self.steps[step.id] = step
        return step, uuid.uuid4()

    def set_step_status(self, step_id, target, **kwargs):
        self.steps[step_id] = replace(self.steps[step_id], status=target)

    def finish_director_step(self, **kwargs):
        step = self.steps[kwargs["step_id"]]
        self.steps[step.id] = replace(
            step,
            status=StepStatus.SUCCEEDED,
            input_snapshot={
                **step.input_snapshot,
                "provider_output": kwargs["provider_output"],
                "normalized_output": kwargs["normalized_output"],
                "normalization_warnings": kwargs["normalization_warnings"],
                "response_id": kwargs["response_id"],
                "request_hash": kwargs["request_hash"],
            },
        )

    def fail_director_step(self, **kwargs):
        step = self.steps[kwargs["step_id"]]
        self.steps[step.id] = replace(step, status=StepStatus.FAILED)

    def fail_step(self, step_id, **kwargs):
        self.steps[step_id] = replace(self.steps[step_id], status=StepStatus.FAILED)

    def get_step(self, step_id):
        return self.steps[step_id]

    def next_director_attempt(self, **kwargs):
        phase = kwargs["phase"]
        slot = kwargs["slot"]
        prefix = f"director:{phase}" + (f":{slot.value}" if slot is not None else "")
        attempts = [step.attempt for step in self.steps.values() if step.operation_key == prefix]
        return max(attempts, default=0) + 1

    def save_planning_context(self, **kwargs):
        self.context = {
            "dayBrief": kwargs["day_brief"].model_dump(mode="json"),
            "dayDirectorStepId": str(kwargs["day_step_id"]),
            "dayDirectorPromptId": str(kwargs["day_prompt_id"]),
            "episodeDrafts": kwargs["episode_drafts"],
            "planningMetadata": kwargs["planning_metadata"],
        }

    def get_planning_context(self, run_id):
        return self.context

    def finalize_plan(self, **kwargs):
        self.plan = kwargs["plan"]
        self.run_status = RunStatus.PLANNED.value

    def get_run(self, run_id):
        return StoredRun(self.run_id, self.content_date, self.run_status, self.plan)

    def set_run_status(self, run_id, target):
        self.run_status = target.value

    def record_review(self, **kwargs):
        return uuid.uuid4()

    def replace_episode_plan(self, **kwargs):
        raise AssertionError("初始规划不应局部替换Episode")


class EmptySeeds:
    def select(self, **kwargs):
        return ()

    def select_patterns(self, **kwargs):
        return {}


def service(repository, director) -> PlanningService:
    return PlanningService(
        repository=repository,
        director=director,
        provider_name="volcengine-ark-standard",
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
        event_seed_catalog=EmptySeeds(),
    )


def test_planning_calls_day_then_morning_noon_evening(daily_plan) -> None:
    director = Director(
        [
            daily_plan.day_brief.model_dump(mode="json"),
            *(item.script.model_dump(mode="json") for item in daily_plan.episodes),
        ]
    )
    repository = PlanningRepository()

    result = service(repository, director).plan_day(
        target_date=daily_plan.content_date,
        planning_context="完整放风筝生活弧",
        candidate_count=1,
        allow_paid_generation=True,
    )

    assert len(director.prompts) == 4
    assert isinstance(result.plan.day_brief, DayBrief)
    assert [item.slot for item in result.plan.episodes] == list(Slot)
    assert [item.operation_key for item in repository.steps.values()] == [
        "director:day",
        "director:episode:morning",
        "director:episode:noon",
        "director:episode:evening",
    ]
    assert all(item.kind is StepKind.DIRECTOR for item in repository.steps.values())
    noon_step = next(
        item for item in repository.steps.values() if item.operation_key == "director:episode:noon"
    )
    assert noon_step.input_snapshot["provider_output"]["hard_constraints"]
    assert noon_step.input_snapshot["normalized_output"] is None
    assert noon_step.input_snapshot["normalization_warnings"] == ()
    assert "storyText" in director.prompts[2]
    assert "hardConstraints" in director.prompts[2]


def test_day_brief_manual_mode_pauses_before_slot_directors(daily_plan) -> None:
    director = Director([daily_plan.day_brief.model_dump(mode="json")])
    repository = PlanningRepository()

    result = service(repository, director).plan_day(
        target_date=daily_plan.content_date,
        planning_context="先确认全天主次和时长",
        candidate_count=1,
        allow_paid_generation=True,
        stop_after_day_brief=True,
    )

    assert isinstance(result, DayBriefPause)
    assert len(director.prompts) == 1
    assert repository.plan is None
    assert repository.context["episodeDrafts"] == {}


def test_fixed_slot_focus_cannot_be_changed_by_day_director(daily_plan) -> None:
    payload = daily_plan.day_brief.model_dump(mode="json")
    payload["slot_briefs"][1]["activity_focus"] = "person_lead"
    director = Director([payload])
    repository = PlanningRepository()
    controls = RunCreativeControls(
        slot_controls=[
            SlotCreativeControl(slot=Slot.MORNING),
            SlotCreativeControl(slot=Slot.NOON, activity_focus=ActivityFocusMode.CAT_LEAD),
            SlotCreativeControl(slot=Slot.EVENING),
        ]
    )

    with pytest.raises(ValueError, match="改写了noon固定活动焦点"):
        service(repository, director).plan_day(
            target_date=daily_plan.content_date,
            planning_context="放风筝",
            candidate_count=1,
            allow_paid_generation=True,
            creative_controls=controls,
        )


def test_invalid_episode_contract_gets_exactly_one_repair(daily_plan) -> None:
    invalid = daily_plan.episodes[0].script.model_dump(mode="json")
    invalid.pop("relationship_arc")
    director = Director(
        [
            daily_plan.day_brief.model_dump(mode="json"),
            invalid,
            daily_plan.episodes[0].script.model_dump(mode="json"),
            daily_plan.episodes[1].script.model_dump(mode="json"),
            daily_plan.episodes[2].script.model_dump(mode="json"),
        ]
    )
    repository = PlanningRepository()

    result = service(repository, director).plan_day(
        target_date=daily_plan.content_date,
        planning_context="放风筝",
        candidate_count=1,
        allow_paid_generation=True,
    )

    assert result.plan.episodes[0].script.relationship_arc
    assert len(director.prompts) == 5


def test_second_invalid_episode_enters_planning_review(daily_plan) -> None:
    invalid = daily_plan.episodes[0].script.model_dump(mode="json")
    invalid["shots"][0]["order"] = 2
    director = Director([daily_plan.day_brief.model_dump(mode="json"), invalid, invalid])
    repository = PlanningRepository()

    with pytest.raises(PlanningReviewRequired, match="需要人工规划审核"):
        service(repository, director).plan_day(
            target_date=daily_plan.content_date,
            planning_context="放风筝",
            candidate_count=1,
            allow_paid_generation=True,
        )

    assert len(director.prompts) == 3
    assert repository.run_status == RunStatus.PLANNING_REVIEW.value
