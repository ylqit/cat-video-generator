from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from cat_video_generator.domain.contracts import (
    CriticalRelation,
    DailyProductionPlan,
    DayBrief,
    EpisodeDirectorDraft,
    GenerationStrategy,
    MediaBinding,
    MediaModality,
    MediaPurpose,
    ProviderMediaRole,
    SegmentPlan,
    SlotBrief,
    VideoInputMode,
    VideoInputPlan,
)
from cat_video_generator.domain.prompts import (
    PromptBudgetError,
    PromptCompilationError,
    compile_day_director_prompt,
    compile_episode_director_prompt,
    compile_segment_video_prompt,
    compile_video_prompt,
)
from cat_video_generator.domain.rules import select_video_input_mode


def _input_plan(
    daily_plan: DailyProductionPlan,
    *,
    slot_index: int,
) -> VideoInputPlan:
    episode = daily_plan.episodes[slot_index]
    if episode.video_input_mode is VideoInputMode.STRICT_FIRST_FRAME:
        sources = (
            (
                "first_frame",
                MediaPurpose.SEMANTIC_OPENING,
                ProviderMediaRole.FIRST_FRAME,
            ),
        )
    elif episode.video_input_mode is VideoInputMode.STRICT_FIRST_LAST:
        sources = (
            (
                "first_frame",
                MediaPurpose.SEMANTIC_OPENING,
                ProviderMediaRole.FIRST_FRAME,
            ),
            (
                "last_frame",
                MediaPurpose.SEMANTIC_ENDING,
                ProviderMediaRole.LAST_FRAME,
            ),
        )
    else:
        sources = (
            (
                "person",
                MediaPurpose.IDENTITY,
                ProviderMediaRole.REFERENCE_IMAGE,
            ),
            (
                "cat",
                MediaPurpose.IDENTITY,
                ProviderMediaRole.REFERENCE_IMAGE,
            ),
            (
                "style",
                MediaPurpose.STYLE,
                ProviderMediaRole.REFERENCE_IMAGE,
            ),
        )
    return VideoInputPlan(
        model="doubao-seedance-2-0-mini-260615",
        input_mode=episode.video_input_mode,
        resolution="720p",
        duration_seconds=episode.duration_seconds,
        bindings=[
            MediaBinding(
                asset_id=uuid.uuid4(),
                source_role=role,
                modality=MediaModality.IMAGE,
                purpose=purpose,
                provider_role=provider_role,
                ordinal=index,
                prompt_alias=f"@图片{index}",
                sha256=f"{index}" * 64,
            )
            for index, (role, purpose, provider_role) in enumerate(
                sources,
                start=1,
            )
        ],
    )


def test_daily_plan_keeps_fixed_slot_order(
    daily_plan: DailyProductionPlan,
) -> None:
    data = daily_plan.model_dump()
    data["episodes"] = list(reversed(data["episodes"]))
    with pytest.raises(ValidationError, match="morning"):
        DailyProductionPlan.model_validate(data)


def test_appearance_can_change_shoes_and_remove_bag_for_story(
    daily_plan: DailyProductionPlan,
) -> None:
    data = daily_plan.model_dump()
    data["episodes"][1]["appearance"] = {
        "description": "进入室内后换成拖鞋，不携带户外背包",
        "changes_from_previous": ["户外鞋换成拖鞋"],
        "change_reason": "中午活动进入需要换鞋的室内空间",
    }
    data["episodes"][2]["appearance"] = {
        "description": "离开室内后恢复户外鞋并增加薄外套",
        "changes_from_previous": ["拖鞋换回户外鞋", "增加薄外套"],
        "change_reason": "傍晚回到室外且风力增强",
    }
    plan = DailyProductionPlan.model_validate(data)
    assert "拖鞋" in plan.episodes[1].appearance.description
    assert "背包" in plan.episodes[1].appearance.description


def test_undeclared_appearance_change_is_rejected(
    daily_plan: DailyProductionPlan,
) -> None:
    data = daily_plan.model_dump()
    data["episodes"][1]["appearance"] = {
        "description": "突然改成另一套衣服",
        "continuity": "changed",
        "changes_from_previous": [],
        "change_reason": None,
    }
    with pytest.raises(ValidationError, match="必须说明情景原因"):
        DailyProductionPlan.model_validate(data)


