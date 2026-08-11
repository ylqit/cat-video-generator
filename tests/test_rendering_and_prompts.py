"""RenderPlan、视觉Prompt和Seedance执行Prompt。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from conftest import episode_for

from cat_video_generator.domain.contracts import Slot
from cat_video_generator.domain.prompts import (
    PromptCompilationError,
    compile_day_director_prompt,
    compile_episode_director_prompt,
    compile_look_prompt,
    compile_opening_anchor_prompt,
    compile_video_diagnostic_prompt,
    compile_video_prompt,
    compile_video_prompt_preview,
    compile_video_range_edit_prompt,
)
from cat_video_generator.domain.rendering import (
    ClipOrigin,
    MediaSource,
    RenderMode,
    RenderOperation,
    VideoSequenceClip,
    VideoSequencePlan,
    build_render_plan,
    build_video_edit_input_plan,
    build_video_input_plan,
    supports_video_extension,
)


@pytest.mark.parametrize(
    ("duration", "mode", "section_durations"),
    [
        (12, RenderMode.SINGLE_PASS, [12]),
        (22, RenderMode.EXTENDED, [11, 11]),
        (45, RenderMode.EXTENDED, [15, 15, 15]),
    ],
)
def test_render_plan_maps_duration_to_one_to_three_tasks(duration, mode, section_durations) -> None:
    plan = build_render_plan(episode_for(Slot.NOON, duration=duration))

    assert plan.mode is mode
    assert [item.duration_seconds for item in plan.sections] == section_durations
    assert [order for item in plan.sections for order in item.shot_orders] == list(
        range(1, len(episode_for(Slot.NOON, duration=duration).script.shots) + 1)
    )


def test_only_full_seedance_profile_supports_extension() -> None:
    assert supports_video_extension("doubao-seedance-2-0-260128")
    assert not supports_video_extension("doubao-seedance-2-0-mini-260615")


def test_initial_and_extension_input_plans_use_one_semantic_source() -> None:
    image = MediaSource(uuid4(), "opening:morning", "image", "a" * 64, {})
    video = MediaSource(uuid4(), "video:section-1", "video", "b" * 64, {})

    initial = build_video_input_plan(
        operation=RenderOperation.INITIAL,
        resolution="720p",
        duration_seconds=12,
        source=image,
    )
    extension = build_video_input_plan(
        operation=RenderOperation.EXTEND,
        resolution="720p",
        duration_seconds=11,
        source=video,
    )

    assert initial.bindings[0].prompt_alias == "@图片1"
    assert initial.bindings[0].provider_role.value == "first_frame"
    assert extension.bindings[0].prompt_alias == "@视频1"
    assert extension.bindings[0].provider_role.value == "reference_video"


def test_edit_input_plan_keeps_video_then_two_boundary_images() -> None:
    video = MediaSource(uuid4(), "video:source", "video", "a" * 64, {})
    before = MediaSource(uuid4(), "boundary:before", "image", "b" * 64, {})
    after = MediaSource(uuid4(), "boundary:after", "image", "c" * 64, {})

    plan = build_video_edit_input_plan(
        resolution="720p",
        duration_seconds=12,
        source_video=video,
        before_frame=before,
        after_frame=after,
    )

    assert [item.prompt_alias for item in plan.bindings] == ["@视频1", "@图片1", "@图片2"]
    assert plan.operation is RenderOperation.EDIT


def test_video_sequence_edl_requires_one_continuous_timeline() -> None:
    first, second = uuid4(), uuid4()
    plan = VideoSequencePlan(
        duration_ms=12_000,
        clips=[
            VideoSequenceClip(
                order=1,
                source_asset_id=first,
                source_start_ms=0,
                source_end_ms=4_000,
                timeline_start_ms=0,
                timeline_end_ms=4_000,
                origin=ClipOrigin.ORIGINAL,
            ),
            VideoSequenceClip(
                order=2,
                source_asset_id=second,
                source_start_ms=0,
                source_end_ms=8_000,
                timeline_start_ms=4_000,
                timeline_end_ms=12_000,
                origin=ClipOrigin.ORIGINAL,
            ),
        ],
    )

    assert plan.duration_ms == 12_000
    with pytest.raises(ValueError, match="连续"):
        VideoSequencePlan(
            duration_ms=13_000,
            clips=[
                plan.clips[0],
                plan.clips[1].model_copy(
                    update={"timeline_start_ms": 5_000, "timeline_end_ms": 13_000}
                ),
            ],
        )


def test_range_edit_prompt_describes_one_change_and_boundaries() -> None:
    prompt = compile_video_range_edit_prompt(
        episode_for(Slot.NOON),
        instruction="让钓线只连接人物手中鱼竿和浮标",
        duration_seconds=12,
        source_start_ms=2_000,
        source_end_ms=5_000,
        relevant_constraints=("钓线不得接触猫咪身体",),
    ).text

    assert "@视频1" in prompt and "@图片1" in prompt and "@图片2" in prompt
    assert "2.00秒至5.00秒" in prompt
    assert prompt.count("让钓线只连接人物手中鱼竿和浮标") == 1
    assert "原视频音轨由本地无损继承" in prompt


def test_director_prompts_separate_day_capacity_from_episode_execution(daily_plan) -> None:
    day_prompt = compile_day_director_prompt(
        target_date=daily_plan.content_date,
        planning_context="放风筝主题",
    )
    slot_prompt = compile_episode_director_prompt(
        day_brief=daily_plan.day_brief,
        slot_brief=daily_plan.day_brief.slot_briefs[1],
        previous_state_summaries=("上午已完成风筝制作",),
    )

    assert "不得输出具体动作、镜头、精确秒数" in day_prompt
    assert "猫咪主活动" in day_prompt
    assert "精确时长必须在16至30秒" in slot_prompt
    assert "relationshipArc" in slot_prompt
    assert "猫咪保持四足自然行为" in slot_prompt


def test_visual_prompts_use_one_look_and_one_opening_anchor() -> None:
    episode = episode_for(Slot.MORNING)
    look = compile_look_prompt(
        episode,
        reference_roles=("person:front", "style:line_texture"),
    )
    anchor = compile_opening_anchor_prompt(
        episode,
        reference_roles=("look:episode", "cat:front", "style:indoor"),
    )

    assert "全身定妆图" in look.text
    assert "不包含猫咪" in look.text
    assert "开场视觉锚点" in anchor.text
    assert "活动焦点" in anchor.text
    assert "故事板" not in look.text + anchor.text


def test_short_video_prompt_keeps_director_execution_depth() -> None:
    episode = episode_for(Slot.MORNING, duration=12)
    prompt = compile_video_prompt_preview(episode, resolution="720p").text

    assert prompt.count("镜头1：") == 1
    assert "固定镜头" in prompt
    assert "肢体" not in prompt  # 动作本身已具体，不输出抽象字段名。
    assert "灰白猫" in prompt and "人物" in prompt
    assert episode.script.story_text not in prompt
    assert "完整剧情" not in prompt
    assert "同一只风筝" in prompt
    assert "原生音频" in prompt
    assert "绝对时间" not in prompt
    assert "数据库" not in prompt
    assert "storyboard" not in prompt.lower()


def test_connector_constraint_has_one_source_across_anchor_video_and_review() -> None:
    episode = episode_for(Slot.NOON, duration=22)
    statement = episode.script.hard_constraints[0].text
    anchor = compile_opening_anchor_prompt(
        episode,
        reference_roles=("look:noon", "cat:front", "style:outdoor"),
    ).text
    video = compile_video_prompt_preview(
        episode,
        resolution="720p",
        section_order=1,
    ).text
    diagnostic = compile_video_diagnostic_prompt(episode)

    assert statement in anchor
    assert statement in video
    assert statement in diagnostic
    assert video.count(statement) == 1
    assert "不得经过、缠绕或连接猫咪身体" in video
    assert "人物与猫咪共同执行" not in video
    assert "motion_path" not in video
    assert "hard_constraints" not in video


def test_low_risk_shot_does_not_emit_empty_interaction_placeholder() -> None:
    episode = episode_for(Slot.MORNING).model_copy(deep=True)
    episode.script.hard_constraints.clear()

    prompt = compile_video_prompt_preview(episode, resolution="720p").text

    assert "没有额外关键交互约束" not in prompt
    assert "本镜关键交互" not in prompt


def test_extension_prompt_does_not_reveal_final_prop_state_early() -> None:
    episode = episode_for(Slot.NOON, duration=22)
    render_plan = build_render_plan(episode)
    source = MediaSource(uuid4(), "video:section-1", "video", "c" * 64, {})
    input_plan = build_video_input_plan(
        operation=RenderOperation.EXTEND,
        resolution="480p",
        duration_seconds=render_plan.sections[1].duration_seconds,
        source=source,
    )
    prompt = compile_video_prompt(
        episode,
        input_plan=input_plan,
        section=render_plan.sections[1],
    ).text

    assert "向后延长 @视频1" in prompt
    assert episode.script.ending in prompt
    assert "最终可见回报" in prompt


def test_first_medium_section_defers_final_payoff() -> None:
    episode = episode_for(Slot.NOON, duration=22)
    prompt = compile_video_prompt_preview(episode, resolution="480p", section_order=1).text

    assert "暂不进入最终结果" in prompt
    assert "不提前完成" in prompt
    assert "最终可见回报" not in prompt
    assert "不提前演出后续镜头或最终回报" in prompt
    assert "必须完整进入最后一个镜头并兑现结尾" not in prompt


def test_invalid_preview_section_raises_business_error() -> None:
    with pytest.raises(PromptCompilationError, match="不存在渲染区段3"):
        compile_video_prompt_preview(
            episode_for(Slot.NOON, duration=22),
            resolution="720p",
            section_order=3,
        )
