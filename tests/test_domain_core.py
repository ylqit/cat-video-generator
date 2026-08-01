"""Episode单一契约与可见世界状态重放。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cat_video_generator.domain.continuity import EntityTransition, replay_world
from cat_video_generator.domain.contracts import EpisodePlan, EpisodeScript, Slot


def test_episode_has_single_nested_script(daily_plan) -> None:
    payload = daily_plan.episodes[0].model_dump(mode="json")
    assert set(payload) == {"slot", "script"}
    assert "slot" not in payload["script"]
    assert "scene_inventory" not in payload["script"]
    assert "required_reference_roles" not in payload["script"]


def test_state_replay_applies_complete_before_after(daily_plan) -> None:
    episode = daily_plan.episodes[0]
    result = replay_world(episode.script.visible_world, episode.script.actions)
    assert result.valid
    assert result.states["pinwheel"].support_id == "person"
    assert result.states["pinwheel"].anchor_id is None


def test_action_without_state_change_uses_empty_transitions(daily_plan) -> None:
    first = daily_plan.episodes[0].script.actions[0]
    assert first.transitions == []
    assert "no_state_change" not in first.model_dump()


def test_noop_transition_is_rejected(daily_plan) -> None:
    state = daily_plan.episodes[0].script.visible_world.entities[0].initial_state
    with pytest.raises(ValidationError, match="不能创建空Transition"):
        EntityTransition(
            entity_id="person",
            before=state,
            after=state,
            reason="没有变化",
        )


def test_unknown_entity_is_rejected_by_episode_contract(daily_plan) -> None:
    original = daily_plan.episodes[0]
    bad_transition = original.script.actions[1].transitions[0].model_copy(
        update={"entity_id": "missing_prop"}
    )
    actions = [
        original.script.actions[0],
        original.script.actions[1].model_copy(
            update={"transitions": [bad_transition]}
        ),
    ]
    payload = original.script.model_dump(mode="python")
    payload["actions"] = actions
    with pytest.raises(ValidationError, match="未建账实体"):
        EpisodeScript.model_validate(payload)


def test_before_state_must_match_previous_terminal_state(daily_plan) -> None:
    original = daily_plan.episodes[0]
    transition = original.script.actions[1].transitions[0]
    wrong_before = transition.before.model_copy(
        update={"appearance_signature": "另一种纸风车"}
    )
    actions = [
        original.script.actions[0],
        original.script.actions[1].model_copy(
            update={
                "transitions": [transition.model_copy(update={"before": wrong_before})]
            }
        ),
    ]
    payload = original.script.model_dump(mode="python")
    payload["actions"] = actions
    with pytest.raises(ValidationError, match="前序终态不一致"):
        EpisodeScript.model_validate(payload)


def test_each_action_belongs_to_exactly_one_shot(daily_plan) -> None:
    original = daily_plan.episodes[0]
    duplicate = original.script.shots[0].model_copy(update={"order": 2})
    payload = original.script.model_dump(mode="python")
    payload["shots"] = [original.script.shots[0], duplicate]
    with pytest.raises(ValidationError, match="镜头必须按顺序完整覆盖全部动作"):
        EpisodeScript.model_validate(payload)


def test_plan_shared_element_requires_same_semantic_key(daily_plan) -> None:
    brief = daily_plan.day_brief.model_copy(
        update={
            "shared_elements": [
                {
                    "semantic_key": "element:pinwheel",
                    "description": "全天出现的同一只浅蓝色纸风车",
                    "slots": [Slot.MORNING, Slot.NOON, Slot.EVENING],
                }
            ]
        }
    )
    valid = daily_plan.model_copy(update={"day_brief": brief})
    assert valid.episodes[0].script.visible_world.entities[2].semantic_key == (
        "element:pinwheel"
    )


def test_episode_plan_does_not_duplicate_title_or_mode(daily_plan) -> None:
    episode = EpisodePlan.model_validate(daily_plan.episodes[0].model_dump())
    assert episode.title == episode.script.title
    assert episode.video_input_mode == episode.script.video_input_mode
