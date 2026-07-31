"""总导演、时段导演、图片和视频 Prompt 的确定性编译器。

完整规划保存在数据库；Seedance 只接收当前 Episode 的执行信息。这样既保留
可审计上下文，也避免把导演理由、评分和长期记忆重复塞进视频 Prompt。
"""

from __future__ import annotations

from dataclasses import dataclass

from .continuity import (
    ActionTransition,
    replay_terminal_state,
    resolve_transition_state_entity_id,
)
from .contracts import (
    EpisodePlan,
    GenerationStrategy,
    MediaBinding,
    MediaPurpose,
    SegmentPlan,
    ShotPlan,
    VideoInputPlan,
)
from .director_prompts import (
    compile_day_director_prompt,
    compile_episode_director_prompt,
    summarize_episode_state,
)
from .visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
    SeriesVisualProfile,
    StyleProfile,
)

__all__ = [
    "compile_day_director_prompt",
    "compile_episode_director_prompt",
    "summarize_episode_state",
]

VIDEO_PROMPT_WARNING_CHARS = 1400
VIDEO_PROMPT_BLOCKING_CHARS = 1600
VIDEO_PROMPT_REPLAN_CHARS = 2000

class PromptBudgetError(ValueError):
    """执行 Prompt 超过收费调用允许的注意力预算。"""


class PromptCompilationError(ValueError):
    """脚本契约无法安全编译为供应商执行Prompt。"""


@dataclass(frozen=True, slots=True)
class CompiledPrompt:
    """编译后的 Prompt 及预算诊断。"""

    text: str
    char_count: int
    utf8_bytes: int
    warnings: tuple[str, ...]


def compile_image_prompt(
    episode: EpisodePlan,
    *,
    target: str,
    reference_roles: tuple[str, ...] = (),
    style_anchor: str | None = None,
    persona_anchor: str | None = None,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    retry_feedback: str | None = None,
) -> CompiledPrompt:
    """为首帧或尾帧生成聚焦图片 Prompt。"""

    if target not in {"first_frame", "last_frame", "element"}:
        raise ValueError(f"不支持的图片目标: {target}")
    style = style_anchor or style_profile.prompt_positive()
    persona = persona_anchor or (
        f"{series_profile.person_identity}；{series_profile.person_hair}；"
        f"{series_profile.person_body}"
    )
    state = episode.actions[0].action if target == "first_frame" else episode.ending
    relation_state = describe_relations(episode, final=target == "last_frame")
    reference_line = "；".join(
        f"图{index}负责{_reference_role_label(role)}"
        for index, role in enumerate(reference_roles, start=1)
    )
    world_state = describe_world_frame(episode, final=target == "last_frame")
    sections = [
        f"【任务】生成{target}竖屏参考图。",
        (f"【参考素材】{reference_line}。" if reference_line else "【参考素材】没有额外参考图。"),
        f"【画风与身份】{style}；排除{style_profile.prompt_negative()}；沿用参考图中"
        f"的同一个中性儿童和同一只灰白猫；人物为{persona}；"
        f"{series_profile.cat_identity}。",
        f"【场景与外观】{episode.scene}；{episode.appearance.description}",
        "【画布】严格9:16竖屏单幅画面，不得生成横图、方图、黑边或多视图拼图。",
    ]
    sections.extend(
        (
            f"【画面状态】{state}",
            f"【可见世界】{world_state}",
            f"【关键关系】{relation_state}",
            "【禁止】额外人物或动物、分身、多视图、文字、Logo、UI；不得凭空改变"
            "关键物体的形状、支撑、包含或空间边界。",
        )
    )
    if retry_feedback:
        sections.append(
            "【本次重试修正】上一张图保持只读审计，不得照搬其错误；"
            f"本次必须修正：{retry_feedback}"
        )
    return _compiled("\n".join(sections))


