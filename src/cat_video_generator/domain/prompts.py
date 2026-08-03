"""总导演、时段导演、图片、视频与审核Prompt的唯一编译模块。

规划上下文可以完整；Seedance只接收当前Episode的执行信息。所有编译器均为纯函数，
Prompt会先持久化再交给Ark，因而这里不能读取数据库或本地文件。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from .continuity import EntityState, replay_world
from .contracts import (
    DayBrief,
    EpisodePlan,
    RecentContentSummary,
    SlotBrief,
)
from .rendering import MediaBinding, VideoInputPlan
from .visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
    SeriesVisualProfile,
    StyleProfile,
)

VIDEO_PROMPT_WARNING_CHARS = 1400
VIDEO_PROMPT_BLOCKING_CHARS = 1600


class PromptBudgetError(ValueError):
    """执行Prompt超出收费调用允许的注意力预算。"""


class PromptCompilationError(ValueError):
    """合法业务对象无法安全投影为供应商Prompt。"""


@dataclass(frozen=True, slots=True)
class CompiledPrompt:
    text: str
    char_count: int
    utf8_bytes: int
    warnings: tuple[str, ...]


def compile_day_director_prompt(
    *,
    target_date: date,
    planning_context: str,
    recent_summaries: tuple[RecentContentSummary, ...] = (),
    event_seeds: tuple[str, ...] = (),
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
) -> str:
    """生成只输出DayBrief的总导演Prompt。"""

    recent = "；".join(item.summary_text for item in recent_summaries[-6:]) or "无"
    seeds = "；".join(event_seeds[:3]) or "无适用事件种子，可原创"
    return "\n".join(
        (
            "你是持续角色生活流短视频的总导演。只输出一个符合JSON Schema的"
            "DayBrief，不输出Episode、分镜、解释或候选数组。",
            f"内容日期：{target_date.isoformat()}。当天输入：{planning_context}。",
            f"近期已批准内容：{recent}。适用事件种子：{seeds}。",
            "固定主体为同一个中性儿童和同一只灰白猫。只规划全天主题、天气、"
            "地点范围、真正跨时段复用的元素及早中晚边界，不替时段导演写动作。",
            f"人物长期身份：{series_profile.person_identity}；{series_profile.person_hair}；"
            f"{series_profile.person_body}。",
            "共享元素使用element:*或scene:*语义键；没有跨时段复用就保持空数组。"
            "服装、鞋帽、背包和配饰服从场景，变化时给出天气、地点或事件原因。",
            "早中晚可以独立或局部承接，不强制准备—完成—归家。slots必须严格按"
            "morning、noon、evening排序，自然语言字段全部使用中文。",
        )
    )


def compile_episode_director_prompt(
    *,
    day_brief: DayBrief,
    slot_brief: SlotBrief,
    previous_state_summaries: tuple[str, ...],
    retry_reason: str | None = None,
    rejected_candidate: dict[str, object] | None = None,
    validation_errors: tuple[str, ...] = (),
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
) -> str:
    """生成只输出一个EpisodeScript的时段导演Prompt。"""

    previous = "；".join(previous_state_summaries) or "当天第一条，无前序状态"
    repair = ""
    if rejected_candidate is not None:
        repair = (
            "上次候选存在确定性矛盾，必须重新输出完整EpisodeScript而不是局部补丁。"
            f"错误：{'；'.join(validation_errors)}。上次候选："
            f"{json.dumps(rejected_candidate, ensure_ascii=False, separators=(',', ':'))}"
        )
    retry = f"局部重规划原因：{retry_reason}。" if retry_reason else ""
    return "\n".join(
        (
            f"你是{slot_brief.slot.value}时段导演。只输出符合JSON Schema的一个"
            "EpisodeScript，不输出slot、全天方案、解释或Markdown。",
            "DayBrief："
            + json.dumps(day_brief.model_dump(mode="json"), ensure_ascii=False),
            "本时段边界："
            + json.dumps(slot_brief.model_dump(mode="json"), ensure_ascii=False),
            f"前序真实终态摘要：{previous}。{retry}{repair}",
            "设计一条8至15秒视频：一个主事件、2至4个连续动作阶段、1至3个镜头和"
            "一个可见收束。动作可以丰富但必须服务同一目标，结尾不得静止互看填时长。",
            "动作主体直接使用VisibleWorld实体ID（固定人物为person、灰白猫为cat）；"
            "每个动作只声明一次actorId。镜头只用actionOrders映射动作，每镜一种运镜。",
            "VisibleWorld先登记锚点与所有可见关键实体。每个实体提供完整initialState。"
            "发生状态变化时，在对应ActionStage.transitions中提供完整before、after和"
            "reason；无状态变化时transitions保持空数组，不能创建空壳Transition。",
            "before必须精确等于前序终态。活动实体必须位于锚点、合法支撑或容器中；"
            "离场或消耗后active=false且不再占用空间。道具不得无原因出现、消失、"
            "复制或变形；坐下前必须登记seat锚点。",
            "真正跨时段元素必须在实体semanticKey中使用DayBrief声明的同一语义键。"
            "人物、猫咪和画风参考由系统固定加入，脚本不声明参考角色或素材列表。",
            f"人物保持{series_profile.person_identity}；{series_profile.person_hair}；"
            f"{series_profile.person_body}。猫保持{series_profile.cat_identity}。"
            "服装、鞋帽和背包按剧情自然变化。",
            "videoInputMode默认multimodal_reference；只有开场必须精确时使用"
            "strict_first_frame，开场和结果都必须精确时使用strict_first_last。",
        )
    )


def compile_image_prompt(
    episode: EpisodePlan,
    *,
    target: str,
    reference_roles: tuple[str, ...] = (),
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    retry_feedback: str | None = None,
    **_: object,
) -> CompiledPrompt:
    """为首帧、尾帧或元素图生成聚焦的9:16 Seedream Prompt。"""

    if target not in {"first_frame", "last_frame", "element"}:
        raise ValueError(f"不支持的图片目标{target}")
    script = episode.script
    final = target == "last_frame"
    references = "；".join(
        f"图{index}负责{role}" for index, role in enumerate(reference_roles, 1)
    ) or "没有额外参考图"
    state = _describe_world_state(episode, final=final)
    intended = script.ending if final else script.actions[0].action
    lines = (
        f"【任务】生成{target}的9:16竖屏单幅参考图。",
        f"【素材职责】{references}。",
        f"【画风】{style_profile.prompt_positive()}；排除{style_profile.prompt_negative()}。",
        f"【主体】{series_profile.person_identity}；{series_profile.person_hair}；"
        f"{series_profile.cat_identity}。",
        f"【场景与外观】{script.scene}；{script.appearance.description}。",
        f"【画面动作与状态】{intended}；{state}。",
        "【硬约束】一人一猫；关键实体数量、支撑、容器和外观签名符合状态账本；"
        "无黑边、多视图、文字、Logo、UI或明显3D商业动画质感。",
    )
    text = "\n".join(lines)
    if retry_feedback:
        text += f"\n【重试修正】保留旧图审计，本次必须修正：{retry_feedback}。"
    return _compiled(text)


def compile_video_prompt(
    episode: EpisodePlan,
    *,
    input_plan: VideoInputPlan,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
    **_: object,
) -> CompiledPrompt:
    """生成当前Episode唯一五段式Seedance执行Prompt。"""

    if input_plan.duration_seconds != episode.script.duration_seconds:
        raise ValueError("VideoInputPlan时长与EpisodeScript不一致")
    return _compile_video_body(
        episode,
        resolution=input_plan.resolution,
        duration_seconds=input_plan.duration_seconds,
        bindings=_describe_bindings(input_plan.bindings),
        style_profile=style_profile,
    )


def compile_video_prompt_preview(
    episode: EpisodePlan,
    *,
    resolution: str,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> CompiledPrompt:
    """规划验收复用正式内核，提前阻断超预算Prompt。"""

    if resolution not in {"480p", "720p"}:
        raise ValueError("视频分辨率只允许480p或720p")
    return _compile_video_body(
        episode,
        resolution=resolution,
        duration_seconds=episode.script.duration_seconds,
        bindings="人物、灰白猫、画风和本集必要元素按最终素材顺序绑定。",
        style_profile=style_profile,
    )


def compile_keyframe_review_prompt(
    episode: EpisodePlan,
    *,
    target: str,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> str:
    """生成关键帧身份、画风和世界终态语义审核Prompt。"""

    if target not in {"first_frame", "last_frame"}:
        raise ValueError("关键帧审核target必须是first_frame或last_frame")
    return "\n".join(
        (
            "你是关键帧语义审核器，只返回给定JSON Schema。",
            f"身份：{series_profile.person_identity}；{series_profile.person_hair}；"
            f"{series_profile.cat_identity}。",
            f"画风：{style_profile.prompt_positive()}；不得出现"
            f"{style_profile.prompt_negative()}。",
            f"目标状态：{_describe_world_state(episode, final=target == 'last_frame')}。",
            "检查实体数量、支撑、容器、座位、外观签名和空间边界。证据必须描述"
            "图片中的实际观察；未知家具、悬空、穿透、形变或持久物缺失应明确拒绝。",
        )
    )


def compile_video_diagnostic_prompt(episode: EpisodePlan) -> str:
    """生成最终视频抽帧诊断Prompt；诊断不自动批准成片。"""

    script = episode.script
    actions = "；".join(
        f"{item.order}.{_actor_name(item.actor_id)}{item.action}" for item in script.actions
    )
    entities = "；".join(
        f"{item.id}={item.name}/{item.initial_state.appearance_signature}"
        for item in script.visible_world.entities
    )
    return "\n".join(
        (
            "你是生活流短视频抽帧诊断器，图片按时间顺序排列，只返回给定JSON。",
            f"实体：{entities}。动作：{actions}。结尾：{script.ending}。",
            "检查同一人物和灰白猫、二维绘本风格、持久实体与服装连续性、支撑和"
            "容器关系、动作先后。单帧遮挡不等于消失；证据必须包含帧序号。",
        )
    )


def summarize_episode_state(episode: EpisodePlan) -> str:
    """把真实重放终态压缩给下一个时段导演。"""

    result = replay_world(episode.script.visible_world, episode.script.actions)
    if result.issues:
        raise PromptCompilationError("不一致世界不能生成前序摘要")
    states = "、".join(
        f"{entity_id}@{state.anchor_id or state.container_id or '离场'}:"
        f"{'active' if state.active else 'inactive'}:{state.appearance_signature}"
        for entity_id, state in sorted(result.states.items())
    )
    return (
        f"{episode.slot.value}结尾={episode.script.ending}，"
        f"外观={episode.script.appearance.description}，可见世界={states}"
    )


def _compile_video_body(
    episode: EpisodePlan,
    *,
    resolution: str,
    duration_seconds: int,
    bindings: str,
    style_profile: StyleProfile,
) -> CompiledPrompt:
    script = episode.script
    shot_lines = "\n".join(
        _compile_shot(episode, order)
        for order in range(1, len(script.shots) + 1)
    )
    transitions = _describe_transitions(episode)
    text = "\n".join(
        (
            "【输出、画风与素材绑定】"
            f"{bindings}输出{resolution}、9:16竖屏、{duration_seconds}秒完整成片。"
            f"{style_profile.prompt_positive()}；排除{style_profile.prompt_negative()}。",
            "【人物、猫咪、外观和空间】保持参考中的同一个中性儿童和同一只灰白猫；"
            f"本集外观：{script.appearance.description}。场景：{script.scene}。",
            f"【顺序动作】\n{shot_lines}\n结尾可见结果：{script.ending}。",
            f"【可见世界状态与切镜连续性】{transitions}切镜自动继承上一动作终态。",
            "【原生声音和硬禁止】自然环境声与动作声，无对白、旁白或歌词。禁止角色"
            "增减或分身；禁止关键实体凭空出现、消失、复制或变形；禁止物体穿透或"
            "无支撑悬空；禁止切镜后服装或持久物无原因变化；禁止字幕、水印、Logo、UI。",
        )
    )
    compiled = _compiled(text)
    if compiled.char_count > VIDEO_PROMPT_BLOCKING_CHARS:
        raise PromptBudgetError("视频Prompt超过1600字符，必须重新规划或去重")
    return compiled


def _compile_shot(episode: EpisodePlan, order: int) -> str:
    script = episode.script
    shot = next((item for item in script.shots if item.order == order), None)
    if shot is None:
        raise PromptCompilationError(f"不存在镜头{order}")
    stages = [item for item in script.actions if item.order in shot.action_orders]
    actions = "；随后".join(
        f"{_actor_name(item.actor_id)}执行：{item.action}" for item in stages
    )
    return (
        f"镜头{shot.order}：{_camera_name(shot.camera_move.value)}，{shot.framing}，"
        f"{shot.direction}；{actions}。"
    )


def _describe_bindings(bindings: list[MediaBinding]) -> str:
    if not bindings:
        return "不使用额外参考素材。"
    return "；".join(
        f"{item.prompt_alias}={item.semantic_key}/{item.provider_role.value}"
        for item in bindings
    ) + "。"


def _describe_world_state(episode: EpisodePlan, *, final: bool) -> str:
    script = episode.script
    if final:
        result = replay_world(script.visible_world, script.actions)
        if result.issues:
            raise PromptCompilationError("不一致世界不能生成尾帧描述")
        states = result.states
    else:
        states = {
            item.id: item.initial_state for item in script.visible_world.entities
        }
    anchors = {item.id: item.name for item in script.visible_world.anchors}
    entities = {item.id: item.name for item in script.visible_world.entities}
    return "；".join(
        _state_line(entity_id, state, anchors, entities)
        for entity_id, state in sorted(states.items())
    )


def _state_line(
    entity_id: str,
    state: EntityState,
    anchors: dict[str, str],
    entities: dict[str, str],
) -> str:
    name = entities[entity_id]
    if not state.active:
        return f"{name}已离场或消耗"
    position = anchors.get(state.anchor_id or "", state.anchor_id or "")
    support = anchors.get(
        state.support_id or "",
        entities.get(state.support_id or "", state.support_id or ""),
    )
    container = entities.get(state.container_id or "", state.container_id or "")
    relations = [
        item
        for item in (
            position,
            f"由{support}支撑" if support else "",
            f"在{container}内" if container else "",
        )
        if item
    ]
    return f"{name}位于{'，'.join(relations)}，外观{state.appearance_signature}"


def _describe_transitions(episode: EpisodePlan) -> str:
    lines: list[str] = []
    entities = {item.id: item.name for item in episode.script.visible_world.entities}
    anchors = {item.id: item.name for item in episode.script.visible_world.anchors}
    for action in episode.script.actions:
        for transition in action.transitions:
            before = _state_topology(transition.before, anchors, entities)
            after = _state_topology(transition.after, anchors, entities)
            # 仅表情、姿态或数量等可见变化已经在动作及visibleResult中表达。
            # 这里跳过拓扑不变项，避免连续性段再次复述同一动作。
            if before == after:
                continue
            lines.append(
                f"动作{action.order}中{entities[transition.entity_id]}因{transition.reason}从"
                f"{before}变为{after}。"
            )
    return "".join(lines) or "关键实体保持初始位置、支撑、容器和外观。"


def _state_topology(
    state: EntityState,
    anchors: dict[str, str],
    entities: dict[str, str],
) -> str:
    """只投影物理拓扑，避免把完整外观签名重复塞入视频Prompt。

    动作文字已经描述可见变化，外观由本集外观段统一锁定；连续性段只负责
    位置、支撑和容器这些容易导致悬空或穿透的关系。
    """

    if not state.active:
        return "非活动状态"
    anchor = anchors.get(state.anchor_id or "", state.anchor_id or "")
    support = anchors.get(
        state.support_id or "",
        entities.get(state.support_id or "", state.support_id or ""),
    )
    container = entities.get(state.container_id or "", state.container_id or "")
    return "/".join(
        value
        for value in (
            f"位置={anchor}" if anchor else None,
            f"支撑={support}" if support else "由动作主体持续持有",
            f"容器={container}" if container else None,
        )
        if value
    )


def _actor_name(actor_id: str) -> str:
    return {"person": "人物", "cat": "灰白猫", "environment": "环境"}.get(
        actor_id,
        actor_id,
    )


def _camera_name(value: str) -> str:
    return {
        "fixed": "固定镜头",
        "follow": "平稳跟拍",
        "push": "缓慢推进",
        "pull": "缓慢拉远",
        "pan": "缓慢摇摄",
        "track": "平稳横移",
    }[value]


def _compiled(text: str) -> CompiledPrompt:
    count = len(text)
    return CompiledPrompt(
        text=text,
        char_count=count,
        utf8_bytes=len(text.encode("utf-8")),
        warnings=("prompt_length_warning",) if count > VIDEO_PROMPT_WARNING_CHARS else (),
    )
