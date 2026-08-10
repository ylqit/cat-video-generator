"""统一Web工作台的剧本编辑、时长同步和五阶段设置。"""

from __future__ import annotations

import uuid

from cat_video_generator.application.ports import StoredRun
from cat_video_generator.application.studio_editing import StudioEditingService
from cat_video_generator.domain.contracts import ActivityFocus, DayBrief, Slot
from cat_video_generator.domain.pipeline import PipelineSettings
from cat_video_generator.domain.visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
)


class StudioRepository:
    def __init__(self, plan) -> None:
        self.run_id = uuid.uuid4()
        self.episode_ids = {slot: uuid.uuid4() for slot in Slot}
        self.plan = plan
        self.settings = PipelineSettings()
        self.replacement = None
        self.updated_day_brief = None

    def episode_detail(self, episode_id):
        slot = next(slot for slot, value in self.episode_ids.items() if value == episode_id)
        episode = next(item for item in self.plan.episodes if item.slot is slot)
        return {
            "id": str(episode_id),
            "runId": str(self.run_id),
            "slot": slot.value,
            "status": "planned",
            "script": episode.script.model_dump(mode="json"),
            "promptOverrides": {},
        }

    def get_planning_context(self, run_id):
        return {"dayBrief": self.plan.day_brief.model_dump(mode="json")}

    def get_run(self, run_id):
        return StoredRun(self.run_id, self.plan.content_date, "planned", self.plan)

    def replace_episode_plan(self, **kwargs):
        self.replacement = kwargs

    def update_day_brief(self, **kwargs):
        self.updated_day_brief = kwargs["day_brief"]

    def save_pipeline_settings(self, **kwargs):
        self.settings = kwargs["settings"]

    def get_pipeline_settings(self, run_id):
        return self.settings


def service(repository) -> StudioEditingService:
    return StudioEditingService(
        repository=repository,
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
        video_resolution="720p",
    )


def test_episode_editor_synchronizes_focus_and_duration_to_day_brief(daily_plan) -> None:
    repository = StudioRepository(daily_plan)
    payload = daily_plan.episodes[0].script.model_dump(mode="json")
    payload["activity_focus"] = "balanced"
    payload["duration_seconds"] = 20

    result = service(repository).update_episode_script(
        repository.episode_ids[Slot.MORNING],
        payload,
    )

    assert result["saved"] is True
    saved_brief = repository.replacement["day_brief"].slot_briefs[0]
    assert saved_brief.resolved_activity_focus is ActivityFocus.BALANCED
    assert saved_brief.duration_intent.resolved_band.value == "medium"
    assert repository.replacement["episode"].duration_seconds == 20


def test_pipeline_settings_round_trip_uses_new_five_stages(daily_plan) -> None:
    repository = StudioRepository(daily_plan)
    result = service(repository).update_pipeline_settings(
        repository.run_id,
        {
            "allowPaidGeneration": True,
            "dayBrief": "manual",
            "script": "auto",
            "visual": "manual",
            "video": "manual",
            "review": "manual",
        },
    )

    assert result["pipelineSettings"] == {
        "allowPaidGeneration": True,
        "dayBrief": "manual",
        "script": "auto",
        "visual": "manual",
        "video": "manual",
        "review": "manual",
    }


def test_draft_day_brief_edit_keeps_slot_controls(daily_plan) -> None:
    repository = StudioRepository(daily_plan)
    repository.plan = None
    repository.get_run = lambda run_id: StoredRun(
        repository.run_id,
        daily_plan.content_date,
        "draft",
        None,
    )

    result = service(repository).update_day_brief(
        repository.run_id,
        daily_plan.day_brief.model_dump(mode="json"),
    )

    assert result["episodeDraftsCleared"] is True
    assert isinstance(repository.updated_day_brief, DayBrief)
    assert [item.slot for item in repository.updated_day_brief.slot_briefs] == list(Slot)
