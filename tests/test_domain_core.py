"""极简导演契约、分层时长与全天边界的领域测试。"""

from __future__ import annotations

from copy import deepcopy

import pytest
from conftest import episode_for
from pydantic import ValidationError

from cat_video_generator.domain.contracts import (
    ActivityFocus,
    ActivityFocusMode,
    DailyProductionPlan,
    EpisodePlan,
    EpisodeScript,
    EpisodeSources,
    ProjectOutlineV3,
    RunCreativeControls,
    SceneRoute,
    Slot,
    SlotCreativeControl,
    StoryInputMode,
    StoryProjectInput,
)
from cat_video_generator.domain.user_story import preview_user_story


def test_creative_controls_default_to_cat_lead_and_inherit_per_slot() -> None:
    controls = RunCreativeControls()

    assert controls.default_activity_focus is ActivityFocusMode.CAT_LEAD
    assert [item.slot for item in controls.slot_controls] == list(Slot)
    assert all(controls.requested_focus(slot) is ActivityFocusMode.CAT_LEAD for slot in Slot)


def test_slot_focus_override_does_not_change_other_slots() -> None:
    controls = RunCreativeControls(
        slot_controls=[
            SlotCreativeControl(slot=Slot.MORNING),
            SlotCreativeControl(slot=Slot.NOON, activity_focus=ActivityFocusMode.BALANCED),
            SlotCreativeControl(slot=Slot.EVENING),
        ]
    )

    assert controls.requested_focus(Slot.NOON) is ActivityFocusMode.BALANCED
    assert controls.requested_focus(Slot.MORNING) is ActivityFocusMode.CAT_LEAD


@pytest.mark.parametrize(
    ("seconds", "expected_shots"),
    [(12, 2), (22, 2), (36, 3)],
)
def test_episode_supports_short_medium_and_long(seconds: int, expected_shots: int) -> None:
    episode = episode_for(Slot.MORNING, duration=seconds)

    assert episode.duration_seconds == seconds
    assert len(episode.script.shots) == expected_shots
    assert episode.script.activity_focus is ActivityFocus.CAT_LEAD


def test_long_episode_does_not_use_shot_count_as_a_hard_gate() -> None:
    payload = episode_for(Slot.MORNING, duration=22).script.model_dump(mode="json")
    payload["duration_seconds"] = 36

    script = EpisodeScript.model_validate(payload)

    assert script.duration_seconds == 36
    assert len(script.shots) == 2


def test_shots_are_complete_text_instead_of_repeated_action_fields() -> None:
    script = episode_for(Slot.NOON, duration=22).script

    assert all(shot.direction for shot in script.shots)
    fields = type(script).model_fields
    assert "story_text" in fields
    assert "actions" not in fields
    assert "critical_props" not in fields
    assert "interaction_constraints" not in fields


def test_hard_constraint_must_reference_existing_shots() -> None:
    payload = episode_for(Slot.NOON, duration=22).script.model_dump(mode="json")
    payload["hard_constraints"][0]["shot_orders"] = [1, 3]

    with pytest.raises(ValidationError, match="硬约束引用不存在的镜头"):
        EpisodeScript.model_validate(payload)


def test_duplicate_handoff_is_rejected(daily_plan) -> None:
    payload = daily_plan.model_dump(mode="json")
    payload["outline"]["handoffs"].append(
        deepcopy(payload["outline"]["handoffs"][0])
    )

    with pytest.raises(ValidationError, match="同一时段交接不能重复"):
        DailyProductionPlan.model_validate(payload)


def test_outline_does_not_duplicate_episode_focus_or_duration(daily_plan) -> None:
    payload = deepcopy(daily_plan.model_dump(mode="json"))
    payload["episodes"][0]["script"]["activity_focus"] = "person_lead"

    plan = DailyProductionPlan.model_validate(payload)

    assert plan.episodes[0].script.activity_focus is ActivityFocus.PERSON_LEAD


def test_existing_scripts_project_can_start_with_only_morning_source() -> None:
    project = StoryProjectInput(
        theme="出去钓鱼",
        input_mode=StoryInputMode.EPISODE_SCRIPTS,
        scene_route=SceneRoute.PROGRESSIVE_LOCATIONS,
        episode_sources=EpisodeSources(morning="在家中整理钓具，猫咪发现鱼饵盒。"),
    )

    assert project.episode_sources.populated_slots == (Slot.MORNING,)


def test_project_outline_uses_named_episodes_and_rejects_a_fourth_slot(daily_plan) -> None:
    payload = daily_plan.outline.model_dump(mode="json")
    payload["episodes"]["night"] = {
        "scene": "夜间房间",
        "direction": "额外的第四时段不属于生产契约。",
    }

    with pytest.raises(ValidationError, match="Extra inputs"):
        ProjectOutlineV3.model_validate(payload)


def test_story_preview_returns_issues_before_strict_confirmation() -> None:
    preview = preview_user_story("主题：钓鱼\n剧本1：短\n剧本2：河边钓鱼\n剧本3：归家")

    assert not (preview.complete and not preview.issues)
    assert preview.issues


def test_episode_plan_keeps_slot_outside_script() -> None:
    plan = episode_for(Slot.MORNING)
    script_payload = plan.script.model_dump(mode="json")

    assert "slot" not in script_payload
    assert EpisodePlan(slot=Slot.MORNING, script=plan.script).title == plan.title
