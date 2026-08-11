"""生活故事工作台的剧本、项目大纲与五阶段设置。"""

from __future__ import annotations

import uuid

from cat_video_generator.application.ports import StoredEpisode, StoredRun
from cat_video_generator.application.studio_editing import StudioEditingService
from cat_video_generator.domain.contracts import ProjectOutlineV3, Slot
from cat_video_generator.domain.pipeline import PipelineSettings
from cat_video_generator.domain.visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
)
from cat_video_generator.domain.workflow import EpisodeStatus


class StudioRepository:
    def __init__(self, plan) -> None:
        self.run_id = uuid.uuid4()
        self.episode_ids = {slot: uuid.uuid4() for slot in Slot}
        self.plan = plan
        self.settings = PipelineSettings()
        self.replacement = None
        self.updated_project_outline = None
        self.context = {
            "projectInput": plan.project_input.model_dump(mode="json"),
            "projectOutline": plan.outline.model_dump(mode="json"),
            "planningMetadata": {},
        }

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
        return self.context

    def get_run(self, run_id):
        return StoredRun(self.run_id, self.plan.content_date, "planned", self.plan)

    def list_episodes(self, run_id):
        if self.plan is None:
            return ()
        return tuple(
            StoredEpisode(
                id=self.episode_ids[item.slot],
                run_id=self.run_id,
                plan=item,
                status=EpisodeStatus.PLANNED,
                selected_video_asset_id=None,
            )
            for item in self.plan.episodes
        )

    def replace_episode_plan(self, **kwargs):
        self.replacement = kwargs

    def update_project_outline(self, **kwargs):
        self.updated_project_outline = kwargs["project_outline"]

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


def test_episode_editor_updates_only_the_episode_contract(daily_plan) -> None:
    repository = StudioRepository(daily_plan)
    payload = daily_plan.episodes[0].script.model_dump(mode="json")
    payload["relationship_arc"] = (
        "人物先完成桌面制作，灰白猫对彩带产生独立反应，最后回到人物手边形成关系汇合。"
    )

    result = service(repository).update_episode_script(
        repository.episode_ids[Slot.MORNING],
        payload,
    )

    assert result["saved"] is True
    assert repository.replacement["episode"].script.relationship_arc == payload["relationship_arc"]
    assert repository.replacement["project_outline"] == daily_plan.outline


def test_pipeline_settings_round_trip_uses_project_outline_stage(daily_plan) -> None:
    repository = StudioRepository(daily_plan)
    result = service(repository).update_pipeline_settings(
        repository.run_id,
        {
            "planningMode": "guided_sequential",
            "allowPaidGeneration": True,
            "projectOutline": "manual",
            "script": "auto",
            "visual": "manual",
            "video": "manual",
            "review": "manual",
        },
    )

    assert result["pipelineSettings"] == {
        "planningMode": "guided_sequential",
        "allowPaidGeneration": True,
        "projectOutline": "manual",
        "script": "auto",
        "visual": "manual",
        "video": "manual",
        "review": "manual",
    }


def test_project_outline_confirmation_uses_v3_named_episodes(daily_plan) -> None:
    repository = StudioRepository(daily_plan)
    repository.plan = None
    repository.get_run = lambda run_id: StoredRun(
        repository.run_id,
        daily_plan.content_date,
        "draft",
        None,
    )

    result = service(repository).update_project_outline(
        repository.run_id,
        daily_plan.outline.model_dump(mode="json"),
    )

    assert result["episodeDraftsCleared"] is True
    assert isinstance(repository.updated_project_outline, ProjectOutlineV3)
    assert repository.updated_project_outline.episodes.noon.scene
