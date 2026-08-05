"""故事板渲染计划和三段式Seedance Prompt。"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from cat_video_generator.domain.contracts import EpisodePlan
from cat_video_generator.domain.prompts import (
    compile_day_director_prompt,
    compile_episode_director_prompt,
    compile_storyboard_prompt,
    compile_storyboard_review_prompt,
    compile_video_prompt,
    storyboard_panel_count,
)
from cat_video_generator.domain.rendering import (
    MediaSource,
    VideoInputMode,
    VideoInputPlan,
    build_video_input_plan,
    storyboard_reference_keys,
)
from cat_video_generator.domain.visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
)


def source(key: str, *, media_type: str = "image") -> MediaSource:
    metadata = (
        {"width": 720, "height": 1280}
        if media_type == "image"
        else {"durationSeconds": 4}
    )
    return MediaSource(
        asset_id=uuid.uuid4(),
        semantic_key=key,
        media_type=media_type,
        sha256="a" * 64,
        metadata=metadata,
    )


def storyboard_sources(count: int = 3) -> tuple[MediaSource, ...]:
    return tuple(source(f"storyboard:panel-{index:02d}") for index in range(1, count + 1))


def test_storyboard_input_plan_contains_ordered_panels() -> None:
    plan = build_video_input_plan(
        input_mode=VideoInputMode.STORYBOARD_REFERENCE,
        resolution="720p",
        duration_seconds=9,
        sources=storyboard_sources(),
    )
    assert set(plan.model_dump()) == {
        "input_mode",
        "resolution",
        "duration_seconds",
        "bindings",
    }
    assert [item.prompt_alias for item in plan.bindings] == ["@图片1", "@图片2", "@图片3"]
    assert all(item.provider_role.value == "reference_image" for item in plan.bindings)


def test_storyboard_references_use_person_and_cat_view_matching_first_shot(
    daily_plan,
) -> None:
    episode = daily_plan.episodes[0]
    keys = storyboard_reference_keys(
        episode,
        DEFAULT_STYLE_PROFILE,
        DEFAULT_SERIES_VISUAL_PROFILE,
        look_key="look:morning-current",
    )

    assert keys == (
        "look:morning-current",
        "cat:front",
    )


def test_all_directors_receive_finalized_style_profile(daily_plan) -> None:
    day = compile_day_director_prompt(
        target_date=daily_plan.content_date,
        planning_context="普通生活日",
    )
    episode = compile_episode_director_prompt(
        day_brief=daily_plan.day_brief,
        slot_brief=daily_plan.day_brief.slots[0],
        previous_state_summaries=(),
    )

    for prompt in (day, episode):
        assert DEFAULT_STYLE_PROFILE.prompt_positive() in prompt
        assert DEFAULT_STYLE_PROFILE.prompt_negative() in prompt


def test_visual_critical_uses_only_storyboard_first_and_last() -> None:
    sources = storyboard_sources(4)
    plan = build_video_input_plan(
        input_mode=VideoInputMode.STRICT_FIRST_LAST,
        resolution="480p",
        duration_seconds=10,
        sources=(sources[0], sources[-1]),
    )
    assert [item.provider_role.value for item in plan.bindings] == [
        "first_frame",
        "last_frame",
    ]
    with pytest.raises(ValueError, match="只发送故事板首尾"):
        build_video_input_plan(
            input_mode=VideoInputMode.STRICT_FIRST_LAST,
            resolution="480p",
            duration_seconds=10,
            sources=(sources[0],),
        )


def test_storyboard_plan_rejects_non_image_binding() -> None:
    with pytest.raises(ValidationError):
        VideoInputPlan(
            input_mode="storyboard_reference",
            resolution="720p",
            duration_seconds=8,
            bindings=[
                {
                    "asset_id": uuid.uuid4(),
                    "semantic_key": "storyboard:panel-01",
                    "modality": "audio",
                    "provider_role": "reference_image",
                    "ordinal": 1,
                    "sha256": "b" * 64,
                }
            ],
        )


def test_storyboard_prompt_requests_separate_clean_panels(daily_plan) -> None:
    episode = daily_plan.episodes[0]
    prompt = compile_storyboard_prompt(
        episode,
        reference_roles=("person:front", "cat:front", "style:line_texture"),
    )
    assert storyboard_panel_count(episode) == 3
    assert "3张相互连贯但彼此独立" in prompt.text
    assert "不要拼成网格" in prompt.text
    assert "不得包含文字、序号" in prompt.text
    assert "景别为中景" in prompt.text
    assert episode.script.shots[0].direction in prompt.text
    assert "唯一运镜为缓慢推近" in prompt.text
    assert "人物孩子" not in prompt.text
    assert "灰白猫灰白猫" not in prompt.text
    assert "formKey" not in prompt.text
    assert "生命周期" not in prompt.text


def test_two_shots_use_distinct_entry_and_payoff_panels(daily_plan) -> None:
    episode = daily_plan.episodes[0]
    payload = episode.model_dump(mode="json")
    payload["script"]["shots"] = [
        {
            "order": 1,
            "action_orders": [1],
            "framing": "环境中景",
            "camera_move": "fixed",
            "dominant_view": "front",
            "direction": "人物活动，猫咪已经在同一空间观察",
        },
        {
            "order": 2,
            "action_orders": [2],
            "framing": "猫咪近景",
            "camera_move": "push",
            "dominant_view": "side",
            "direction": "猫咪独立探索后回到人物关系中",
        },
    ]
    relationship_arc = EpisodePlan.model_validate(payload)

    prompt = compile_storyboard_prompt(relationship_arc)

    assert storyboard_panel_count(relationship_arc) == 3
    assert "沿用镜头2的机位、空间轴线" in prompt.text
    assert "不重新设计人物、猫咪或场景" in prompt.text


def test_single_shot_video_prompt_uses_storyboard_without_redundant_sections(
    daily_plan,
) -> None:
    episode = daily_plan.episodes[0]
    plan = build_video_input_plan(
        input_mode=VideoInputMode.STORYBOARD_REFERENCE,
        resolution="720p",
        duration_seconds=episode.duration_seconds,
        sources=storyboard_sources(),
    )
    prompt = compile_video_prompt(episode, input_plan=plan)
    assert "【整体设定与素材绑定】" in prompt.text
    assert "【镜头顺序】" in prompt.text
    assert "【质量、连续性与声音】" in prompt.text
    assert "@图片1" in prompt.text
    assert "执行主体为" in prompt.text
    assert episode.script.sound_design in prompt.text
    assert "00:00" not in prompt.text
    assert "assetId" not in prompt.text


def test_storyboard_review_distinguishes_identity_proportion_and_space(daily_plan) -> None:
    episode = daily_plan.episodes[0]

    prompt = compile_storyboard_review_prompt(
        episode,
        panel_count=storyboard_panel_count(episode),
    )

    assert "bodyProportionOk" in prompt
    assert "spatialContinuityOk" in prompt
    assert "propContinuityOk" in prompt
    assert "面板序号" in prompt
    assert "合理重新取景不算漂移" in prompt


def test_legal_long_prompt_is_not_locally_blocked(daily_plan) -> None:
    episode = daily_plan.episodes[0]
    long_scene = "明亮而自然的生活空间" * 120
    script = episode.script.model_copy(update={"scene": long_scene})
    long_episode = episode.model_copy(update={"script": script})
    plan = build_video_input_plan(
        input_mode=VideoInputMode.STORYBOARD_REFERENCE,
        resolution="720p",
        duration_seconds=episode.duration_seconds,
        sources=storyboard_sources(),
    )
    prompt = compile_video_prompt(long_episode, input_plan=plan)
    assert prompt.char_count > 1600
    assert prompt.warnings == ()


def test_directors_are_four_separate_contract_prompts(daily_plan) -> None:
    day_prompt = compile_day_director_prompt(
        target_date=daily_plan.content_date,
        planning_context="设计自然的普通生活日",
    )
    episode_prompt = compile_episode_director_prompt(
        day_brief=daily_plan.day_brief,
        slot_brief=daily_plan.day_brief.slots[0],
        previous_state_summaries=(),
    )
    assert "DayBrief" in day_prompt
    assert "EpisodeScript" not in day_prompt
    assert "EpisodeScript" in episode_prompt
    assert "SceneContinuity" in episode_prompt
    assert "首个动作" in episode_prompt
    assert "start_state" in episode_prompt
    assert "全天三个" not in episode_prompt
    assert "动作阶段服务于镜头叙事" in episode_prompt
    assert "猫咪产生独立反应" in episode_prompt
    assert "事件种子一天最多分配给一个时段" in day_prompt


def test_three_shots_compile_four_storyboard_panels(daily_plan) -> None:
    episode = daily_plan.episodes[0]
    payload = episode.model_dump(mode="json")
    payload["script"]["actions"].append(
        {
            "order": 3,
            "actor_id": "cat",
            "action": "灰白猫探索后回到人物脚边，用鼻尖轻碰纸风车",
            "visible_result": "人物低头回应，猫咪重新进入人物关系",
        }
    )
    payload["script"]["shots"] = [
        {
            "order": 1,
            "action_orders": [1],
            "framing": "环境中景",
            "camera_move": "fixed",
            "dominant_view": "front",
            "direction": "人物在桌边活动，猫咪已在画面下方观察",
        },
        {
            "order": 2,
            "action_orders": [2],
            "framing": "猫咪近景",
            "camera_move": "follow",
            "dominant_view": "side",
            "direction": "猫咪离开人物脚边，独立探索转动的纸风车",
        },
        {
            "order": 3,
            "action_orders": [3],
            "framing": "双主体中近景",
            "camera_move": "push",
            "dominant_view": "mixed",
            "direction": "猫咪回到人物身边，人物俯身作出回应",
        },
    ]
    relationship_arc = EpisodePlan.model_validate(payload)

    prompt = compile_storyboard_prompt(relationship_arc)

    assert storyboard_panel_count(relationship_arc) == 4
    assert "4张相互连贯但彼此独立" in prompt.text
    assert "环境中景" in prompt.text
    assert "猫咪近景" in prompt.text
    assert "双主体中近景" in prompt.text
    assert "猫咪重新进入人物关系" in prompt.text
    assert "沿用镜头3的机位、空间轴线" in prompt.text
