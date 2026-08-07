"""四次独立导演调用与Slot本地注入。"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from datetime import date

import pytest

from cat_video_generator.application.planning import PlanningReviewRequired, PlanningService
from cat_video_generator.application.ports import DirectorResult, StoredRun, StoredStep
from cat_video_generator.domain.contracts import DayBrief, Slot
from cat_video_generator.domain.visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
)
from cat_video_generator.domain.workflow import RunStatus, StepKind, StepStatus


class Director:
    model = "planning-model"

    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads
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

    def create_draft_run(self, content_date: date) -> uuid.UUID:
        return self.run_id

    def save_pipeline_settings(self, **kwargs):
        self.pipeline_settings = kwargs["settings"]

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

    def save_prompt(self, **kwargs):
        return uuid.uuid4()

    def set_step_status(self, step_id, target, **kwargs):
        self.steps[step_id] = replace(self.steps[step_id], status=target)

    def finish_director_step(self, **kwargs):
        step = self.steps[kwargs["step_id"]]
        snapshot = {**step.input_snapshot, "output": kwargs["output"]}
        self.steps[step.id] = replace(
            step,
            status=StepStatus.SUCCEEDED,
            input_snapshot=snapshot,
        )

    def fail_director_step(self, **kwargs):
        step = self.steps[kwargs["step_id"]]
        snapshot = {**step.input_snapshot, "output": kwargs["output"]}
        self.steps[step.id] = replace(
            step,
            status=StepStatus.FAILED,
            input_snapshot=snapshot,
        )

    def fail_step(self, step_id, **kwargs):
        self.steps[step_id] = replace(self.steps[step_id], status=StepStatus.FAILED)

    def get_step(self, step_id):
        return self.steps[step_id]

    def next_director_attempt(self, **kwargs):
        return 1

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
        return StoredRun(
            id=self.run_id,
            content_date=date(2026, 8, 2),
            status=self.run_status,
            plan=self.plan,
        )

    def set_run_status(self, run_id, target):
        self.run_status = target.value

    def record_review(self, **kwargs):
        return uuid.uuid4()

    def replace_episode_plan(self, **kwargs):
        raise AssertionError("初始规划不应调用局部替换")


class EmptySeeds:
    def select(self, **kwargs):
        return ()

    def select_patterns(self, **kwargs):
        return {}


def test_planning_calls_day_and_three_episode_directors(daily_plan) -> None:
    payloads = [
        daily_plan.day_brief.model_dump(mode="json"),
        *(item.script.model_dump(mode="json") for item in daily_plan.episodes),
    ]
    director = Director(payloads)
    repository = PlanningRepository()
    service = PlanningService(
        repository=repository,
        director=director,
        provider_name="volcengine-ark-standard",
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
        event_seed_catalog=EmptySeeds(),
    )
    result = service.plan_day(
        target_date=daily_plan.content_date,
        planning_context="设计普通生活中的小发现",
        candidate_count=1,
        allow_paid_generation=True,
    )
    assert len(director.prompts) == 4
    assert isinstance(result.plan.day_brief, DayBrief)
    assert [item.slot for item in result.plan.episodes] == list(Slot)
    assert all("slot" not in item.script.model_dump() for item in result.plan.episodes)
    assert [item.kind for item in repository.steps.values()] == [
        StepKind.DIRECTOR,
        StepKind.DIRECTOR,
        StepKind.DIRECTOR,
        StepKind.DIRECTOR,
    ]


def test_structural_director_failure_repairs_once(daily_plan) -> None:
    invalid = daily_plan.episodes[0].script.model_dump(mode="json")
    invalid.pop("title")
    payloads = [
        daily_plan.day_brief.model_dump(mode="json"),
        invalid,
        *(item.script.model_dump(mode="json") for item in daily_plan.episodes),
    ]
    director = Director(payloads)
    repository = PlanningRepository()
    service = PlanningService(
        repository=repository,
        director=director,
        provider_name="volcengine-ark-standard",
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
        event_seed_catalog=EmptySeeds(),
    )

    result = service.plan_day(
        target_date=daily_plan.content_date,
        planning_context="设计普通生活中的小发现",
        candidate_count=1,
        allow_paid_generation=True,
    )

    assert result.plan.episodes[0].script.title == daily_plan.episodes[0].script.title
    assert len(director.prompts) == 5


def test_semantic_director_failure_requires_explicit_replan(daily_plan) -> None:
    gendered = daily_plan.episodes[0].script.model_copy(
        update={"main_event": "固定女孩在阳台发现风吹动纸风车并拿起来观察"}
    )
    # 语义失败会带反馈自动修复（原始+两次修复共三次尝试），
    # 三次都不合格才进入人工规划审核。
    director = Director(
        [
            daily_plan.day_brief.model_dump(mode="json"),
            gendered.model_dump(mode="json"),
            gendered.model_dump(mode="json"),
            gendered.model_dump(mode="json"),
        ]
    )
    repository = PlanningRepository()
    service = PlanningService(
        repository=repository,
        director=director,
        provider_name="volcengine-ark-standard",
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
        event_seed_catalog=EmptySeeds(),
    )

    with pytest.raises(PlanningReviewRequired, match="需要人工规划审核"):
        service.plan_day(
            target_date=daily_plan.content_date,
            planning_context="设计普通生活中的小发现",
            candidate_count=1,
            allow_paid_generation=True,
        )

    assert len(director.prompts) == 4
    assert repository.run_status == RunStatus.PLANNING_REVIEW.value
