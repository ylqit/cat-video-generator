"""全天导演、时段导演、故事板、视频与媒体审核Prompt编译。

导演上下文可以完整；Seedream负责把脚本视觉化为有序独立面板；Seedance只接收
当前Episode、已批准故事板和少量关键连续性。字符数仅用于展示，不作为本地准入门。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from .continuity import EntityKind, EntityState, PlacementKind, validate_continuity
from .contracts import DayBrief, EpisodePlan, RecentContentSummary, SlotBrief
from .rendering import MediaBinding, VideoInputPlan
from .visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
    SeriesVisualProfile,
    StyleProfile,
)


class PromptCompilationError(ValueError):
    """业务对象无法安全投影为供应商Prompt。"""


@dataclass(frozen=True, slots=True)
class CompiledPrompt:
    """Prompt正文和只读统计；统计值不会阻断生成。"""

    text: str
    char_count: int
    utf8_bytes: int
    warnings: tuple[str, ...] = ()


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
            "共享元素只使用逻辑entity_key，不要写数据库semantic_key，不要把固定人物、"
            "固定猫咪或普通场景重复声明为共享元素；没有跨时段关键道具就保持空数组。"
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
    """生成只输出一个故事板友好EpisodeScript的时段导演Prompt。"""

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
            "DayBrief：" + json.dumps(day_brief.model_dump(mode="json"), ensure_ascii=False),
            "本时段边界：" + json.dumps(slot_brief.model_dump(mode="json"), ensure_ascii=False),
            f"前序真实终态摘要：{previous}。{retry}{repair}",
            "设计一条8至15秒生活流视频：一个主事件、2至4个连续动作阶段、1至3个"
            "镜头和一个可见收束。每个动作必须适合转成一张清楚的独立故事板图，"
            "结尾不得靠静止互看填时长。",
            "动作主体使用SceneContinuity实体ID（固定人物person、灰白猫cat）；"
            "ActionStage只保存order、actor_id、action和visible_result。停步、转头、"
            "蹲下、嗅闻、走动等姿态只写入动作文本。",
            "SceneContinuity只登记人物、猫咪、被操作或跨镜头延续的关键道具，以及"
            "真正参与承重的桌面、座椅等锚点。普通植物、屋檐、远山和装饰不要建账。",
            "每个实体声明kind、逻辑entity_key、start_state、end_state、lifecycle和稳定"
            "form_key。form_key只表示类别或固定外观，禁止加入crouch、sniffing、walking等"
            "姿势。人物和猫咪固定persist；其他实体按persist、enter、exit、consume或"
            "transform声明起终态，变化时说明原因。",
            "首个动作里提到的人物、猫咪和关键道具起点必须逐项等于对应实体的start_state；"
            "不得同时写成位于窗台和来自地面等互相冲突的位置。后续动作与end_state也必须"
            "保持同一空间语义。",
            "关键实体不得无原因出现、消失、复制或改变类别；承担发现或结尾回报的实体"
            "必须登记，并在ending.key_entity_ids中引用。跨时段道具使用DayBrief中的同一"
            "entity_key。导演不得生成数据库资产semantic_key。",
            f"人物保持{series_profile.person_identity}；{series_profile.person_hair}；"
            f"{series_profile.person_body}。猫保持{series_profile.cat_identity}。"
            "服装、鞋帽和背包按剧情自然变化。appearance直接描述本时段外观；"
            "若相对前一时段有变化，列入changes_from_previous并给出change_reason，"
            "没有变化时两者保持空值。",
            "ending必须给出result和key_entity_ids；key_entity_ids可引用continuity中已登记的"
            "实体或场景锚点；只有结尾物体状态或构图必须精确锁定"
            "时，才将visual_critical设为true。脚本不选择Seedance输入模式，也不声明"
            "参考素材。",
        )
    )


def storyboard_panel_count(episode: EpisodePlan) -> int:
    """按动作数量确定组图数量；一次Episode永远只产生一个组图任务。"""

    return 3 if len(episode.script.actions) == 2 else 4


def compile_storyboard_prompt(
    episode: EpisodePlan,
    *,
    reference_roles: tuple[str, ...] = (),
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    retry_feedback: str | None = None,
) -> CompiledPrompt:
    """生成一次Seedream多图序列Prompt，返回3至4张独立无字竖图。"""

    references = (
        "；".join(f"图{index}只负责{role}" for index, role in enumerate(reference_roles, 1))
        or "没有额外参考图"
    )
    panels = _storyboard_panels(episode)
    panel_lines = "\n".join(
        f"面板{index}：{description}" for index, description in enumerate(panels, 1)
    )
    script = episode.script
    text = "\n".join(
        (
            f"【任务】一次生成{len(panels)}张相互连贯但彼此独立的9:16竖屏故事板图；"
            "每张都是完整画面，不要拼成网格。",
            f"【参考职责】{references}。",
            f"【固定主体】{series_profile.person_identity}；{series_profile.person_hair}；"
            f"{series_profile.cat_identity}。全组始终准确一人一猫。",
            f"【画风】{style_profile.prompt_positive()}；排除{style_profile.prompt_negative()}。",
            f"【场景与服饰】{script.scene}；{script.appearance.description}。",
            f"【有序面板】\n{panel_lines}",
            "【连续性】跨面板保持同一人物、同一灰白猫、服装、关键实体稳定formKey和"
            "数量；动作过程自然连接，并最终符合脚本声明的起终态与生命周期。",
            "【禁止】不得包含文字、序号、对白框、边框、九宫格、UI、Logo、水印、"
            "角色分身、明显3D/PBR质感或与面板顺序冲突的状态。",
        )
    )
    if retry_feedback:
        text += f"\n【重试修正】保留旧组图审计，本次必须修正：{retry_feedback}。"
    return _compiled(text)


def compile_video_prompt(
    episode: EpisodePlan,
    *,
    input_plan: VideoInputPlan,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
    **_: object,
) -> CompiledPrompt:
    """根据最终有序故事板输入生成五段式Seedance执行Prompt。"""

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
    """不绑定真实资产的执行Prompt预览；长度仅用于Web展示。"""

    if resolution not in {"480p", "720p"}:
        raise ValueError("视频分辨率只允许480p或720p")
    count = storyboard_panel_count(episode)
    bindings = "；".join(f"@图片{i}=故事板面板{i}" for i in range(1, count + 1)) + "。"
    return _compile_video_body(
        episode,
        resolution=resolution,
        duration_seconds=episode.script.duration_seconds,
        bindings=bindings,
        style_profile=style_profile,
    )


def compile_storyboard_review_prompt(
    episode: EpisodePlan,
    *,
    panel_count: int,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> str:
    """一次审核整组面板，结果必须对全部面板原子生效。"""

    if panel_count != storyboard_panel_count(episode):
        raise ValueError("故事板审核数量与Episode动作数不一致")
    actions = "；".join(
        f"{item.order}.{_actor_name(item.actor_id)}{item.action}→{item.visible_result}"
        for item in episode.script.actions
    )
    continuity = _describe_continuity(episode)
    return "\n".join(
        (
            f"你是故事板组图审核器。输入{panel_count}张图片按面板顺序排列，只返回给定JSON。",
            f"身份：{series_profile.person_identity}；{series_profile.person_hair}；"
            f"{series_profile.cat_identity}。",
            f"画风：{style_profile.prompt_positive()}；不得出现{style_profile.prompt_negative()}。",
            f"动作链：{actions}。关键起终态：{continuity}。"
            f"结尾：{episode.script.ending.result}。",
            "硬失败只包括：人物或猫咪身份/数量错误、明显3D画风、动作错序、关键道具"
            "复制消失或变类、实际坐下却没有前序座位、同场景服装断裂、最后一张未兑现"
            "结尾。普通背景、次要植物、轻微姿势、构图或面貌差异写入warnings，不应让"
            "布尔硬门失败。证据必须指出面板序号。",
        )
    )


def compile_video_diagnostic_prompt(episode: EpisodePlan) -> str:
    """生成最终视频抽帧诊断Prompt；诊断不自动批准成片。"""

    actions = "；".join(
        f"{item.order}.{_actor_name(item.actor_id)}{item.action}" for item in episode.script.actions
    )
    entities = _describe_continuity(episode)
    return "\n".join(
        (
            "你是生活流短视频抽帧诊断器，图片按时间顺序排列，只返回给定JSON。",
            f"关键实体：{entities}。动作：{actions}。结尾：{episode.script.ending.result}。",
            "检查同一人物和灰白猫、二维绘本风格、关键实体与服装连续性、动作先后和"
            "结尾兑现。单帧遮挡不等于消失；证据必须包含帧序号。",
        )
    )


def summarize_episode_state(episode: EpisodePlan) -> str:
    """把关键实体真实终态压缩给下一个时段导演。"""

    result = validate_continuity(episode.script.continuity)
    if result.issues:
        raise PromptCompilationError("不一致场景连续性不能生成前序摘要")
    states = "、".join(
        f"{entity_id}@{_placement_text(state)}"
        for entity_id, state in sorted(result.final_states.items())
    )
    return (
        f"{episode.slot.value}结尾={episode.script.ending.result}，"
        f"外观={episode.script.appearance.description}，关键实体={states}"
    )


def _storyboard_panels(episode: EpisodePlan) -> tuple[str, ...]:
    script = episode.script
    opening = (
        f"开场建立{script.scene}，{script.appearance.description}；"
        f"关键初态：{_describe_states(episode, final=False)}"
    )
    actions = script.actions
    if len(actions) == 2:
        middle = (
            f"{_actor_name(actions[0].actor_id)}{actions[0].action}，{actions[0].visible_result}"
        )
        end = (
            f"{_actor_name(actions[1].actor_id)}{actions[1].action}，"
            f"{actions[1].visible_result}；结尾{script.ending.result}；"
            f"关键终态：{_describe_states(episode, final=True)}"
        )
        return opening, middle, end
    second = f"{_actor_name(actions[0].actor_id)}{actions[0].action}，{actions[0].visible_result}"
    middle_actions = actions[1:-1]
    third = "；随后".join(
        f"{_actor_name(item.actor_id)}{item.action}，{item.visible_result}"
        for item in middle_actions
    )
    last = actions[-1]
    ending = (
        f"{_actor_name(last.actor_id)}{last.action}，{last.visible_result}；"
        f"结尾{script.ending.result}；关键终态：{_describe_states(episode, final=True)}"
    )
    return opening, second, third, ending


def _compile_video_body(
    episode: EpisodePlan,
    *,
    resolution: str,
    duration_seconds: int,
    bindings: str,
    style_profile: StyleProfile,
) -> CompiledPrompt:
    script = episode.script
    actions = "\n".join(
        f"{item.order}. {_actor_name(item.actor_id)}{item.action}，"
        f"画面结果为{item.visible_result}。"
        for item in script.actions
    )
    continuity = _describe_continuity(episode)
    text = "\n".join(
        (
            "【输出和画风】"
            f"{bindings}生成{resolution}、9:16、{duration_seconds}秒single-pass完整视频。"
            f"{style_profile.prompt_positive()}；排除{style_profile.prompt_negative()}。",
            "【人物、猫咪与场景】保持故事板中的同一个中性儿童和同一只灰白猫；"
            f"外观：{script.appearance.description}。场景：{script.scene}。",
            f"【按故事板顺序发生的动作】\n{actions}\n最后兑现：{script.ending.result}。",
            f"【关键实体连续性】{continuity}",
            "【原生声音和禁止项】自然环境声与动作声，无对白、旁白或歌词。禁止人物或"
            "猫咪分身；禁止关键道具无原因出现、消失、复制或改变form；禁止物体穿透、"
            "悬空；禁止切镜后服装突变；禁止字幕、水印、Logo和UI。",
        )
    )
    return _compiled(text)


def _describe_bindings(bindings: list[MediaBinding]) -> str:
    if not bindings:
        raise PromptCompilationError("Seedance故事板输入不能为空")
    return (
        "；".join(
            f"{item.prompt_alias}={item.semantic_key}/{item.provider_role.value}"
            for item in bindings
        )
        + "。"
    )


def _describe_states(episode: EpisodePlan, *, final: bool) -> str:
    result = validate_continuity(episode.script.continuity)
    if result.issues:
        raise PromptCompilationError("不一致连续性不能生成故事板状态")
    entities = {item.id: item for item in episode.script.continuity.entities}
    return "；".join(
        f"{entity.name}={_placement_text(entity.end_state if final else entity.start_state)}/"
        f"{entity.final_form_key if final and entity.final_form_key else entity.form_key}"
        for entity in sorted(entities.values(), key=lambda item: item.id)
    )


def _describe_continuity(episode: EpisodePlan) -> str:
    """把轻量起终态压缩为故事板和视频共用的一份连续性描述。"""

    entities = episode.script.continuity.entities
    props = [item for item in entities if item.kind is EntityKind.PROP]
    selected = props or entities
    return "；".join(
        f"{item.name}({item.form_key})从{_placement_text(item.start_state)}到"
        f"{_placement_text(item.end_state)}，生命周期{item.lifecycle.value}"
        + (f"，原因{item.change_reason}" if item.change_reason else "")
        for item in selected
    )


def _placement_text(state: EntityState) -> str:
    if not state.present:
        return "离屏"
    labels = {
        PlacementKind.ANCHOR: "位于",
        PlacementKind.HELD_BY: "由其持有",
        PlacementKind.INSIDE: "位于其内部",
        PlacementKind.OFFSCREEN: "离屏",
    }
    return f"{labels[state.placement.kind]}{state.placement.target_id or ''}"


def _actor_name(actor_id: str) -> str:
    return {"person": "人物", "cat": "灰白猫", "environment": "环境"}.get(actor_id, actor_id)


def _compiled(text: str) -> CompiledPrompt:
    stripped = text.strip()
    if not stripped:
        raise PromptCompilationError("Prompt不能为空")
    return CompiledPrompt(
        text=stripped,
        char_count=len(stripped),
        utf8_bytes=len(stripped.encode("utf-8")),
    )
