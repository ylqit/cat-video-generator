"""紧凑渲染计划和五段式Prompt。"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from cat_video_generator.domain.prompts import (
    compile_day_director_prompt,
    compile_episode_director_prompt,
    compile_video_prompt,
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


def test_video_input_plan_contains_only_non_derived_fields() -> None:
    plan = build_video_input_plan(
        input_mode=VideoInputMode.MULTIMODAL_REFERENCE,
        resolution="720p",
        duration_seconds=9,
        sources=(source("person:front"), source("cat:front"), source("style:indoor")),
    )
    assert set(plan.model_dump()) == {
        "input_mode",
        "resolution",
        "duration_seconds",
        "bindings",
    }
    binding = plan.bindings[0]
    assert set(binding.model_dump()) == {
        "asset_id",
        "semantic_key",
        "modality",
        "provider_role",
        "ordinal",
        "sha256",
    }
    assert binding.prompt_alias == "@图片1"


def test_strict_first_last_requires_exact_frame_pair() -> None:
    plan = build_video_input_plan(
        input_mode=VideoInputMode.STRICT_FIRST_LAST,
        resolution="480p",
        duration_seconds=10,
        sources=(source("frame:first"), source("frame:last")),
    )
    assert [item.provider_role.value for item in plan.bindings] == [
        "first_frame",
        "last_frame",
    ]
    with pytest.raises(ValueError, match="要求素材"):
        build_video_input_plan(
            input_mode=VideoInputMode.STRICT_FIRST_LAST,
            resolution="480p",
            duration_seconds=10,
            sources=(source("frame:first"),),
        )


def test_audio_reference_requires_visual_input() -> None:
    with pytest.raises(ValidationError, match="必须同时具有视觉输入"):
        VideoInputPlan(
            input_mode="multimodal_reference",
            resolution="720p",
            duration_seconds=8,
            bindings=[
                {
                    "asset_id": uuid.uuid4(),
                    "semantic_key": "audio:wind",
                    "modality": "audio",
                    "provider_role": "reference_audio",
                    "ordinal": 1,
                    "sha256": "b" * 64,
                }
            ],
        )


def test_video_prompt_is_compact_and_has_five_sections(daily_plan) -> None:
    episode = daily_plan.episodes[0]
    plan = build_video_input_plan(
        input_mode=episode.video_input_mode,
        resolution="720p",
        duration_seconds=episode.duration_seconds,
        sources=(source("person:front"), source("cat:front"), source("style:indoor")),
    )
    prompt = compile_video_prompt(episode, input_plan=plan)
    assert prompt.char_count < 1400
    for title in (
        "【输出、画风与素材绑定】",
        "【人物、猫咪、外观和空间】",
        "【顺序动作】",
        "【可见世界状态与切镜连续性】",
        "【原生声音和硬禁止】",
    ):
        assert title in prompt.text
    assert "00:00" not in prompt.text
    assert "assetId" not in prompt.text


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
    assert "全天三个" not in episode_prompt