def test_video_prompt_is_focused_and_uses_shot_order(
    daily_plan: DailyProductionPlan,
) -> None:
    prompt = compile_video_prompt(
        daily_plan.episodes[1],
        input_plan=_input_plan(daily_plan, slot_index=1),
    )
    for heading in (
        "【输出、画风与素材绑定】",
        "【顺序动作】",
        "【可见世界状态与切镜连续性】",
    ):
        assert heading in prompt.text
    assert "镜头1" in prompt.text
    assert "00:" not in prompt.text
    assert "@图片1" in prompt.text
    assert "<主体1>" not in prompt.text
    assert "人物执行" in prompt.text
    assert "纯黑竖椭圆眼睛" not in prompt.text
    assert "眼白、虹膜" not in prompt.text
    assert prompt.char_count <= 1400


def test_episode_draft_normalizes_unambiguous_provider_shape(daily_plan) -> None:
    payload = daily_plan.episodes[0].model_dump(
        exclude={
            "slot",
            "cast",
            "video_input_mode",
            "required_reference_roles",
        }
    )
    payload["shot_boundary_states"] = payload["visible_world"].pop(
        "shot_boundary_states"
    )
    for transition in payload["visible_world"]["action_transitions"]:
        transition["continuous_shot"] = False
    draft = EpisodeDirectorDraft.model_validate(payload)
    assert draft.visible_world.shot_boundary_states
    assert all(
        item.continuous_shot for item in draft.visible_world.action_transitions
    )


def test_video_prompt_over_budget_never_gets_truncated(
    daily_plan: DailyProductionPlan,
) -> None:
    episode = daily_plan.episodes[1].model_copy(
        update={
            "scene": "场景细节" * 75,
            "ending": "有效收束动作" * 35,
            "actions": [
                stage.model_copy(
                    update={
                        "action": "连续可见动作" * 40,
                        "visible_result": "明确画面变化" * 25,
                    }
                )
                for stage in daily_plan.episodes[1].actions
            ],
        }
    )
    with pytest.raises(PromptBudgetError, match="重新规划|去重"):
        compile_video_prompt(
            episode,
            input_plan=_input_plan(daily_plan, slot_index=1),
        )


def test_director_prompts_split_day_and_one_episode(
    daily_plan: DailyProductionPlan,
) -> None:
    day_prompt = compile_day_director_prompt(
        target_date=daily_plan.content_date,
        planning_context="普通城市周末",
    )
    assert "只输出一个" in day_prompt
    assert "不要替时段导演写具体动作时间线" in day_prompt
    brief = DayBrief(
        content_date=daily_plan.content_date,
        theme=daily_plan.theme,
        day_context=daily_plan.day_context,
        slots=[
            SlotBrief(
                slot=episode.slot,
                narrative_purpose=f"完成{episode.title}的生活观察",
                scene_direction=episode.scene,
                event_direction=episode.main_event,
                appearance_intent=episode.appearance.description,
            )
            for episode in daily_plan.episodes
        ],
    )
    episode_prompt = compile_episode_director_prompt(
        day_brief=brief,
        slot_brief=brief.slots[1],
        previous_state_summaries=("上午已经结束并离开橱窗",),
    )
    assert "只输出一个" in episode_prompt
    assert "不要因为交互次数较多而删减合理剧情" in episode_prompt
    assert "VisibleWorldPlan的状态链完整自洽" in episode_prompt
    assert "最多两个发生位置" not in episode_prompt
    assert "EpisodeDirectorDraft" in episode_prompt
    assert "required_reference_roles" in episode_prompt
    assert "由本地系统确定" in episode_prompt
    assert "上午已经结束" in episode_prompt
    assert "眼睛服从参考素材整体画风" in episode_prompt
    assert "1至3个轻量镜头" in episode_prompt


def test_video_input_mode_is_only_upgraded_for_endpoint_risk(
    daily_plan: DailyProductionPlan,
) -> None:
    morning = daily_plan.episodes[0]
    assert select_video_input_mode(morning) is VideoInputMode.MULTIMODAL_REFERENCE

    simple = morning.model_copy(
        update={
            "actions": morning.actions[:2],
            "shots": [morning.shots[0].model_copy(update={"action_orders": [1, 2]})],
            "critical_relations": [],
        }
    )
    assert select_video_input_mode(simple) is VideoInputMode.MULTIMODAL_REFERENCE

    containment = simple.model_copy(
        update={
            "critical_relations": [
                CriticalRelation(
                    subject="纸袋",
                    relation="containment",
                    initial_state="水果在纸袋内",
                    final_state="水果仍从袋口回到纸袋内",
                )
            ],
        }
    )
    assert select_video_input_mode(containment) is VideoInputMode.MULTIMODAL_REFERENCE


