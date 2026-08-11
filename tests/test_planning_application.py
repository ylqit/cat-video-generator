"""生活故事项目、可选总导演与顺序时段镜头化。"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from datetime import date

import pytest

from cat_video_generator.application.planning import (
    PlanningReviewRequired,
    PlanningService,
    ProjectOutlinePause,
    ProjectPlanningResult,
)
from cat_video_generator.application.ports import (
    DirectorResult,
    StoredEpisode,
    StoredRun,
    StoredStep,
)
from cat_video_generator.domain.contracts import (
    AcceptedOutcome,
    EpisodeSources,
    RunCreativeControls,
    SceneRoute,
    Slot,
    StoryConnection,
    StoryConnectionMode,
    StoryInputMode,
    StoryProjectInput,
)
from cat_video_generator.domain.pipeline import PipelineSettings, PlanningMode
from cat_video_generator.domain.visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
)
from cat_video_generator.domain.workflow import EpisodeStatus, RunStatus, StepKind, StepStatus


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
        self.content_date = date(2026, 8, 10)
        self.steps: dict[uuid.UUID, StoredStep] = {}
        self.context: dict = {}
        self.plan = None
        self.run_status = RunStatus.DRAFT.value
        self.pipeline_settings = PipelineSettings()
        self.episodes: list[StoredEpisode] = []
        self.reviews: list[dict] = []

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
        existing = next(
            (
                item
                for item in self.steps.values()
                if item.operation_key == kwargs["operation_key"]
                and item.attempt == kwargs["attempt"]
            ),
            None,
        )
        if existing is not None:
            return existing, uuid.uuid5(existing.id, "prompt")
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
        return step, uuid.uuid5(step.id, "prompt")

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
                "normalization_warnings": list(kwargs["normalization_warnings"]),
                "response_id": kwargs["response_id"],
                "request_hash": kwargs["request_hash"],
            },
        )

    def fail_director_step(self, **kwargs):
        step = self.steps[kwargs["step_id"]]
        self.steps[step.id] = replace(
            step,
            status=StepStatus.FAILED,
            input_snapshot={
                **step.input_snapshot,
                "provider_output": kwargs["provider_output"],
            },
        )

    def fail_step(self, step_id, **kwargs):
        self.steps[step_id] = replace(self.steps[step_id], status=StepStatus.FAILED)

    def get_step(self, step_id):
        return self.steps[step_id]

    def next_director_attempt(self, **kwargs):
        slot = kwargs["slot"]
        operation_key = "director:day" if slot is None else f"director:episode:{slot.value}"
        attempts = [
            step.attempt for step in self.steps.values() if step.operation_key == operation_key
        ]
        return max(attempts, default=0) + 1

    def save_initial_planning_metadata(self, **kwargs):
        self.context = {
            **self.context,
            "planningMetadata": kwargs["planning_metadata"],
            "projectInput": kwargs["project_input"].model_dump(mode="json"),
            "episodeDrafts": kwargs.get("episode_drafts", {}),
        }

    def save_planning_context(self, **kwargs):
        self.context = {
            **self.context,
            "projectInput": kwargs["project_input"].model_dump(mode="json"),
            "projectOutline": kwargs["project_outline"].model_dump(mode="json"),
            "projectOutlineDirectorStepId": str(kwargs["project_outline_step_id"]),
            "projectOutlineDirectorPromptId": str(kwargs["project_outline_prompt_id"]),
            "episodeDrafts": kwargs["episode_drafts"],
            "planningMetadata": kwargs["planning_metadata"],
        }

    def get_planning_context(self, run_id):
        return self.context

    def finalize_plan(self, **kwargs):
        self.plan = kwargs["plan"]
        self.run_status = RunStatus.PLANNED.value
        if not self.episodes:
            for episode in self.plan.episodes:
                self.save_planned_episode(run_id=self.run_id, episode=episode)

    def save_planned_episode(self, **kwargs):
        episode = kwargs["episode"]
        if any(item.plan.slot is episode.slot for item in self.episodes):
            return next(item for item in self.episodes if item.plan.slot is episode.slot)
        stored = StoredEpisode(
            id=uuid.uuid4(),
            run_id=self.run_id,
            plan=episode,
            status=EpisodeStatus.PLANNED,
            selected_video_asset_id=None,
        )
        self.episodes.append(stored)
        drafts = dict(self.context.get("episodeDrafts", {}))
        drafts[episode.slot.value] = episode.script.model_dump(mode="json")
        self.context["episodeDrafts"] = drafts
        self.run_status = RunStatus.PLANNED.value
        return stored

    def list_episodes(self, run_id):
        return tuple(self.episodes)

    def get_run(self, run_id):
        return StoredRun(self.run_id, self.content_date, self.run_status, self.plan)

    def set_run_status(self, run_id, target):
        self.run_status = target.value

    def record_review(self, **kwargs):
        self.reviews.append(kwargs)
        return uuid.uuid4()

    def replace_episode_plan(self, **kwargs):
        index = next(
            i for i, item in enumerate(self.episodes) if item.plan.slot is kwargs["episode"].slot
        )
        old = self.episodes[index]
        self.episodes[index] = replace(old, plan=kwargs["episode"])


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


def existing_script_project(*, complete: bool = True) -> StoryProjectInput:
    return StoryProjectInput(
        theme="出去钓鱼",
        input_mode=StoryInputMode.EPISODE_SCRIPTS,
        scene_route=SceneRoute.PROGRESSIVE_LOCATIONS,
        episode_sources=EpisodeSources(
            morning="家中整理钓竿、水桶和鱼饵，猫咪发现鱼饵盒。",
            noon=("河边钓鱼，猫咪先发现浮标下沉，人物稳定提竿。" if complete else None),
            evening=("夕阳下带着收获归家，人物分享一条小鱼给猫咪。" if complete else None),
        ),
    )


def test_theme_expand_auto_calls_outline_then_three_episode_directors(daily_plan) -> None:
    director = Director(
        [
            daily_plan.outline.model_dump(mode="json"),
            *(item.script.model_dump(mode="json") for item in daily_plan.episodes),
        ]
    )
    repository = PlanningRepository()

    result = service(repository, director).create_project(
        target_date=daily_plan.content_date,
        project_input=daily_plan.project_input,
        allow_paid_generation=True,
        pipeline_settings=PipelineSettings(planningMode=PlanningMode.AUTO_DAY),
    )

    assert isinstance(result, ProjectPlanningResult)
    assert result.plan is not None
    assert result.plan.outline == daily_plan.outline
    assert len(director.prompts) == 4
    assert [item.operation_key for item in repository.steps.values()] == [
        "director:day",
        "director:episode:morning",
        "director:episode:noon",
        "director:episode:evening",
    ]
    assert all(item.kind is StepKind.DIRECTOR for item in repository.steps.values())


def test_existing_scripts_guided_project_creates_no_director_step() -> None:
    director = Director([])
    repository = PlanningRepository()
    project_input = existing_script_project(complete=False)

    result = service(repository, director).create_project(
        target_date=repository.content_date,
        project_input=project_input,
        allow_paid_generation=False,
        pipeline_settings=PipelineSettings(planningMode=PlanningMode.GUIDED_SEQUENTIAL),
    )

    assert isinstance(result, ProjectPlanningResult)
    assert result.outline is None and result.plan is None
    assert director.prompts == []
    assert repository.steps == {}
    assert repository.context["projectInput"]["episode_sources"]["morning"]


def test_existing_scripts_auto_calls_only_three_episode_directors(daily_plan) -> None:
    director = Director([item.script.model_dump(mode="json") for item in daily_plan.episodes])
    repository = PlanningRepository()

    result = service(repository, director).create_project(
        target_date=repository.content_date,
        project_input=existing_script_project(),
        allow_paid_generation=True,
        pipeline_settings=PipelineSettings(planningMode=PlanningMode.AUTO_DAY),
    )

    assert result.plan is not None and result.plan.outline is None
    assert len(director.prompts) == 3
    assert "用户本集原文" in director.prompts[0]
    assert [item.operation_key for item in repository.steps.values()] == [
        "director:episode:morning",
        "director:episode:noon",
        "director:episode:evening",
    ]


def test_theme_expand_guided_pauses_for_project_outline_confirmation(daily_plan) -> None:
    director = Director([daily_plan.outline.model_dump(mode="json")])
    repository = PlanningRepository()

    result = service(repository, director).create_project(
        target_date=daily_plan.content_date,
        project_input=daily_plan.project_input,
        allow_paid_generation=True,
        pipeline_settings=PipelineSettings(planningMode=PlanningMode.GUIDED_SEQUENTIAL),
    )

    assert isinstance(result, ProjectOutlinePause)
    assert result.project_outline.episodes.noon.scene
    assert len(director.prompts) == 1
    assert repository.plan is None


def test_guided_noon_reads_only_enabled_story_connection(daily_plan) -> None:
    director = Director(
        [
            daily_plan.episodes[0].script.model_dump(mode="json"),
            daily_plan.episodes[1].script.model_dump(mode="json"),
        ]
    )
    repository = PlanningRepository()
    planner = service(repository, director)
    planner.create_project(
        target_date=repository.content_date,
        project_input=existing_script_project(),
        allow_paid_generation=False,
        pipeline_settings=PipelineSettings(planningMode=PlanningMode.GUIDED_SEQUENTIAL),
    )

    planner.plan_slot(
        repository.run_id,
        slot=Slot.MORNING,
        allow_paid_generation=True,
    )
    with pytest.raises(ValueError, match="结果卡"):
        planner.plan_slot(
            repository.run_id,
            slot=Slot.NOON,
            allow_paid_generation=True,
        )

    repository.context["acceptedOutcomes"] = {
        "morning": AcceptedOutcome(
            summary="上午实际已在家整理好钓具，猫咪最后回到便携箱旁。",
            carryForward=["同一套钓竿和鱼饵盒"],
            doNotCarryForward=["画面偶发的第二只水桶"],
            confirmedAt="2026-08-10T10:00:00+08:00",
        ).model_dump(mode="json", by_alias=True)
    }
    repository.context["storyConnections"] = {
        "noon": StoryConnection(
            useForDirector=True,
            mode=StoryConnectionMode.SELECTED_LINK,
            brief="只关联上午已整理好的同一套钓竿，转到河边开始钓鱼。",
            confirmedAt="2026-08-10T10:05:00+08:00",
        ).model_dump(mode="json", by_alias=True)
    }
    planner.plan_slot(
        repository.run_id,
        slot=Slot.NOON,
        allow_paid_generation=True,
    )

    assert "只关联上午已整理好的同一套钓竿" in director.prompts[-1]
    assert "上午实际已在家整理好钓具" not in director.prompts[-1]
    assert "第二只水桶" not in director.prompts[-1]
    assert "河边钓鱼" in director.prompts[-1]


def test_invalid_episode_enters_review_without_automatic_paid_repair(daily_plan) -> None:
    invalid = daily_plan.episodes[0].script.model_dump(mode="json")
    invalid.pop("relationship_arc")
    director = Director([invalid])
    repository = PlanningRepository()
    planner = service(repository, director)
    planner.create_project(
        target_date=repository.content_date,
        project_input=existing_script_project(complete=False),
        allow_paid_generation=False,
        pipeline_settings=PipelineSettings(planningMode=PlanningMode.GUIDED_SEQUENTIAL),
        creative_controls=RunCreativeControls(),
    )

    with pytest.raises(PlanningReviewRequired, match="需要人工审核"):
        planner.plan_slot(
            repository.run_id,
            slot=Slot.MORNING,
            allow_paid_generation=True,
        )

    assert len(director.prompts) == 1
    assert len(repository.steps) == 1
    assert next(iter(repository.steps.values())).status is StepStatus.FAILED
