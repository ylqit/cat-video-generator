from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace

from cat_video_generator.application.planning import PlanningService
from cat_video_generator.application.ports import (
    DirectorResult,
    StoredRun,
    StoredStep,
)
from cat_video_generator.domain.contracts import (
    DayBrief,
    EpisodeDirectorDraft,
    EpisodePlan,
    Slot,
    SlotBrief,
)
from cat_video_generator.domain.workflow import RunStatus, StepKind, StepStatus


class RecordingRepository:
    def __init__(self) -> None:
        self.run_id = uuid.uuid4()
        self.events: list[str] = []
        self.steps: dict[uuid.UUID, StoredStep] = {}
        self.finalized = None
        self.context = {}

    def create_draft_run(self, _):
        self.events.append("create_run")
        return self.run_id

    def create_step_intent(self, **kwargs):
        index = len(self.steps) + 1
        self.events.append(f"intent-{index}")
        step = StoredStep(
            id=uuid.uuid4(),
            run_id=self.run_id,
            episode_id=None,
            kind=StepKind.DIRECTOR,
            status=StepStatus.PENDING,
            attempt=kwargs["attempt"],
            provider_task_id=None,
            model=kwargs["model"],
            request_summary=kwargs["request_summary"],
        )
        self.steps[step.id] = step
        return step

    def save_prompt(self, **_):
        index = len(self.steps)
        self.events.append(f"prompt-{index}")
        return uuid.uuid4()

    def set_step_status(self, step_id, target, **__):
        index = list(self.steps).index(step_id) + 1
        self.events.append(f"{target.value}-{index}")
        self.steps[step_id] = replace(self.steps[step_id], status=target)

    def finish_director_step(self, **kwargs):
        step_id = kwargs["step_id"]
        index = list(self.steps).index(step_id) + 1
        self.events.append(f"finished-{index}")
        self.steps[step_id] = replace(
            self.steps[step_id],
            status=StepStatus.SUCCEEDED,
            request_summary={
                **self.steps[step_id].request_summary,
                "directorOutput": kwargs["output"],
            },
        )

    def get_step(self, step_id):
        return self.steps[step_id]

    def fail_step(self, *_, **__):
        raise AssertionError("导演调用不应失败")

    def record_review(self, **_):
        raise AssertionError("导演输出不应被拒绝")

    def finalize_plan(self, **kwargs):
        self.finalized = kwargs

    def save_planning_context(self, **kwargs):
        self.context = {
            "dayBrief": kwargs["day_brief"].model_dump(mode="json"),
            "dayDirectorStepId": str(kwargs["day_step_id"]),
            "dayDirectorPromptId": str(kwargs["day_prompt_id"]),
            "episodeDrafts": kwargs["episode_drafts"],
        }

    def next_director_attempt(self, *, phase, slot, **_):
        attempts = [
            item.attempt
            for item in self.steps.values()
            if item.request_summary.get("phase") == phase
            and item.request_summary.get("slot")
            == (None if slot is None else slot.value)
        ]
        return max(attempts, default=0) + 1

    def get_planning_context(self, _):
        return self.context

    def get_run(self, _):
        return StoredRun(
            id=self.run_id,
            content_date=self.finalized["plan"].content_date,
            status=RunStatus.PLANNED.value,
            plan=self.finalized["plan"],
        )

    def replace_episode_plan(self, **kwargs):
        self.replaced = kwargs["episode"]


class RecordingDirector:
    model = "director-model"

    def __init__(self, repository, outputs) -> None:
        self.repository = repository
        self.outputs = outputs
        self.calls = 0

    def generate_structured(self, *, prompt, schema, output_name):
        self.calls += 1
        index = self.calls
        assert schema
        assert f"prompt-{index}" in self.repository.events
        assert f"submitting-{index}" in self.repository.events
        self.repository.events.append(f"gateway-{index}")
        payload = self.outputs[index - 1].model_dump(mode="json")
        return DirectorResult(
            payload=payload,
            response_id=f"response-{index}",
            model=self.model,
            request_hash=hashlib.sha256(prompt.encode()).hexdigest(),
        )


def _director_draft(episode: EpisodePlan) -> EpisodeDirectorDraft:
    return EpisodeDirectorDraft.model_validate(
        episode.model_dump(
            exclude={
                "slot",
                "cast",
                "video_input_mode",
                "required_reference_roles",
            }
        )
    )


def test_planning_uses_one_day_and_three_slot_prompts(daily_plan) -> None:
    repository = RecordingRepository()
    brief = DayBrief(
        content_date=daily_plan.content_date,
        theme=daily_plan.theme,
        day_context=daily_plan.day_context,
        slots=[
            SlotBrief(
                slot=episode.slot,
                narrative_purpose=f"呈现{episode.title}的生活变化",
                scene_direction=episode.scene,
                event_direction=episode.main_event,
                appearance_intent=episode.appearance.description,
            )
            for episode in daily_plan.episodes
        ],
    )
    director = RecordingDirector(
        repository,
        [brief, *(_director_draft(item) for item in daily_plan.episodes)],
    )
    service = PlanningService(
        repository=repository,
        director=director,
        provider_name="test",
    )
    result = service.plan_day(
        target_date=daily_plan.content_date,
        planning_context="普通生活",
        candidate_count=1,
        allow_paid_generation=True,
    )
    assert director.calls == 4
    assert result.candidate_count == 1
    assert repository.finalized is not None
    for index in range(1, 5):
        events = repository.events
        assert events.index(f"intent-{index}") < events.index(f"prompt-{index}")
        assert events.index(f"prompt-{index}") < events.index(f"submitting-{index}")
        assert events.index(f"submitting-{index}") < events.index(f"gateway-{index}")


def test_replan_episode_only_calls_requested_slot_director(daily_plan) -> None:
    repository = RecordingRepository()
    brief = DayBrief(
        content_date=daily_plan.content_date,
        theme=daily_plan.theme,
        day_context=daily_plan.day_context,
        slots=[
            SlotBrief(
                slot=episode.slot,
                narrative_purpose=f"呈现{episode.title}的生活变化",
                scene_direction=episode.scene,
                event_direction=episode.main_event,
                appearance_intent=episode.appearance.description,
            )
            for episode in daily_plan.episodes
        ],
    )
    director = RecordingDirector(
        repository,
        [brief, *(_director_draft(item) for item in daily_plan.episodes)],
    )
    service = PlanningService(
        repository=repository,
        director=director,
        provider_name="test",
    )
    service.plan_day(
        target_date=daily_plan.content_date,
        planning_context="普通生活",
        candidate_count=1,
        allow_paid_generation=True,
    )
    director.outputs.append(_director_draft(daily_plan.episodes[1]))

    result = service.replan_episode(
        repository.run_id,
        slot=Slot.NOON,
        reason="修正中午动作的承重和收束关系",
        allow_paid_generation=True,
    )

    assert director.calls == 5
    assert result.slot is Slot.NOON
    assert result.attempt == 2
    assert repository.replaced.slot is Slot.NOON
