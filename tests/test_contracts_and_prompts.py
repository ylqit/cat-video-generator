from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from cat_video_generator.domain.contracts import (
    CriticalRelation,
    DayBrief,
    DailyProductionPlan,
    MediaBinding,
    MediaModality,
    MediaPurpose,
    ProviderMediaRole,
    SlotBrief,
    VideoInputMode,
    VideoInputPlan,
)
from cat_video_generator.domain.prompts import (
    PromptBudgetError,
    compile_day_director_prompt,
    compile_episode_director_prompt,
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
        "【整体设定与素材绑定】",
        "【镜头顺序】",
        "【质量、物理与声音】",
    ):
        assert heading in prompt.text
    assert "镜头1" in prompt.text
    assert "00:" not in prompt.text
    assert "@图片1" in prompt.text
    assert "<主体1>" in prompt.text
    assert "纯黑竖椭圆眼睛" not in prompt.text
    assert "眼白、虹膜" not in prompt.text
    assert prompt.char_count <= 1400


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
    assert select_video_input_mode(containment) is VideoInputMode.STRICT_FIRST_LAST


def test_simple_episode_uses_one_paragraph_prompt(
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
    assert "【镜头顺序】" not in prompt.text
    assert "\n" not in prompt.text
