"""四次独立导演调用与Slot本地注入。"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from datetime import date

from cat_video_generator.application.planning import PlanningService
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

    def list_recent_completed_summaries(self, *, limit: int):
        assert limit == 6
        return ()

    def create_step_intent(self, **kwargs):
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
        return step

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
        raise AssertionError(kwargs)

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
