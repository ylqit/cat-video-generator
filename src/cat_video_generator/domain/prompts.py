"""全天导演、时段导演、故事板、视频与媒体审核Prompt编译。

导演上下文可以完整；Seedream负责把脚本视觉化为有序独立面板；Seedance只接收
当前Episode、已批准故事板和少量关键连续性。字符数仅用于展示，不作为本地准入门。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from .continuity import EntityKind, EntityState, PlacementKind, validate_continuity
from .contracts import (
    ActionStage,
    DayBrief,
    EpisodePlan,
    RecentContentSummary,
    ShotPlan,
    Slot,
    SlotBrief,
)
from .rendering import MediaBinding, VideoInputPlan
from .story_patterns import StoryPattern
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
    story_patterns: dict[Slot, StoryPattern] | None = None,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
) -> str:
    """生成只输出DayBrief的总导演Prompt。"""

    recent = "；".join(item.summary_text for item in recent_summaries[-6:]) or "无"
    seeds = "；".join(event_seeds[:3]) or "无适用事件种子，可原创"
    patterns = "；".join(
        f"{slot.value}={pattern.name}，情绪={pattern.mood}，节拍="
        f"{'→'.join(pattern.structure)}，可选反差="
        f"{'、'.join(pattern.humor_mechanisms) or '自然反应'}，方向示例="
        f"{'、'.join(pattern.example_themes)}"
        for slot, pattern in (story_patterns or {}).items()
    ) or "未分配模式，各时段可自由选择不同叙事结构"
    return "\n".join(
        (
            "你是持续角色生活流短视频的总导演。只输出一个符合JSON Schema的"
            "DayBrief，不输出Episode、分镜、解释或候选数组。",
            f"内容日期：{target_date.isoformat()}。当天输入：{planning_context}。",
            f"近期已批准内容：{recent}。适用事件种子：{seeds}。",
            f"本日创意模式分配：{patterns}。模式只约束叙事方向，必须按当天环境改编，"
            "不得照抄示例或为了匹配模式增加第二个任务。",
            "固定主体为同一个中性儿童和同一只灰白猫。只规划全天主题、天气、"
            "地点范围、真正跨时段复用的元素及早中晚边界，不替时段导演写动作。",
            f"人物长期身份：{series_profile.person_identity}；{series_profile.person_hair}；"
            f"{series_profile.person_body}。",
            f"人物性格：{series_profile.person_personality}。猫咪性格："
            f"{series_profile.cat_personality}。幽默表达：{series_profile.humor_style}。",
            "共享元素只使用逻辑entity_key，不要写数据库semantic_key，不要把固定人物、"
            "固定猫咪或普通场景重复声明为共享元素；没有跨时段关键道具就保持空数组。"
            "服装、鞋帽、背包和配饰服从场景，变化时给出天气、地点或事件原因。",
            "早中晚可以独立或局部承接，不强制准备—完成—归家。slots必须严格按"
            "morning、noon、evening排序，自然语言字段全部使用中文。每个带编号的事件"
            "种子一天最多分配给一个时段；三个时段必须采用不同的观察、探索、共同活动"
            "或环境回应方式，不能全部重复“听声—寻找—发现”的同一种关系结构。",
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
    story_pattern: StoryPattern | None = None,
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
    pattern = (
        f"本时段采用“{story_pattern.name}”创意模式，整体情绪为{story_pattern.mood}。"
        f"节拍骨架：{'→'.join(story_pattern.structure)}。可选幽默机制："
        f"{'、'.join(story_pattern.humor_mechanisms) or '自然反差'}。可借鉴但不可照抄的"
        f"方向：{'、'.join(story_pattern.example_themes)}；可能需要的道具方向："
        f"{'、'.join(story_pattern.prop_hints) or '由剧情决定'}。"
        "按DayBrief改编，不照抄示例；模式不是校验字段，不要把pattern_id写进JSON。"
        if story_pattern is not None
        else "本时段没有预分配模式，请选择与其他时段不同的自然生活叙事结构。"
    )
    return "\n".join(
        (
            f"你是{slot_brief.slot.value}时段导演。只输出符合JSON Schema的一个"
            "EpisodeScript，不输出slot、全天方案、解释或Markdown。",
            "DayBrief：" + json.dumps(day_brief.model_dump(mode="json"), ensure_ascii=False),
            "本时段边界：" + json.dumps(slot_brief.model_dump(mode="json"), ensure_ascii=False),
            f"前序真实终态摘要：{previous}。{retry}{repair}",
            pattern,
            "设计一条8至15秒生活流视频：一个主事件、2至4个连续动作阶段，默认使用"
            "2至3个镜头和一个可见收束。动作阶段服务于镜头叙事，不要求每个动作机械"
            "对应一张故事板图；结尾不得靠静止互看填时长。",
            "一个主事件可以有3至5次可见信息变化，但不得加入第二个独立任务。猫咪"
            "猫咪产生独立反应、探索或关系作用，不要求它每次都负责解决问题。实际"
            "操作的关键道具优先控制在0至2个，复杂度更高时先保证主事件可读。",
            "每个Shot必须明确景别、主体位置与画面方向、唯一一种camera_move、进入"
            "状态、主要变化和动作稳定后的切点。高风险拿取、落下或交接过程应在同一"
            "镜头内完成，不把物理变化藏在切镜中。",
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
            f"人物行为符合{series_profile.person_personality}；猫咪行为符合"
            f"{series_profile.cat_personality}；{series_profile.humor_style}。"
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
    """按镜头结构确定组图数量；一次Episode永远只产生一个组图任务。"""

    return 4 if len(episode.script.shots) == 3 else 3


def compile_look_prompt(
    episode: EpisodePlan,
    *,
    reference_roles: tuple[str, ...],
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
    retry_feedback: str | None = None,
) -> CompiledPrompt:
    """生成本时段的一张日内定妆图，不把临时服装写回长期Canon。"""

    references = "；".join(
        f"图{index}只负责{_reference_role_description(role)}"
        for index, role in enumerate(reference_roles, 1)
    )
    text = "\n".join(
        (
            "【任务】生成一张独立的9:16竖屏全身定妆图，用于同一天后续故事板保持人物外观。",
            f"【参考职责】{references}。",
            f"【人物身份】保持{series_profile.person_identity}；{series_profile.person_hair}；"
            f"{series_profile.person_body}。人物主要面貌必须与大头照是同一个人，全身比例参考全身照。",
            f"【本时段外观】{episode.script.appearance.description}。服装、鞋帽、雨具、背包和配饰完整可见，"
            "只表现本集剧情需要的装扮，不擅自增加性别化妆容或无关饰品。",
            f"【画风】{style_profile.prompt_positive()}；排除{style_profile.prompt_negative()}。",
            "【构图】自然站姿，正面或轻微四分之三侧身，头顶到脚部完整入画，背景简化且不抢主体，"
            "面部清楚、四肢自然、衣物层次和颜色稳定。",
            "【禁止】文字、编号、角色卡边框、多视图拼图、额外人物、身体重复、衣物断裂、明显3D/PBR质感。",
        )
    )
    if retry_feedback:
        text += f"\n【重试修正】保留旧图审计，本次必须修正：{retry_feedback}。"
    return _compiled(text)


def compile_look_review_prompt(
    episode: EpisodePlan,
    *,
    reference_roles: tuple[str, ...],
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> str:
    """审核单张日内定妆图的身份、画风与剧情装扮。"""

    references = "；".join(
        f"参考图{index}={_reference_role_description(role)}"
        for index, role in enumerate(reference_roles, 1)
    )
    return "\n".join(
        (
            "你是日内定妆图审核器，只返回给定JSON。",
            f"输入顺序：{references}；最后一张图片是待审核定妆图。",
            f"身份：{series_profile.person_identity}；{series_profile.person_hair}；{series_profile.person_body}。",
            f"本时段外观：{episode.script.appearance.description}。",
            f"画风：{style_profile.prompt_positive()}；不得出现{style_profile.prompt_negative()}。",
            "identityOk只检查是否明显为同一个人物及合理身体比例；styleOk检查二维绘本画风；"
            "appearanceOk检查服装、鞋帽和随身物品是否符合本时段描述且完整可见。"
            "轻微姿势、表情和构图差异写入warnings，不作为硬失败。",
        )
    )


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
        "；".join(
            f"图{index}只负责{_reference_role_description(role)}"
            for index, role in enumerate(reference_roles, 1)
        )
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
            f"【连续性】{_describe_continuity(episode)}。跨面板保持同一人物、同一灰白猫、"
            "同场景服装和关键道具类别、颜色、形状与数量；动作过程自然连接。",
            "【禁止】不得包含文字、序号、对白框、边框、九宫格、UI、Logo、水印、"
            "角色分身、明显3D/PBR质感或与面板顺序冲突的状态。",
        )
    )
    text += (
        "\n【表情与肢体】用视线、耳朵、尾巴、手势、重心和步态把好奇、犹豫、得意或意外外化；"
        "动作低缓连贯，避免只有站立互看，也避免夸张到改变角色身份。"
        "\n【画质稳定】面部和四肢结构清楚，手绘线条稳定，色彩均匀，衣物与关键道具细节跨面板保持。"
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
    reference_roles: tuple[str, ...] = (),
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> str:
    """一次审核整组面板，结果必须对全部面板原子生效。"""

    if panel_count != storyboard_panel_count(episode):
        raise ValueError("故事板审核数量与Episode镜头结构不一致")
    shots = _describe_shot_sequence(episode)
    continuity = _describe_continuity(episode)
    reference_note = (
        "；".join(
            f"参考图{index}={_reference_role_description(role)}"
            for index, role in enumerate(reference_roles, 1)
        )
        or "无额外身份参考"
    )
    return "\n".join(
        (
            f"你是故事板组图审核器。先输入{len(reference_roles)}张基准参考（{reference_note}），"
            f"随后输入{panel_count}张待审核面板并按面板顺序排列，只返回给定JSON。",
            f"身份：{series_profile.person_identity}；{series_profile.person_hair}；"
            f"{series_profile.cat_identity}。",
            f"画风：{style_profile.prompt_positive()}；不得出现{style_profile.prompt_negative()}。",
            f"镜头链：{shots}。关键连续性：{continuity}。"
            f"结尾：{episode.script.ending.result}。",
            "布尔字段按轻量关系弧判定：actionSequenceOk只检查面板是否依次表达建立场景、"
            "猫咪独立反应或探索、人物与猫咪关系回报，不要求每个转头、手势或脚步逐字复现；"
            "continuityOk只检查人物猫咪数量与身份、关键道具类别和数量、完整服装层及必要座位，"
            "不要因牵引绳松紧、左右手切换、轻微姿势或道具朝向变化判失败；endingOk只检查"
            "最后一张是否在语义上看得出一人一猫共同回应本集发现，静态图不必证明旋转、声音"
            "或运动模糊。硬失败只包括人物或猫咪身份/数量错误、明显3D画风、关系弧面板错序或"
            "缺失、关键道具复制消失或变类、实际坐下却没有座位、整层服装断裂、最终关系回报"
            "不可见。其他差异写入warnings，不应让布尔硬门失败。证据必须指出面板序号。",
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
        f"{entity_id}@{_placement_text(episode, state)}"
        for entity_id, state in sorted(result.final_states.items())
    )
    return (
        f"{episode.slot.value}结尾={episode.script.ending.result}，"
        f"外观={episode.script.appearance.description}，关键实体={states}"
    )


def _storyboard_panels(episode: EpisodePlan) -> tuple[str, ...]:
    """把ShotPlan投影成面板，不再按动作数量机械切分。"""

    script = episode.script
    shot_panels = [_describe_shot(episode, shot.order) for shot in script.shots]
    if len(script.shots) == 1:
        shot = script.shots[0]
        actions = _actions_for_shot(episode, shot.order)
        first = (
            f"开场建立{script.scene}与{script.appearance.description}；"
            f"{_shot_directing_text(shot)}；先呈现{actions[0].visible_result}。"
        )
        progress = f"同一镜头自然继续，{_action_sequence_text(actions)}"
        payoff = (
            f"同一镜头在动作稳定后落到关系回报：{script.ending.result}；"
            f"结尾可见状态为{_describe_end_states(episode)}。"
        )
        return first, progress, payoff
    if len(script.shots) == 2:
        return (
            f"开场建立{script.scene}与{script.appearance.description}；{shot_panels[0]}",
            f"从上一镜头的稳定姿态切入；{shot_panels[1]}",
            "保持镜头2的空间轴线与角色位置连续，改用能同时看清一人一猫和回报物的"
            "稳定收束构图，不复制面板2的动作构图；兑现关系回报："
            f"{script.ending.result}；结尾可见状态为{_describe_end_states(episode)}。",
        )
    return (
        f"开场建立{script.scene}与{script.appearance.description}；{shot_panels[0]}",
        f"从镜头1稳定切点进入；{shot_panels[1]}",
        f"从镜头2稳定切点进入；{shot_panels[2]}",
        "保持镜头3的空间轴线与角色位置连续，改用能同时看清关系回报的稳定收束构图，"
        "不复制面板3的动作构图；清楚兑现关系回报："
        f"{script.ending.result}；结尾可见状态为{_describe_end_states(episode)}。",
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
    shots = "\n".join(
        f"{_describe_shot(episode, shot.order)}"
        + (" 动作稳定后再切入下一镜头。" if shot.order < len(script.shots) else "")
        for shot in script.shots
    )
    continuity = _describe_continuity(episode)
    lighting = _lighting_direction(script.scene, script.style_context)
    if len(script.shots) == 1:
        action_text = _action_sequence_text(
            _actions_for_shot(episode, script.shots[0].order)
        )
        return _compiled(
            f"{bindings}生成{resolution}、9:16、{duration_seconds}秒single-pass完整视频。"
            f"保持故事板中的同一个中性儿童、同一只灰白猫与{script.appearance.description}；"
            f"场景为{script.scene}，{style_profile.prompt_positive()}，{lighting}。"
            f"单一连续镜头采用{_shot_directing_text(script.shots[0])}：{action_text}；"
            f"最后清楚兑现{script.ending.result}。关键连续性：{continuity}。"
            "动作通过视线、重心、耳朵和尾巴自然外化情绪，保持面部、四肢、手绘线条与色彩稳定。"
            "保留自然环境声和动作声，无对白、旁白或歌词；禁止分身、关键道具突变、穿透、悬空、"
            "服装断裂、字幕、水印、Logo和UI。"
        )
    text = "\n".join(
        (
            "【输出和画风】"
            f"{bindings}生成{resolution}、9:16、{duration_seconds}秒single-pass完整视频。"
            f"{style_profile.prompt_positive()}；排除{style_profile.prompt_negative()}。",
            "【人物、猫咪与场景】保持故事板中的同一个中性儿童和同一只灰白猫；"
            f"外观：{script.appearance.description}。场景：{script.scene}。",
            f"【按故事板顺序发生的动作】\n{shots}\n最后兑现关系回报：{script.ending.result}。",
            f"【关键实体连续性】{continuity}",
            "【原生声音和禁止项】自然环境声与动作声，无对白、旁白或歌词。禁止人物或"
            "猫咪分身；禁止关键道具无原因出现、消失、复制或改变类别；禁止物体穿透、"
            "悬空；禁止切镜后服装突变；禁止字幕、水印、Logo和UI。",
        )
    )
    text += (
        f"\n【光影与定调】{lighting}。"
        "\n【稳定与画质】面部和五官清楚但不强制单一眼睛画法；四肢自然，动作前后惯性连续，"
        "手绘线条不漂移，色彩均匀，无卡顿、穿模或服装断裂。"
    )
    return _compiled(text)


def _lighting_direction(scene: str, style_context: str) -> str:
    """从真实场景语义提炼光影，不按早中晚写死模板。"""

    scene_text = scene.lower()
    if any(word in scene_text for word in ("雨", "阴", "雾", "潮湿")):
        return "使用雨后或阴天的柔和散射光、低反差与湿润环境反光"
    if any(word in scene_text for word in ("夜", "灯", "傍晚", "黄昏", "夕阳")):
        return "使用环境中已有灯光或低角度暖光形成自然层次，不添加舞台式高光"
    if style_context == "indoor":
        return "使用场景中窗户或室内灯具提供的柔和方向光，阴影克制"
    return "使用符合天气与地点的自然环境光，保持二维绘本的哑光平涂和克制明暗"


def _reference_role_description(semantic_key: str) -> str:
    """把资产语义键翻译成供应商可理解的职责，不向Prompt泄露内部哈希。"""

    if semantic_key == "person:headshot":
        return "人物主要面貌与短发身份"
    if semantic_key == "person:fullbody":
        return "人物全身比例与体型"
    if semantic_key.startswith("look:"):
        return "本时段已批准的全身服饰与装扮"
    if semantic_key.startswith("cat:"):
        return "同一只灰白猫的体型与主要斑纹"
    if semantic_key == "style:line_texture":
        return "二维绘本线条、彩铅与蜡笔材质"
    if semantic_key in {"style:indoor", "style:outdoor"}:
        return "与本集场景匹配的绘本色彩与环境氛围"
    if semantic_key.startswith("element:"):
        return "本集关键道具的颜色、形状与材质"
    if semantic_key.startswith("scene:"):
        return "本集场景的空间与环境元素"
    return "本集必要视觉参考"


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


def _describe_continuity(episode: EpisodePlan) -> str:
    """用自然语言描述必要道具关系，不暴露数据库ID或生命周期术语。"""

    continuity = episode.script.continuity
    props = [item for item in continuity.entities if item.kind is EntityKind.PROP]
    if not props:
        return "同一个中性儿童和同一只灰白猫始终在场，服饰与数量保持一致"
    return "；".join(
        f"{item.name}开场{_placement_text(episode, item.start_state)}，结尾"
        f"{_placement_text(episode, item.end_state)}"
        + (f"，变化原因是{item.change_reason}" if item.change_reason else "")
        for item in props
    )


def _describe_end_states(episode: EpisodePlan) -> str:
    continuity = episode.script.continuity
    selected = [
        item
        for item in continuity.entities
        if item.id in episode.script.ending.key_entity_ids
    ]
    if not selected:
        selected = [item for item in continuity.entities if item.kind is EntityKind.PROP]
    return "；".join(
        f"{item.name}{_placement_text(episode, item.end_state)}" for item in selected
    ) or episode.script.ending.result


def _describe_shot_sequence(episode: EpisodePlan) -> str:
    return "；".join(_describe_shot(episode, shot.order) for shot in episode.script.shots)


def _describe_shot(episode: EpisodePlan, shot_order: int) -> str:
    shot = next((item for item in episode.script.shots if item.order == shot_order), None)
    if shot is None:
        raise PromptCompilationError(f"镜头{shot_order}不存在")
    actions = _actions_for_shot(episode, shot_order)
    return f"{_shot_directing_text(shot)}；{_action_sequence_text(actions)}"


def _actions_for_shot(episode: EpisodePlan, shot_order: int) -> tuple[ActionStage, ...]:
    shot = next((item for item in episode.script.shots if item.order == shot_order), None)
    if shot is None:
        raise PromptCompilationError(f"镜头{shot_order}不存在")
    actions_by_order = {item.order: item for item in episode.script.actions}
    try:
        return tuple(actions_by_order[order] for order in shot.action_orders)
    except KeyError as exc:
        raise PromptCompilationError(f"镜头{shot_order}引用不存在的动作{exc.args[0]}") from exc


def _shot_directing_text(shot: ShotPlan) -> str:
    moves = {
        "fixed": "固定机位",
        "follow": "平稳跟随",
        "push": "缓慢推近",
        "pull": "缓慢拉远",
        "pan": "单向摇摄",
        "track": "平稳横移",
    }
    return (
        f"镜头{shot.order}，景别为{shot.framing}，主体视角{shot.dominant_view.value}，"
        f"画面方向与主体位置：{shot.direction}；唯一运镜为{moves[shot.camera_move.value]}"
    )


def _action_sequence_text(actions: tuple[ActionStage, ...]) -> str:
    rendered: list[str] = []
    actor_prefixes = {
        "person": ("人物", "孩子", "儿童", "中性儿童"),
        "cat": ("灰白猫", "猫咪", "猫"),
        "environment": ("环境", "风", "雨", "光线", "水滴"),
    }
    for item in actions:
        action = item.action.strip()
        prefixes = actor_prefixes.get(item.actor_id, (item.actor_id,))
        # 导演经常已经在动作正文中写明执行主体。只在正文缺少主体时补前缀，
        # 避免“人物孩子”“灰白猫灰白猫”这类重复文本分散图像模型注意力。
        if not action.startswith(prefixes):
            action = f"{_actor_name(item.actor_id)}{action}"
        rendered.append(f"{action}，画面清楚形成{item.visible_result}")
    return "；随后".join(rendered)


def _placement_text(episode: EpisodePlan, state: EntityState) -> str:
    if not state.present:
        return "离屏"
    continuity = episode.script.continuity
    names = {item.id: item.name for item in continuity.anchors}
    names.update({item.id: item.name for item in continuity.entities})
    target = names.get(state.placement.target_id or "", state.placement.target_id or "")
    labels = {
        PlacementKind.ANCHOR: "位于",
        PlacementKind.HELD_BY: "由",
        PlacementKind.INSIDE: "位于",
        PlacementKind.OFFSCREEN: "离屏",
    }
    suffix = {
        PlacementKind.HELD_BY: "持有",
        PlacementKind.INSIDE: "内部",
    }.get(state.placement.kind, "")
    return f"{labels[state.placement.kind]}{target}{suffix}"


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