def test_multi_clip_uses_two_disjoint_hard_cut_segments(
    daily_plan: DailyProductionPlan,
) -> None:
    base = daily_plan.episodes[0]
    episode = base.model_copy(
        update={
            "generation_strategy": GenerationStrategy.MULTI_CLIP,
            "segments": [
                SegmentPlan(
                    order=1,
                    shot_order=1,
                    action_orders=[1],
                    duration_seconds=4,
                ),
                SegmentPlan(
                    order=2,
                    shot_order=2,
                    action_orders=[2, 3],
                    duration_seconds=6,
                ),
            ],
        }
    )
    episode = type(base).model_validate(episode.model_dump())
    input_plan = _input_plan(daily_plan, slot_index=0).model_copy(
        update={"duration_seconds": 4}
    )

    compiled = compile_segment_video_prompt(
        episode,
        episode.segments[0],
        input_plan=input_plan,
    )

    assert "镜头1" in compiled.text
    assert "镜头2" not in compiled.text
    assert episode.actions[0].action in compiled.text
    assert episode.actions[1].action not in compiled.text


def test_multi_clip_rejects_overlapping_actions(
    daily_plan: DailyProductionPlan,
) -> None:
    data = daily_plan.episodes[0].model_dump()
    data.update(
        {
            "generation_strategy": "multi_clip",
            "segments": [
                {
                    "order": 1,
                    "shot_order": 1,
                    "action_orders": [1, 2],
                    "duration_seconds": 4,
                },
                {
                    "order": 2,
                    "shot_order": 2,
                    "action_orders": [2, 3],
                    "duration_seconds": 6,
                },
            ],
        }
    )
    with pytest.raises(ValidationError, match="重复"):
        type(daily_plan.episodes[0]).model_validate(data)


def test_multi_clip_rejects_segment_shot_mismatch(
    daily_plan: DailyProductionPlan,
) -> None:
    data = daily_plan.episodes[0].model_dump()
    data.update(
        {
            "generation_strategy": "multi_clip",
            "segments": [
                {
                    "order": 1,
                    "shot_order": 2,
                    "action_orders": [1],
                    "duration_seconds": 4,
                },
                {
                    "order": 2,
                    "shot_order": 1,
                    "action_orders": [2, 3],
                    "duration_seconds": 6,
                },
            ],
        }
    )
    with pytest.raises(ValidationError, match="一一对应"):
        type(daily_plan.episodes[0]).model_validate(data)


def test_segment_prompt_invalid_object_raises_business_error(
    daily_plan: DailyProductionPlan,
) -> None:
    base = daily_plan.episodes[0]
    episode = base.model_copy(
        update={
            "generation_strategy": GenerationStrategy.MULTI_CLIP,
            "segments": [
                SegmentPlan(
                    order=1,
                    shot_order=1,
                    action_orders=[1],
                    duration_seconds=4,
                ),
                SegmentPlan(
                    order=2,
                    shot_order=2,
                    action_orders=[2, 3],
                    duration_seconds=6,
                ),
            ],
        }
    )
    invalid = SegmentPlan.model_construct(
        order=1,
        shot_order=3,
        action_orders=[1],
        duration_seconds=4,
        requires_tail_link=False,
    )
    input_plan = _input_plan(daily_plan, slot_index=0).model_copy(
        update={"duration_seconds": 4}
    )
    with pytest.raises(PromptCompilationError, match="不存在的镜头"):
        compile_segment_video_prompt(
            episode,
            invalid,
            input_plan=input_plan,
        )


def test_simple_episode_still_uses_five_focused_sections(
    daily_plan: DailyProductionPlan,
) -> None:
    episode = daily_plan.episodes[0].model_copy(
        update={
            "actions": daily_plan.episodes[0].actions[:2],
            "shots": [
                daily_plan.episodes[0]
                .shots[0]
                .model_copy(update={"action_orders": [1, 2]})
            ],
            "critical_relations": [],
        }
    )
    plan = _input_plan(daily_plan, slot_index=0).model_copy(
        update={"duration_seconds": episode.duration_seconds}
    )
    prompt = compile_video_prompt(episode, input_plan=plan)
    assert "【顺序动作】" in prompt.text
    assert "【可见世界状态与切镜连续性】" in prompt.text
    assert prompt.char_count <= 1400
