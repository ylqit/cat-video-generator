"""总导演、时段导演、图片和视频 Prompt 的确定性编译器。

完整规划保存在数据库；Seedance 只接收当前 Episode 的执行信息。这样既保留
可审计上下文，也避免把导演理由、评分和长期记忆重复塞进视频 Prompt。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from .contracts import (
    DayBrief,
    EpisodePlan,
    MediaBinding,
    MediaPurpose,
    ShotPlan,
    SlotBrief,
    VideoInputPlan,
)

VIDEO_PROMPT_WARNING_CHARS = 1400
VIDEO_PROMPT_BLOCKING_CHARS = 1600
VIDEO_PROMPT_REPLAN_CHARS = 2000


class PromptBudgetError(ValueError):
    """执行 Prompt 超过收费调用允许的注意力预算。"""


@dataclass(frozen=True, slots=True)
class CompiledPrompt:
    """编译后的 Prompt 及预算诊断。"""

    text: str
    char_count: int
    utf8_bytes: int
    warnings: tuple[str, ...]


def compile_day_director_prompt(
    *,
    target_date: date,
    planning_context: str,
    recent_summaries: tuple[str, ...] = (),
) -> str:
    """生成一次只输出 DayBrief 的总导演 Prompt。"""

    recent = "；".join(recent_summaries[-6:]) or "无"
    return "\n".join(
        (
            "你是持续角色生活流短视频的总导演。只输出一个符合给定JSON Schema的"
            "DayBrief，不输出Episode、分镜、解释、Markdown或候选数组。",
            f"内容日期：{target_date.isoformat()}。",
            f"当天输入：{planning_context}。",
            f"近期内容：{recent}。",
            "固定主角是同一个人物和同一只灰白猫。你只决定全天主题、共同背景、"
            "真正需要跨时段保持的共享元素，以及早中晚各自的叙事目的、场景方向、"
            "事件方向和外观意图。不要替时段导演写具体动作时间线。",
            "早中晚是同一天的三个生活观察窗口，可以独立，也可以局部承接；不要"
            "强制准备—完成—归家。服装、鞋帽、配饰和随身物品必须服务场景：需要"
            "拖鞋就换拖鞋，不需要背包就不出现；相邻时段有变化时在方向中给出天气、"
            "地点或事件原因。",
            "slots严格按morning、noon、evening排序。所有自然语言字段使用中文；"
            "只有slot和element_id等契约标识保留英文。",
        )
    )


def compile_episode_director_prompt(
    *,
    day_brief: DayBrief,
    slot_brief: SlotBrief,
    previous_state_summaries: tuple[str, ...],
    retry_reason: str | None = None,
) -> str:
    """为一个时段生成一次只输出 EpisodePlan 的导演 Prompt。"""

    previous = "；".join(previous_state_summaries) or "这是当天第一条，无前序状态"
    retry = (
        f"本次是局部重规划，必须修正：{retry_reason}。"
        if retry_reason
        else "本次不是重试。"
    )
    brief_json = json.dumps(
        day_brief.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    slot_json = json.dumps(
        slot_brief.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "\n".join(
        (
            f"你是{slot_brief.slot.value}时段导演。只输出一个符合给定JSON Schema的"
            "EpisodeDirectorDraft，不输出全天方案、解释、Markdown或候选数组。",
            f"不可改写的DayBrief：{brief_json}",
            f"本时段边界：{slot_json}",
            f"已发生状态摘要：{previous}。",
            retry,
            "把边界细化为一条8至15秒视频：一个主事件、2至4个连续动作阶段和一个"
            "可见收束。另提供1至3个轻量镜头，每个镜头关联动作阶段且只能使用一种"
            "主要运镜。动作结束后不能用站立互看或静止画面填时长。",
            "固定出现一个人物和一只灰白猫，最多一个剧情确需的配角。人物主要面貌、"
            "发型和体型保持同一人；猫保持同一只灰白猫的脸型、体型和主要灰白斑纹。"
            "眼睛服从参考素材整体画风，不新增独立的眼睛硬约束。",
            "共享元素只能引用DayBrief已定义且包含本slot的element_id。每个引用必须"
            "填写element_uses，明确用途、初态和终态。关键数量、承重、包含、边界或"
            "交接关系必须填写critical_relations的initial_state和final_state。",
            "appearance必须落实本时段外观；morning使用continue且不声明相对变化，"
            "noon/evening若换装、换鞋或增减随身物品则使用changed并给出具体变化与"
            "情景原因。slot、cast、required_reference_roles和video_input_mode由本地"
            "系统确定，不得在输出中添加。所有自然语言字段使用中文。",
        )
    )


def summarize_episode_state(episode: EpisodePlan) -> str:
    """提取下一个时段真正需要读取的已发生状态。"""

    element_states = "、".join(
        f"{item.element_id}={item.final_state}" for item in episode.element_uses
    )
    relation_states = "、".join(
        f"{item.subject}:{item.final_state}" for item in episode.critical_relations
    )
    parts = [
        f"{episode.slot.value}结尾={episode.ending}",
        f"外观={episode.appearance.description}",
    ]
    if element_states:
        parts.append(f"共享元素={element_states}")
    if relation_states:
        parts.append(f"关键关系={relation_states}")
    return "，".join(parts)


def compile_image_prompt(
    episode: EpisodePlan,
    *,
    target: str,
    reference_roles: tuple[str, ...] = (),
) -> CompiledPrompt:
    """为首帧或尾帧生成聚焦图片 Prompt。"""

    if target not in {"first_frame", "last_frame", "element"}:
        raise ValueError(f"不支持的图片目标: {target}")
    state = episode.actions[0].action if target == "first_frame" else episode.ending
    relation_state = _relation_lines(episode, final=target == "last_frame")
    reference_line = "；".join(
        f"图{index}负责{_reference_role_label(role)}"
        for index, role in enumerate(reference_roles, start=1)
    )
    text = "\n".join(
        (
            f"【任务】生成{target}竖屏参考图。",
            (
                f"【参考素材】{reference_line}。"
                if reference_line
                else "【参考素材】没有额外参考图。"
            ),
            "【画风与身份】沿用参考图中的系列画风、同一个人物和同一只灰白猫；"
            "人物主要面貌、发型和体型可辨识为同一人，猫保持主要灰白斑纹。",
            f"【场景与外观】{episode.scene}；{episode.appearance.description}",
            f"【画面状态】{state}",
            f"【关键关系】{relation_state}",
            "【禁止】额外人物或动物、分身、多视图、文字、Logo、UI；不得凭空改变"
            "关键物体的形状、支撑、包含或空间边界。",
        )
    )
    return _compiled(text)


def compile_video_prompt(
    episode: EpisodePlan,
    *,
    input_plan: VideoInputPlan,
) -> CompiledPrompt:
    """按场景复杂度生成Seedance简单路径或复杂三段式Prompt。"""

    if input_plan.duration_seconds != episode.duration_seconds:
        raise ValueError("输入计划时长与Episode不一致")
    bindings = _binding_definitions(input_plan.bindings)
    relations = _relation_transition_lines(episode)
    if _is_simple_episode(episode):
        action_text = "随后".join(
            f"{stage.action}，画面明确出现{stage.visible_result}"
            for stage in episode.actions
        )
        text = (
            f"{bindings}生成{input_plan.resolution}、9:16竖屏、"
            f"{input_plan.duration_seconds}秒的单一连续生活场景。"
            "全程只有同一个人物和同一只灰白猫，人物主要面貌、发型和体型可辨识"
            "为同一人，猫保持脸型、体型和主要灰白斑纹。"
            f"人物外观为{episode.appearance.description}；场景为{episode.scene}。"
            f"{action_text}。关键关系：{relations}。最后{episode.ending}。"
            "原生环境声和动作声自然同步，无对白、旁白或歌词。画面稳定，不生成"
            "分身、额外动物、无支撑悬空、字幕、水印、Logo或供应商UI。"
        )
    else:
        shots = episode.shots or _legacy_shots(episode)
        shot_text = "\n".join(_compile_shot(shot, episode) for shot in shots)
        text = "\n".join(
            (
                "【整体设定与素材绑定】"
                f"{bindings}输出{input_plan.resolution}、9:16竖屏、"
                f"{input_plan.duration_seconds}秒单次完整成片。全程只有同一个人物和"
                "同一只灰白猫；人物主要面貌、发型和体型可辨识为同一人，猫保持"
                f"脸型、体型和主要灰白斑纹。外观：{episode.appearance.description}。"
                f"场景：{episode.scene}。",
                f"【镜头顺序】\n{shot_text}\n最后的可见回报：{episode.ending}。",
                "【质量、物理与声音】"
                f"关键关系只按以下状态变化：{relations}。原生环境声和动作声随画面"
                "同步，无对白、旁白或歌词。画面稳定、角色数量固定，禁止分身、额外"
                "动物、瞬移、无原因换装、关键物体悬空或自动恢复、字幕、水印、Logo"
                "和供应商UI；结尾不得用原地互看或完全静止填充时长。",
            )
        )
    compiled = _compiled(text)
    if compiled.char_count > VIDEO_PROMPT_REPLAN_CHARS:
        raise PromptBudgetError("视频Prompt超过2000字符，必须重新规划或拆分事件")
    if compiled.char_count > VIDEO_PROMPT_BLOCKING_CHARS:
        raise PromptBudgetError("视频Prompt超过1600字符，必须先去重后才能付费生成")
    return compiled


def _is_simple_episode(episode: EpisodePlan) -> bool:
    """时间和空间维度都较低时使用一段式Prompt。"""

    return (
        len(episode.actions) <= 2
        and len(episode.shots) <= 1
        and len(episode.critical_relations) <= 1
    )


def _binding_definitions(bindings: list[MediaBinding]) -> str:
    if not bindings:
        return "不使用额外参考素材。"
    definitions: list[str] = []
    for item in bindings:
        if item.source_role == "person":
            definitions.append(f"将{item.prompt_alias}中的人物定义为<主体1>")
        elif item.source_role == "cat":
            definitions.append(f"将{item.prompt_alias}中的灰白猫定义为<主体2>")
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
            definitions.append(
                f"{item.prompt_alias}负责{_reference_role_label(item.source_role)}"
            )
    return "；".join(definitions) + "。"


def _compile_shot(shot: ShotPlan, episode: EpisodePlan) -> str:
    stages = [stage for stage in episode.actions if stage.order in shot.action_orders]
    actions = "，随后".join(
        f"{stage.action}，结果为{stage.visible_result}" for stage in stages
    )
    return (
        f"镜头{shot.order}：{_camera_label(shot.camera_move.value)}，"
        f"{shot.framing}，{shot.direction}；<主体1>与<主体2>{actions}。"
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


def _relation_lines(episode: EpisodePlan, *, final: bool) -> str:
    states = [
        f"{item.subject}：{item.final_state if final else item.initial_state}"
        for item in episode.critical_relations
    ]
    states.extend(
        f"{item.element_id}：{item.final_state if final else item.initial_state}"
        for item in episode.element_uses
    )
    return "；".join(states) or "保持一人一猫，遵守当前场景真实边界"


def _relation_transition_lines(episode: EpisodePlan) -> str:
    lines = [
        (f"{item.subject}({item.relation})：{item.initial_state} → {item.final_state}")
        for item in episode.critical_relations
    ]
    lines.extend(
        f"{item.element_id}：{item.initial_state} → {item.final_state}"
        for item in episode.element_uses
    )
    return "；".join(lines) or "一人一猫数量固定，角色始终位于当前真实空间内"


def _compiled(text: str) -> CompiledPrompt:
    count = len(text)
    warnings = ("prompt_length_warning",) if count > VIDEO_PROMPT_WARNING_CHARS else ()
    return CompiledPrompt(
        text=text,
        char_count=count,
        utf8_bytes=len(text.encode("utf-8")),
        warnings=warnings,
    )