def compile_video_prompt(
    episode: EpisodePlan,
    *,
    input_plan: VideoInputPlan,
    style_anchor: str | None = None,
    persona_anchor: str | None = None,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> CompiledPrompt:
    """生成只包含当前Episode执行信息的五段式Seedance Prompt。"""

    if input_plan.duration_seconds != episode.duration_seconds:
        raise ValueError("输入计划时长与Episode不一致")
    return _compile_video_prompt_body(
        episode,
        bindings=_binding_definitions(input_plan.bindings),
        resolution=input_plan.resolution,
        duration_seconds=input_plan.duration_seconds,
        style_anchor=style_anchor,
        persona_anchor=persona_anchor,
        style_profile=style_profile,
    )


def compile_video_prompt_preview(
    episode: EpisodePlan,
    *,
    resolution: str,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> CompiledPrompt:
    """规划阶段复用正式编译内核，提前发现注意力预算问题。

    这里只放入固定长度的素材占位说明，不创建输入计划、Step或收费任务。
    """

    if resolution not in {"480p", "720p"}:
        raise ValueError("Prompt预检分辨率只允许480p或720p")
    return _compile_video_prompt_body(
        episode,
        bindings="人物、灰白猫、画风和本集必要元素按实际素材顺序绑定。",
        resolution=resolution,
        duration_seconds=episode.duration_seconds,
        style_anchor=None,
        persona_anchor=None,
        style_profile=style_profile,
    )


def _compile_video_prompt_body(
    episode: EpisodePlan,
    *,
    bindings: str,
    resolution: str,
    duration_seconds: int,
    style_anchor: str | None,
    persona_anchor: str | None,
    style_profile: StyleProfile,
) -> CompiledPrompt:
    style = style_anchor or style_profile.prompt_positive()
    persona = persona_anchor or "同一名中性儿童"
    shots = episode.shots or _legacy_shots(episode)
    shot_text = "\n".join(_compile_shot(shot, episode) for shot in shots)
    world = _world_transition_lines(episode)
    boundaries = describe_boundaries(episode)
    text = "\n".join(
        (
            "【输出、画风与素材绑定】"
            f"{bindings}输出{resolution}、9:16竖屏、"
            f"{duration_seconds}秒完整成片。{style}；"
            "保持二维手绘、哑光平涂，排除3D、CG、PBR和塑料高光。",
            "【人物、猫咪、外观和空间】"
            f"固定{persona}与同一只灰白猫，主要面貌、短发、体型和灰白斑纹"
            f"以参考素材为准。本集外观：{episode.appearance.description}。"
            f"场景：{episode.scene}。",
            f"【顺序动作】\n{shot_text}\n结尾可见结果：{episode.ending}。",
            f"【可见世界状态与切镜连续性】{world}。{boundaries}",
            "【原生声音和硬禁止】环境声、动作声自然同步，无对白、旁白或歌词。"
            "禁止角色增减或分身；禁止道具凭空出现、消失、复制或变形；禁止物体"
            "穿透或无支撑悬空；切镜继承服装、发长和持久物；禁止字幕、水印、Logo、UI。",
        )
    )
    compiled = _compiled(text)
    if compiled.char_count > VIDEO_PROMPT_REPLAN_CHARS:
        raise PromptBudgetError("视频Prompt超过2000字符，必须重新规划或拆分事件")
    if compiled.char_count > VIDEO_PROMPT_BLOCKING_CHARS:
        raise PromptBudgetError("视频Prompt超过1600字符，必须先去重后才能付费生成")
    return compiled


def compile_segment_video_prompt(
    episode: EpisodePlan,
    segment: SegmentPlan,
    *,
    input_plan: VideoInputPlan,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> CompiledPrompt:
    """把一个天然硬切片段投影为独立 Seedance 执行上下文。

    片段只继承自己覆盖的动作与镜头，并重新编号为连续序列。这样 Ark 不会看到
    另一个片段尚未发生的动作，也不会因为原始 order 有空洞而误读执行顺序。
    """

    if episode.generation_strategy is not GenerationStrategy.MULTI_CLIP:
        raise ValueError("只有multi_clip Episode可以编译片段Prompt")
    selected_actions = [
        item for item in episode.actions if item.order in segment.action_orders
    ]
    if (
        not selected_actions
        or {item.order for item in selected_actions} != set(segment.action_orders)
    ):
        raise PromptCompilationError(
            f"片段{segment.order}引用了不存在或不完整的动作阶段"
        )
    order_map = {item.order: index for index, item in enumerate(selected_actions, 1)}
    actions = [
        item.model_copy(update={"order": order_map[item.order]}) for item in selected_actions
    ]
    selected_shot = next(
        (item for item in episode.shots if item.order == segment.shot_order),
        None,
    )
    if selected_shot is None:
        raise PromptCompilationError(
            f"片段{segment.order}引用了不存在的镜头{segment.shot_order}"
        )
    shot = selected_shot.model_copy(
        update={
            "order": 1,
            "action_orders": [order_map[item] for item in segment.action_orders],
        }
    )
    assert episode.visible_world is not None
    transitions = [
        item.model_copy(
            update={
                "action_order": order_map[item.action_order],
                "shot_order": 1,
            }
        )
        for item in episode.visible_world.action_transitions
        if item.action_order in order_map
    ]
    world = episode.visible_world.model_copy(
        update={
            "action_transitions": transitions,
            "shot_boundary_states": [],
        }
    )
    segment_episode = episode.model_copy(
        update={
            "actions": actions,
            "shots": [shot],
            "ending": actions[-1].visible_result,
            "duration_seconds": segment.duration_seconds,
            "visible_world": world,
            "video_input_mode": input_plan.input_mode,
            "generation_strategy": GenerationStrategy.SINGLE_PASS,
            "segments": [],
        }
    )
    return compile_video_prompt(
        segment_episode,
        input_plan=input_plan,
        style_profile=style_profile,
    )


def _binding_definitions(bindings: list[MediaBinding]) -> str:
    if not bindings:
        return "不使用额外参考素材。"
    definitions: list[str] = []
    for item in bindings:
        if item.source_role == "person":
            definitions.append(f"{item.prompt_alias}只负责同一中性儿童身份")
        elif item.source_role == "cat":
            definitions.append(f"{item.prompt_alias}只负责同一灰白猫身份")
        elif item.purpose is MediaPurpose.STYLE:
            definitions.append(f"画风参考{item.prompt_alias}")
        elif item.purpose is MediaPurpose.MOTION:
            definitions.append(f"只参考{item.prompt_alias}的动作与运镜，不覆盖主体身份")
        elif item.purpose is MediaPurpose.ATMOSPHERE:
            definitions.append(f"只参考{item.prompt_alias}的声音氛围")
        elif item.purpose is MediaPurpose.SEMANTIC_OPENING:
            definitions.append(f"{item.prompt_alias}作为开场画面语义参考")
        elif item.purpose is MediaPurpose.SEMANTIC_ENDING:
            definitions.append(f"{item.prompt_alias}作为结尾画面语义参考")
        else:
            definitions.append(f"{item.prompt_alias}负责{_reference_role_label(item.source_role)}")
    return "；".join(definitions) + "。"


def _compile_shot(shot: ShotPlan, episode: EpisodePlan) -> str:
    stages = [stage for stage in episode.actions if stage.order in shot.action_orders]
    actor_actions = "；随后".join(
        f"{describe_actor(stage.actor_id)}执行：{stage.action}" for stage in stages
    )
    return (
        f"镜头{shot.order}：{_camera_label(shot.camera_move.value)}，"
        f"{shot.framing}，{shot.direction}；"
        f"{actor_actions}。"
    )


def _legacy_shots(episode: EpisodePlan) -> tuple[ShotPlan, ...]:
    """只为已入库且没有镜头计划的旧脚本提供可读执行映射。"""

    return (
        ShotPlan(
            order=1,
            action_orders=[stage.order for stage in episode.actions],
            framing="中景",
            camera_move="fixed",
            direction="保持同一空间和稳定构图，动作连续发生",
        ),
    )


def _camera_label(value: str) -> str:
    return {
        "fixed": "固定镜头",
        "follow": "平稳跟拍",
        "push": "缓慢推进",
        "pull": "缓慢拉远",
        "pan": "缓慢摇摄",
        "track": "平稳横移",
    }[value]


def describe_actor(actor_id: str) -> str:
    return {
        "person": "人物",
        "cat": "灰白猫",
        "guest": "临时配角",
        "environment": "环境",
    }[actor_id]


def describe_world_frame(episode: EpisodePlan, *, final: bool) -> str:
    assert episode.visible_world is not None
    anchors = {item.anchor_id: item.display_name for item in episode.visible_world.scene_anchors}
    entities = {item.entity_id: item for item in episode.visible_world.tracked_entities}
    if final:
        terminal = replay_terminal_state(episode.visible_world)
        positions = {
            entity_id: state.anchor_id
            for entity_id, state in terminal.items()
            if state.active
        }
    else:
        positions = {
            item.entity_id: item.initial_anchor_id
            for item in episode.visible_world.tracked_entities
            if item.lifecycle.value != "enter"
        }
    values = [
        f"{entities[entity_id].display_name}位于"
        f"{anchors.get(anchor_id or '', anchor_id or '场景内')}"
        for entity_id, anchor_id in sorted(positions.items())
    ]
    return "；".join(values) or "只有同一个中性儿童和同一只灰白猫"


def _world_transition_lines(episode: EpisodePlan) -> str:
    assert episode.visible_world is not None
    entities = {
        item.entity_id: item.display_name for item in episode.visible_world.tracked_entities
    }
    anchors = {item.anchor_id: item.display_name for item in episode.visible_world.scene_anchors}
    lines: list[str] = []
    for item in sorted(
        episode.visible_world.action_transitions,
        key=lambda transition: transition.action_order,
    ):
        if not _transition_changes_visible_state(item):
            continue
        state_entity_id = resolve_transition_state_entity_id(item, entities)
        state_entity = (
            entities.get(state_entity_id, state_entity_id)
            if state_entity_id is not None
            else "角色自身"
        )
        before = anchors.get(item.before_anchor_id or "", item.before_anchor_id or "原位置")
        after = anchors.get(item.after_anchor_id or "", item.after_anchor_id or before)
        support = item.support_after or item.after_anchor_id
        details = [
            f"动作{item.action_order}",
            f"{state_entity}从{before}到{after}",
        ]
        if support:
            details.append(f"结束后由{anchors.get(support, entities.get(support, support))}支撑")
        if item.containment_before != item.containment_after:
            details.append(
                f"包含关系由{item.containment_before or '无'}变为{item.containment_after or '无'}"
            )
        if item.contact_before != item.contact_after and item.contact_after:
            details.append(f"接触{entities.get(item.contact_after, item.contact_after)}")
        if item.lifecycle_event is not None and item.lifecycle_event.value != "persist":
            details.append(f"生命周期事件为{item.lifecycle_event.value}")
        lines.append("，".join(details))
    return "；".join(lines) or "人物和猫咪始终在已声明场景锚点内"


def _transition_changes_visible_state(transition: ActionTransition) -> bool:
    lifecycle = transition.lifecycle_event
    return any(
        (
            transition.before_anchor_id != transition.after_anchor_id,
            transition.support_before != transition.support_after,
            transition.contact_before != transition.contact_after,
            transition.containment_before != transition.containment_after,
            lifecycle is not None and lifecycle.value != "persist",
            transition.topology_change.value != "none",
        )
    )


def describe_boundaries(episode: EpisodePlan) -> str:
    assert episode.visible_world is not None
    if not episode.visible_world.shot_boundary_states:
        return "没有切镜边界，动作在同一连续空间内完成。"
    cuts = "、".join(
        f"{item.after_shot_order}→{item.next_shot_order}"
        for item in episode.visible_world.shot_boundary_states
    )
    return f"切镜{cuts}继承一人一猫、服装层、位置和已出现的持久物。"


def _reference_role_label(role: str) -> str:
    return {
        "person": "固定人物身份与外观",
        "cat": "固定灰白猫身份与斑纹",
        "style": "系列画风、材质与色彩",
        "element": "关键道具的颜色、形状与材质",
        "scene": "场景空间与构图",
        "motion": "动作和运镜",
        "atmosphere": "声音氛围和节奏",
        "first_frame": "开场状态",
        "last_frame": "结尾状态",
    }.get(role, role)


def _duplicated_element_ids(episode: EpisodePlan) -> set[str]:
    """element_uses 中与某个关键关系 subject 描述同一物体的 element_id。"""

    subjects = [item.subject for item in episode.critical_relations]
    return {
        item.element_id
        for item in episode.element_uses
        if any(item.element_id in subject or subject in item.element_id for subject in subjects)
    }


def describe_relations(episode: EpisodePlan, *, final: bool) -> str:
    duplicated = _duplicated_element_ids(episode)
    states = [
        f"{item.subject}：{item.final_state if final else item.initial_state}"
        for item in episode.critical_relations
    ]
    states.extend(
        f"{item.element_id}：{item.final_state if final else item.initial_state}"
        for item in episode.element_uses
        if item.element_id not in duplicated
    )
    return "；".join(states) or "保持一人一猫，遵守当前场景真实边界"


def _compiled(text: str) -> CompiledPrompt:
    count = len(text)
    warnings = ("prompt_length_warning",) if count > VIDEO_PROMPT_WARNING_CHARS else ()
    return CompiledPrompt(
        text=text,
        char_count=count,
        utf8_bytes=len(text.encode("utf-8")),
        warnings=warnings,
    )
