"""故事板渲染计划和五段式Seedance Prompt。"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from cat_video_generator.domain.prompts import (
    compile_day_director_prompt,
    compile_episode_director_prompt,
    compile_storyboard_prompt,
    compile_video_prompt,
    storyboard_panel_count,
)
from cat_video_generator.domain.rendering import (
    MediaSource,
    VideoInputMode,
    VideoInputPlan,
    build_video_input_plan,
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


def test_video_prompt_uses_storyboard_and_has_five_sections(daily_plan) -> None:
    episode = daily_plan.episodes[0]
    plan = build_video_input_plan(
        input_mode=VideoInputMode.STORYBOARD_REFERENCE,
        resolution="720p",
        duration_seconds=episode.duration_seconds,
        sources=storyboard_sources(),
    )
    prompt = compile_video_prompt(episode, input_plan=plan)
    for title in (
        "【输出和画风】",
        "【人物、猫咪与场景】",
        "【按故事板顺序发生的动作】",
        "【关键实体连续性】",
        "【原生声音和禁止项】",
    ):
        assert title in prompt.text
    assert "@图片1" in prompt.text
    assert "00:00" not in prompt.text
    assert "assetId" not in prompt.text


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
