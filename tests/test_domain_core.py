"""轻量Episode契约与关键实体起终态连续性。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cat_video_generator.domain.continuity import (
    EntityKind,
    EntityLifecycle,
    Placement,
    TrackedEntity,
    validate_continuity,
)
from cat_video_generator.domain.contracts import (
    AppearancePlan,
    DailyProductionPlan,
    EpisodePlan,
    EpisodeScript,
    Slot,
)
from cat_video_generator.domain.rules import hard_failures, validate_episode_against_brief


def test_episode_has_single_nested_script(daily_plan) -> None:
    payload = daily_plan.episodes[0].model_dump(mode="json")
    assert set(payload) == {"slot", "script"}
    assert "slot" not in payload["script"]
    assert "state_changes" not in str(payload)
    assert "continuity" in payload["script"]


def test_actions_keep_director_motion_without_world_state(daily_plan) -> None:
    first = daily_plan.episodes[0].script.actions[0]
    payload = first.model_dump(mode="json")
    assert set(payload) == {"order", "actor_id", "action", "visible_result"}


def test_start_and_end_state_define_only_key_continuity(daily_plan) -> None:
    episode = daily_plan.episodes[0]
    result = validate_continuity(episode.script.continuity)
    assert result.valid
    assert result.final_states["pinwheel"].placement.kind == "held_by"
    assert result.final_states["pinwheel"].placement.target_id == "person"


def test_persist_entity_may_keep_identical_start_and_end(daily_plan) -> None:
    person = daily_plan.episodes[0].script.continuity.entities[0]
    assert person.start_state == person.end_state
    assert person.lifecycle is EntityLifecycle.PERSIST


def test_persist_entity_may_move_without_redundant_reason(daily_plan) -> None:
    prop = daily_plan.episodes[0].script.continuity.entities[2]
    moved = TrackedEntity.model_validate(
        {**prop.model_dump(mode="python"), "change_reason": None}
    )
    assert moved.start_state != moved.end_state
    assert moved.change_reason is None


def test_appearance_change_is_derived_from_change_list() -> None:
    changed = AppearancePlan(
        description="雨后增加一件浅色防水外套",
        changes_from_previous=["增加浅色防水外套"],
        change_reason="傍晚降温且仍有小雨",
    )
    assert changed.changes_from_previous
    assert AppearancePlan(description="延续同一套室内服饰").change_reason is None


def test_appearance_change_without_reason_is_rejected() -> None:
    with pytest.raises(ValidationError, match="外观变化必须"):
        AppearancePlan(
            description="增加一件浅色防水外套",
            changes_from_previous=["增加浅色防水外套"],
        )


def test_persist_prop_cannot_disappear_without_lifecycle(daily_plan) -> None:
    prop = daily_plan.episodes[0].script.continuity.entities[2]
    with pytest.raises(ValidationError, match="persist实体必须在开场和结尾都存在"):
        TrackedEntity.model_validate(
            {
                **prop.model_dump(mode="python"),
                "end_state": {
                    "present": False,
                    "placement": {"kind": "offscreen", "target_id": None},
                },
            }
        )


def test_transform_requires_reason_and_new_form(daily_plan) -> None:
    prop = daily_plan.episodes[0].script.continuity.entities[2]
    payload = {
        **prop.model_dump(mode="python"),
        "lifecycle": "transform",
        "final_form_key": "folded-pinwheel",
        "change_reason": None,
    }
    with pytest.raises(ValidationError, match="transform实体必须说明变化原因"):
        TrackedEntity.model_validate(payload)


@pytest.mark.parametrize("pose", ["crouching-child", "cat-sniffing", "walking-person"])
def test_form_key_rejects_transient_pose(daily_plan, pose: str) -> None:
    person = daily_plan.episodes[0].script.continuity.entities[0]
    with pytest.raises(ValidationError, match="formKey"):
        TrackedEntity.model_validate({**person.model_dump(), "form_key": pose})


def test_unknown_anchor_is_pydantic_reference_error(daily_plan) -> None:
    original = daily_plan.episodes[0]
    payload = original.script.continuity.model_dump(mode="python")
    payload["entities"][2]["end_state"]["placement"] = {
        "kind": "anchor",
        "target_id": "missing_table",
    }
    with pytest.raises(ValidationError, match="未知锚点"):
        original.script.continuity.__class__.model_validate(payload)


def test_background_does_not_need_continuity_entry(daily_plan) -> None:
    episode = daily_plan.episodes[0]
    assert all(
        item.name not in {"屋檐", "植物", "远山", "光影"}
        for item in episode.script.continuity.entities
    )


def test_sitting_text_is_not_parsed_as_physics(daily_plan) -> None:
    original = daily_plan.episodes[0]
    action = original.script.actions[0].model_copy(
        update={"actor_id": "person", "action": "人物在阳台边自然蹲下观察纸风车"}
    )
    episode = original.model_copy(
        update={
            "script": original.script.model_copy(
                update={"actions": [action, *original.script.actions[1:]]}
            )
        }
    )
    issues = validate_episode_against_brief(
        episode,
        day_brief=daily_plan.day_brief,
        slot_brief=daily_plan.day_brief.slots[0],
    )
    assert not hard_failures(issues)


def test_missing_ending_entity_is_rejected(daily_plan) -> None:
    payload = daily_plan.episodes[0].script.model_dump(mode="python")
    payload["ending"]["key_entity_ids"] = ["snail"]
    with pytest.raises(ValidationError, match="结尾引用未登记关键实体或场景锚点"):
        EpisodeScript.model_validate(payload)


def test_registered_anchor_can_be_part_of_visible_ending(daily_plan) -> None:
    original = daily_plan.episodes[0]
    payload = original.script.model_dump(mode="python")
    anchor_id = payload["continuity"]["anchors"][0]["id"]
    payload["ending"]["key_entity_ids"] = ["person", "cat", anchor_id]

    script = EpisodeScript.model_validate(payload)

    assert anchor_id in script.ending.key_entity_ids


def test_more_than_four_ending_entities_is_warning_only(daily_plan) -> None:
    original = daily_plan.episodes[0]
    payload = original.script.model_dump(mode="python")
    base = payload["continuity"]["entities"][2]
    extra_ids = ("ribbon", "bell")
    for entity_id in extra_ids:
        payload["continuity"]["entities"].append(
            {
                **base,
                "id": entity_id,
                "name": f"道具{entity_id}",
                "entity_key": entity_id,
                "form_key": entity_id,
            }
        )
    payload["ending"]["key_entity_ids"] = [
        "person",
        "cat",
        "pinwheel",
        *extra_ids,
    ]
    episode = EpisodePlan(slot=original.slot, script=EpisodeScript.model_validate(payload))
    issues = validate_episode_against_brief(
        episode,
        day_brief=daily_plan.day_brief,
        slot_brief=daily_plan.day_brief.slots[0],
    )
    assert not hard_failures(issues)
    assert any(item.code == "many_ending_entities" for item in issues)


def test_each_action_belongs_to_exactly_one_shot(daily_plan) -> None:
    original = daily_plan.episodes[0]
    duplicate = original.script.shots[0].model_copy(
        update={"order": 2, "action_orders": [2]}
    )
    payload = original.script.model_dump(mode="python")
    payload["shots"] = [original.script.shots[0], duplicate]
    with pytest.raises(ValidationError, match="每个动作只能属于一个镜头"):
        EpisodeScript.model_validate(payload)


@pytest.mark.parametrize(
    ("lifecycle", "start_present", "end_present", "final_form"),
    [
        ("exit", True, False, None),
        ("consume", True, False, None),
        ("enter", False, True, None),
        ("transform", True, True, "folded-paper"),
    ],
)
def test_explicit_lifecycle_changes_are_valid(
    daily_plan,
    lifecycle: str,
    start_present: bool,
    end_present: bool,
    final_form: str | None,
) -> None:
    original = daily_plan.episodes[0].script.continuity.entities[2]
    start = original.start_state.model_copy(
        update={
            "present": start_present,
            "placement": (
                Placement(kind="anchor", target_id="table")
                if start_present
                else Placement(kind="offscreen", target_id=None)
            ),
        }
    )
    end = original.end_state.model_copy(
        update={
            "present": end_present,
            "placement": (
                Placement(kind="held_by", target_id="person")
                if end_present
                else Placement(kind="offscreen", target_id=None)
            ),
        }
    )
    entity = TrackedEntity(
        id="paper",
        name="彩纸",
        kind=EntityKind.PROP,
        entity_key="paper",
        start_state=start,
        end_state=end,
        lifecycle=lifecycle,
        form_key="flat-paper",
        final_form_key=final_form,
        change_reason="动作明确造成该生命周期变化",
    )
    assert entity.lifecycle.value == lifecycle


def test_shared_element_uses_logical_entity_key(daily_plan) -> None:
    payload = daily_plan.day_brief.model_dump(mode="python")
    payload["shared_elements"] = [
        {
            "entity_key": "pinwheel",
            "description": "全天出现的同一只浅蓝色纸风车",
            "slots": [Slot.MORNING, Slot.NOON, Slot.EVENING],
        }
    ]
    brief = daily_plan.day_brief.__class__.model_validate(payload)
    valid = DailyProductionPlan(day_brief=brief, episodes=daily_plan.episodes)
    assert valid.episodes[0].script.continuity.entities[2].entity_key == "pinwheel"


def test_episode_plan_does_not_duplicate_title_or_render_mode(daily_plan) -> None:
    episode = EpisodePlan.model_validate(daily_plan.episodes[0].model_dump())
    assert episode.title == episode.script.title
    assert "video_input_mode" not in episode.script.model_dump()
