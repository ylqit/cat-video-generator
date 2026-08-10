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
    RunCreativeControls,
    Slot,
    SlotCreativeControl,
)


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


def test_long_episode_requires_three_shots() -> None:
    payload = episode_for(Slot.MORNING, duration=22).script.model_dump(mode="json")
    payload["duration_seconds"] = 36

    with pytest.raises(ValidationError, match="没有足够的连续镜头"):
        EpisodeScript.model_validate(payload)


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
    payload["day_brief"]["handoffs"].append(
        deepcopy(payload["day_brief"]["handoffs"][0])
    )

    with pytest.raises(ValidationError, match="同一时段交接不能重复"):
        DailyProductionPlan.model_validate(payload)


def test_episode_focus_and_duration_must_match_day_brief(daily_plan) -> None:
    payload = deepcopy(daily_plan.model_dump(mode="json"))
    payload["episodes"][0]["script"]["activity_focus"] = "person_lead"

    with pytest.raises(ValidationError, match="morning活动焦点与DayBrief不一致"):
        DailyProductionPlan.model_validate(payload)


def test_episode_plan_keeps_slot_outside_script() -> None:
    plan = episode_for(Slot.MORNING)
    script_payload = plan.script.model_dump(mode="json")

    assert "slot" not in script_payload
    assert EpisodePlan(slot=Slot.MORNING, script=plan.script).title == plan.title
