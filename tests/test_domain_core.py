"""猫咪主活动、分层时长与全天交接的领域契约。"""

from __future__ import annotations

from copy import deepcopy

import pytest
from conftest import episode_for
from pydantic import ValidationError

from cat_video_generator.domain.contracts import (
    ActivityFocus,
    ActivityFocusMode,
    DailyProductionPlan,
    DurationBand,
    DurationIntent,
    DurationMode,
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


def test_fixed_duration_intent_cannot_be_rewritten() -> None:
    with pytest.raises(ValidationError, match="固定时长档不得被总导演改写"):
        DurationIntent(
            requested_mode=DurationMode.SHORT,
            resolved_band=DurationBand.MEDIUM,
            resolution_reason="错误地把用户固定短片改成了中片",
        )


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


def test_each_action_may_belong_to_only_one_shot() -> None:
    payload = episode_for(Slot.MORNING).script.model_dump(mode="json")
    payload["shots"][1]["action_orders"] = [2, 3]

    with pytest.raises(ValidationError, match="每个动作只能属于一个镜头"):
        EpisodeScript.model_validate(payload)


def test_unknown_actor_is_rejected_without_keyword_story_policing() -> None:
    payload = episode_for(Slot.MORNING).script.model_dump(mode="json")
    payload["actions"][0]["actor_id"] = "vendor"

    with pytest.raises(ValidationError, match="动作引用未知主体"):
        EpisodeScript.model_validate(payload)


def test_handoff_requires_same_prop_key_in_source_and_target(daily_plan) -> None:
    payload = daily_plan.model_dump(mode="json")
    payload["episodes"][1]["script"]["critical_props"][0]["entity_key"] = "different_kite"

    with pytest.raises(ValidationError, match="没有在noon时段使用同一entityKey"):
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
