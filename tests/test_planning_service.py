from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace

import pytest

from cat_video_generator.application.planning import (
    PlanningReviewRequired,
    PlanningService,
)
from cat_video_generator.application.ports import (
    DirectorResult,
    GatewayError,
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
from cat_video_generator.domain.visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
)
from cat_video_generator.domain.workflow import RunStatus, StepKind, StepStatus


class RecordingRepository:
    def __init__(self) -> None:
        self.run_id = uuid.uuid4()
        self.events: list[str] = []
        self.steps: dict[uuid.UUID, StoredStep] = {}
        self.finalized = None
        self.context = {}
        self.run_status = RunStatus.DRAFT
        self.content_date = None
        self.reviews = []

    def create_draft_run(self, content_date):
        self.events.append("create_run")
        self.content_date = content_date
        return self.run_id

    def list_recent_completed_summaries(self, *, limit):
        assert limit == 6
        return ()

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

    def fail_step(self, step_id, **kwargs):
        self.events.append(f"failed-{kwargs['code']}")
        self.steps[step_id] = replace(
            self.steps[step_id],
            status=(
                StepStatus.SUBMISSION_UNKNOWN
                if kwargs.get("submission_unknown")
                else StepStatus.FAILED
            ),
        )

    def fail_director_step(self, **kwargs):
        step_id = kwargs["step_id"]
        self.events.append(f"failed-{kwargs['code']}")
        self.steps[step_id] = replace(
            self.steps[step_id],
            status=StepStatus.FAILED,
            request_summary={
                **self.steps[step_id].request_summary,
                "rejectedDirectorOutput": kwargs["output"],
                "responseId": kwargs["response_id"],
            },
        )

    def set_run_status(self, _, target):
        self.run_status = target

    def record_review(self, **kwargs):
        self.reviews.append(kwargs)
        return uuid.uuid4()

    def finalize_plan(self, **kwargs):
        self.finalized = kwargs

    def save_planning_context(self, **kwargs):
        self.context = {
            "dayBrief": kwargs["day_brief"].model_dump(mode="json"),
            "dayDirectorStepId": str(kwargs["day_step_id"]),
            "dayDirectorPromptId": str(kwargs["day_prompt_id"]),
            "episodeDrafts": kwargs["episode_drafts"],
            "planningMetadata": kwargs["planning_metadata"],
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
        plan = None if self.finalized is None else self.finalized["plan"]
        return StoredRun(
            id=self.run_id,
            content_date=self.content_date,
            status=self.run_status.value,
            plan=plan,
        )

    def replace_episode_plan(self, **kwargs):
        self.replaced = kwargs["episode"]


class RecordingDirector:
    model = "director-model"

    def __init__(self, repository, outputs) -> None:
        self.repository = repository
        self.outputs = outputs
        self.calls = 0
        self.prompts = []

    def generate_structured(self, *, prompt, schema, output_name):
        self.calls += 1
        self.prompts.append(prompt)
        index = self.calls
        assert schema
        assert f"prompt-{index}" in self.repository.events
        assert f"submitting-{index}" in self.repository.events
        self.repository.events.append(f"gateway-{index}")
        output = self.outputs[index - 1]
        payload = (
            output
            if isinstance(output, dict)
            else output.model_dump(mode="json")
        )
        return DirectorResult(
            payload=payload,
            response_id=f"response-{index}",
            model=self.model,
            request_hash=hashlib.sha256(prompt.encode()).hexdigest(),
        )


class FailingDirector:
    model = "director-model"

    def generate_structured(self, **_):
        raise GatewayError(
            "结构化输出未完成",
            code="director_incomplete_max_output_tokens",
            retryable=True,
        )


class BriefThenUnknownDirector:
    model = "director-model"

    def __init__(self, brief: DayBrief) -> None:
        self.brief = brief
        self.calls = 0

    def generate_structured(self, *, prompt, **_):
        self.calls += 1
        if self.calls == 1:
            return DirectorResult(
                payload=self.brief.model_dump(mode="json"),
                response_id="day-response",
                model=self.model,
                request_hash=hashlib.sha256(prompt.encode()).hexdigest(),
            )
        raise GatewayError(
            "导演请求结果未知",
            code="transport_interrupted",
            retryable=False,
            submission_unknown=True,
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
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
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


def test_initial_planning_failure_closes_draft_run(daily_plan) -> None:
    repository = RecordingRepository()
    service = PlanningService(
        repository=repository,
        director=FailingDirector(),
        provider_name="test",
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
    )
    with pytest.raises(GatewayError, match="未完成"):
        service.plan_day(
            target_date=daily_plan.content_date,
            planning_context="普通生活",
            candidate_count=1,
            allow_paid_generation=True,
        )
    assert repository.run_status is RunStatus.FAILED
    assert any(item.startswith("failed-director_incomplete") for item in repository.events)


def test_resume_planning_reuses_day_brief_and_only_calls_missing_slots(
    daily_plan,
) -> None:
    repository = RecordingRepository()
    repository.content_date = daily_plan.content_date
    repository.run_status = RunStatus.FAILED
    day_step_id = uuid.uuid4()
    day_prompt_id = uuid.uuid4()
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
    repository.context = {
        "dayBrief": brief.model_dump(mode="json"),
        "dayDirectorStepId": str(day_step_id),
        "dayDirectorPromptId": str(day_prompt_id),
        "episodeDrafts": {},
        "planningMetadata": {"recentSummaries": []},
    }
    director = RecordingDirector(
        repository,
        [_director_draft(item) for item in daily_plan.episodes],
    )
    service = PlanningService(
        repository=repository,
        director=director,
        provider_name="test",
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
    )
    result = service.resume_planning(
        repository.run_id,
        allow_paid_generation=True,
    )
    assert director.calls == 3
    assert result.plan.content_date == daily_plan.content_date
    assert repository.finalized is not None


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
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
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


def test_episode_contradiction_is_repaired_once_then_planning_continues(
    daily_plan,
) -> None:
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
    bad_morning = _director_draft(daily_plan.episodes[0]).model_dump(mode="json")
    bad_morning["visible_world"]["action_transitions"][0]["actor_id"] = "guest"
    director = RecordingDirector(
        repository,
        [
            brief,
            bad_morning,
            _director_draft(daily_plan.episodes[0]),
            _director_draft(daily_plan.episodes[1]),
            _director_draft(daily_plan.episodes[2]),
        ],
    )
    service = PlanningService(
        repository=repository,
        director=director,
        provider_name="test",
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
    )

    result = service.plan_day(
        target_date=daily_plan.content_date,
        planning_context="普通生活",
        candidate_count=1,
        allow_paid_generation=True,
    )

    assert result.plan.content_date == daily_plan.content_date
    assert director.calls == 5
    assert "被拒绝候选" in director.prompts[2]
    repair_steps = [
        step
        for step in repository.steps.values()
        if step.request_summary.get("directorRepairAttempted")
    ]
    assert len(repair_steps) == 1
    assert len(repository.reviews) == 1


def test_prompt_budget_failure_enters_same_director_repair_loop(
    daily_plan,
) -> None:
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
    verbose = _director_draft(daily_plan.episodes[0]).model_dump(mode="json")
    verbose["scene"] = "连续生活空间" + "环境细节" * 70
    verbose["appearance"]["description"] = "本时段自然穿着" + "服装细节" * 60
    for action in verbose["actions"]:
        action["action"] = "同一主事件中的连续动作" + "可见变化" * 55
        action["visible_result"] = "动作产生明确结果" + "结果细节" * 35
    for shot in verbose["shots"]:
        shot["direction"] = "保持单一运镜并展示动作" + "镜头细节" * 35
    director = RecordingDirector(
        repository,
        [
            brief,
            verbose,
            _director_draft(daily_plan.episodes[0]),
            _director_draft(daily_plan.episodes[1]),
            _director_draft(daily_plan.episodes[2]),
        ],
    )
    service = PlanningService(
        repository=repository,
        director=director,
        provider_name="test",
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
    )

    result = service.plan_day(
        target_date=daily_plan.content_date,
        planning_context="普通生活",
        candidate_count=1,
        allow_paid_generation=True,
    )

    assert result.plan.content_date == daily_plan.content_date
    assert director.calls == 5
    assert "视频Prompt超过" in director.prompts[2]


def test_second_episode_contradiction_enters_planning_review(daily_plan) -> None:
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
    bad = _director_draft(daily_plan.episodes[0]).model_dump(mode="json")
    bad["visible_world"]["action_transitions"][0]["actor_id"] = "guest"
    director = RecordingDirector(repository, [brief, bad, bad])
    service = PlanningService(
        repository=repository,
        director=director,
        provider_name="test",
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
    )

    with pytest.raises(PlanningReviewRequired, match="自动修复后仍不自洽"):
        service.plan_day(
            target_date=daily_plan.content_date,
            planning_context="普通生活",
            candidate_count=1,
            allow_paid_generation=True,
        )

    assert director.calls == 3
    assert repository.run_status is RunStatus.PLANNING_REVIEW
    assert repository.finalized is None


def test_submission_unknown_never_triggers_director_repair(daily_plan) -> None:
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
    director = BriefThenUnknownDirector(brief)
    service = PlanningService(
        repository=repository,
        director=director,
        provider_name="test",
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
    )

    with pytest.raises(RuntimeError, match="禁止重复调用"):
        service.plan_day(
            target_date=daily_plan.content_date,
            planning_context="普通生活",
            candidate_count=1,
            allow_paid_generation=True,
        )

    assert director.calls == 2
    assert not any(
        step.request_summary.get("directorRepairAttempted")
        for step in repository.steps.values()
    )


def test_manual_replan_moves_incomplete_review_run_back_to_draft(
    daily_plan,
) -> None:
    repository = RecordingRepository()
    repository.content_date = daily_plan.content_date
    repository.run_status = RunStatus.PLANNING_REVIEW
    day_step_id = uuid.uuid4()
    day_prompt_id = uuid.uuid4()
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
    repository.context = {
        "dayBrief": brief.model_dump(mode="json"),
        "dayDirectorStepId": str(day_step_id),
        "dayDirectorPromptId": str(day_prompt_id),
        "episodeDrafts": {},
        "planningMetadata": {"recentSummaries": []},
    }
    director = RecordingDirector(
        repository,
        [_director_draft(daily_plan.episodes[0])],
    )
    service = PlanningService(
        repository=repository,
        director=director,
        provider_name="test",
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
    )

    result = service.replan_episode(
        repository.run_id,
        slot=Slot.MORNING,
        reason="修复人物进入路径与场景地面之间的状态关系",
        allow_paid_generation=True,
    )

    assert result.slot is Slot.MORNING
    assert repository.run_status is RunStatus.DRAFT
