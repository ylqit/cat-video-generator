"""导演、视觉锚点、视频生成与媒体审核Prompt编译。

导演Prompt拥有完整创作上下文；Seedream只接收定妆或开场画面所需信息；
Seedance只接收当前渲染区段的镜头执行信息。三者不得互相承载数据库状态。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from .contracts import (
    ActivityFocus,
    DayBrief,
    EpisodePlan,
    RecentContentSummary,
    RunCreativeControls,
    ShotPlan,
    Slot,
    SlotBrief,
)
from .rendering import RenderOperation, RenderSection, VideoInputPlan, build_render_plan
from .story_patterns import StoryPattern
from .user_story import UserStory
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
    creative_controls: RunCreativeControls | None = None,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> str:
    """总导演只输出全天关系、时段方向和自适应配置解析。"""

    controls = creative_controls or RunCreativeControls()
    recent = "；".join(item.summary_text for item in recent_summaries[-6:]) or "无"
    seeds = "；".join(event_seeds[:3]) or "无适用事件种子，可原创"
    patterns = _pattern_summary(story_patterns or {})
    return "\n".join(
        (
            "你是持续角色生活流视频的总导演。只输出一个符合JSON Schema的DayBrief，"
            "不得输出具体动作、镜头、精确秒数、视频Prompt或候选数组。",
            f"内容日期：{target_date.isoformat()}。用户主题输入：{planning_context}。",
            "用户已冻结的活动焦点与时长意图："
            + json.dumps(controls.model_dump(mode="json"), ensure_ascii=False),
            f"近期已批准内容：{recent}。事件方向种子：{seeds}。模式建议：{patterns}。",
            "全天默认关系是猫咪主活动、人物副活动或回应、两线汇合形成回报。"
            "猫咪通过探索、发现、追逐、等待、误触或自然反应推动观众注意力，"
            "不代替人物完成复杂工具劳动。",
            "每个slotBrief必须原样保留用户固定选择。activity_focus为adaptive时才可"
            "在cat_lead、person_lead、balanced中解析；duration_mode为adaptive时才可"
            "在short、medium、long中解析，并在resolution_reason说明全天节奏理由。",
            "short承载8至15秒轻量事件；medium承载16至30秒的变化、受阻或协作恢复；"
            "long承载31至45秒连续过程。长时段仍只有一个主事件，不得用第二个任务填时长。",
            "早间建立当天活动，中午推进变化或主要事件，傍晚回收前文并兑现关系与情绪。"
            "总导演只给eventDirection、relationshipDirection和narrativeRole，"
            "具体事件、精确秒数与镜头由时段导演决定。",
            f"固定人物：{series_profile.person_identity}；{series_profile.person_hair}；"
            f"{series_profile.person_body}。固定猫咪：{series_profile.cat_identity}。",
            f"猫咪行为边界：{series_profile.cat_motion_rules}。全日画风："
            f"{style_profile.prompt_positive()}；排除{style_profile.prompt_negative()}。",
            "handoffs只登记真正跨时段延续的同一关键道具或结果；人物、猫咪和普通背景"
            "不得登记为handoff。slotBriefs严格按morning、noon、evening排序。",
        )
    )


def compile_day_structuring_prompt(
    *,
    user_story: UserStory,
    target_date: date,
    creative_controls: RunCreativeControls | None = None,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> str:
    """把用户完整三集剧情映射为DayBrief，不改写情节。"""

    controls = creative_controls or RunCreativeControls()
    episodes = "\n".join(
        f"剧本{index}（{slot.value}）：{text}"
        for index, (slot, text) in enumerate(zip(Slot, user_story.episodes, strict=True), 1)
    )
    return "\n".join(
        (
            "你是全天结构导演。只输出符合JSON Schema的DayBrief，不增删用户剧情。",
            f"日期：{target_date.isoformat()}。主题：{user_story.theme}。\n{episodes}",
            "创作控制：" + json.dumps(controls.model_dump(mode="json"), ensure_ascii=False),
            "提炼全天目标、上下文、共享意象、三个时段作用、关系方向和跨时段handoff。"
            "固定选择不得改写；adaptive活动焦点与时长档按原剧情容量解析。",
            "默认以猫咪推动可见信息、人物承担工具活动或回应、最终关系汇合的方式整理，"
            "但不得改变用户原文的实际主次。",
            f"固定主体：{series_profile.person_identity}；{series_profile.cat_identity}。",
            f"唯一画风：{style_profile.prompt_positive()}；排除{style_profile.prompt_negative()}。",
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
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> str:
    """时段导演在总导演边界内生成精确事件、时长和镜头。"""

    return _episode_director_body(
        day_brief=day_brief,
        slot_brief=slot_brief,
        previous_state_summaries=previous_state_summaries,
        retry_reason=retry_reason,
        rejected_candidate=rejected_candidate,
        validation_errors=validation_errors,
        story_pattern=story_pattern,
        series_profile=series_profile,
        style_profile=style_profile,
        user_episode_text=None,
    )


def compile_episode_adaptation_prompt(
    *,
    user_episode_text: str,
    day_brief: DayBrief,
    slot_brief: SlotBrief,
    previous_state_summaries: tuple[str, ...],
    retry_reason: str | None = None,
    rejected_candidate: dict[str, object] | None = None,
    validation_errors: tuple[str, ...] = (),
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> str:
    """把用户本集原文映射为同一EpisodeScript，不增删情节。"""

    return _episode_director_body(
        day_brief=day_brief,
        slot_brief=slot_brief,
        previous_state_summaries=previous_state_summaries,
        retry_reason=retry_reason,
        rejected_candidate=rejected_candidate,
        validation_errors=validation_errors,
        story_pattern=None,
        series_profile=series_profile,
        style_profile=style_profile,
        user_episode_text=user_episode_text,
    )


def _episode_director_body(
    *,
    day_brief: DayBrief,
    slot_brief: SlotBrief,
    previous_state_summaries: tuple[str, ...],
    retry_reason: str | None,
    rejected_candidate: dict[str, object] | None,
    validation_errors: tuple[str, ...],
    story_pattern: StoryPattern | None,
    series_profile: SeriesVisualProfile,
    style_profile: StyleProfile,
    user_episode_text: str | None,
) -> str:
    previous = "；".join(previous_state_summaries) or "当天第一条，无前序结果"
    requested = slot_brief.duration_intent.resolved_band.range
    source = (
        "用户本集原文（不得增删情节）：" + user_episode_text
        if user_episode_text is not None
        else "根据DayBrief原创一个具体生活事件。"
    )
    pattern = (
        f"建议叙事模式：{story_pattern.name}；节拍：{'→'.join(story_pattern.structure)}。"
        if story_pattern is not None
        else "选择最适合本时段的叙事模式。"
    )
    repair = ""
    if rejected_candidate is not None:
        repair = (
            "上次结构不合格，重新输出完整对象，不做局部补丁。错误："
            f"{'；'.join(validation_errors)}。上次对象："
            f"{json.dumps(rejected_candidate, ensure_ascii=False, separators=(',', ':'))}"
        )
    return "\n".join(
        (
            f"你是{slot_brief.slot.value}时段导演。只输出一个符合JSON Schema的"
            "EpisodeScript，不输出slot、解释或Markdown。",
            source,
            "DayBrief：" + json.dumps(day_brief.model_dump(mode="json"), ensure_ascii=False),
            "本时段边界：" + json.dumps(slot_brief.model_dump(mode="json"), ensure_ascii=False),
            f"前序时段结果：{previous}。{pattern}",
            f"精确时长必须在{requested[0]}至{requested[1]}秒之间；根据可见动作需要选择"
            "整数秒，不用停顿填时长。8至15秒使用1至3镜头；16至30秒至少2镜头；"
            "31至45秒至少3镜头并可使用process_montage。全程只有一个主事件。",
            f"本时段活动焦点固定为{slot_brief.resolved_activity_focus.value}。"
            "relationshipArc分别写主活动、副活动和两线如何汇合。cat_lead时猫咪必须"
            "通过探索、发现、追逐、等待、误触或自然反应推动新信息，人物只承担工具"
            "操作、辅助或回应；不能把猫写成画面边缘装饰。",
            "猫咪保持四足自然行为：可追逐、嗅闻、拨弄、轻拍、蹭、跳上低矮物或叼"
            "轻小物；不得直立、双足行走、人手式抓握或操作复杂工具。",
            "actions为2至4个连续阶段。actor_id只允许person、cat、environment，"
            "若存在guest则还允许本集guest.id；中性儿童必须写person，灰白猫必须写cat。"
            "每个阶段写具体肢体路径、自然速度、"
            "接触对象与可见结果。shots为1至3个，每镜只使用一种运镜，并明确景别、"
            "人物与猫咪位置关系、主要变化和稳定切点。",
            "criticalProps只登记被拿取、包含、消耗或跨镜头/跨时段延续的关键道具，"
            "使用自然语言start和end；不登记人物、猫咪、普通植物、远山或光影。",
            "appearance只描述人物本时段的完整定妆，不得混入猫咪、场景或道具状态。"
            "必须明确上装、下装、鞋袜，以及外套、帽子、包或配饰是穿戴还是明确没有；"
            "描述必须足以生成一张从头顶到鞋底均完整可见的单人全身定妆图。"
            "猫咪配饰如草帽应写入mainEvent、actions和必要的criticalProps，不写入appearance。",
            "结尾必须兑现relationshipArc.convergence，不能用原地互看或完全静止填时长。"
            "soundDesign明确环境底声、动作声和结尾声音回报，无对白、旁白或歌词。",
            f"人物身份：{series_profile.person_identity}；{series_profile.person_hair}；"
            f"{series_profile.person_body}。猫咪：{series_profile.cat_identity}；"
            f"{series_profile.cat_motion_rules}。",
            f"画风：{style_profile.prompt_positive()}；排除{style_profile.prompt_negative()}。",
            (f"人工重规划原因：{retry_reason}。" if retry_reason else "") + repair,
        )
    )


def compile_look_prompt(
    episode: EpisodePlan,
    *,
    reference_roles: tuple[str, ...],
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
    retry_feedback: str | None = None,
) -> CompiledPrompt:
    references = "；".join(
        f"图{index}只负责{_reference_description(role)}"
        for index, role in enumerate(reference_roles, 1)
    )
    text = "\n".join(
        (
            "【任务】生成一张独立无字的9:16全身定妆图，作为本集人物外观唯一基准。",
            f"【参考职责】{references}。参考画风不得改写人物身份。",
            f"【人物】{series_profile.person_identity}；{series_profile.person_hair}；"
            f"{series_profile.person_body}。本集外观：{episode.script.appearance.description}。",
            f"【画风】{style_profile.prompt_positive()}；排除{style_profile.prompt_negative()}。",
            "【构图】人物自然站姿并位于画面中央，占画面高度约75%，头顶上方和鞋底下方"
            "都保留明确留白；必须完整显示头顶、双手、下装、双腿、鞋袜和脚底落地阴影。"
            "面貌、短发、身体比例与全部服装层清楚，简洁纯净背景，不包含猫咪、场景道具"
            "或其他人物。",
            "【禁止】文字、角色卡网格、多视图、身体重复、服装断裂、明显3D/PBR质感。",
        )
    )
    if retry_feedback:
        text += f"\n【重试修正】{retry_feedback}。"
    return _compiled(text)


def compile_opening_anchor_prompt(
    episode: EpisodePlan,
    *,
    reference_roles: tuple[str, ...],
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
    retry_feedback: str | None = None,
) -> CompiledPrompt:
    first_shot = episode.script.shots[0]
    first_actions = _actions_for_shot(episode, first_shot)
    references = "；".join(
        f"图{index}只负责{_reference_description(role)}"
        for index, role in enumerate(reference_roles, 1)
    )
    focus = _focus_instruction(episode.script.activity_focus)
    text = "\n".join(
        (
            "【任务】生成一张独立无字9:16开场视觉锚点，用于Seedance第一帧。",
            f"【参考职责】{references}。定妆图负责人物外观，猫咪图负责斑纹体型和正常尾巴，"
            "画风图只负责视觉媒介，道具图只负责对应道具。",
            f"【固定身份】{series_profile.person_identity}；{series_profile.person_hair}；"
            f"{series_profile.cat_identity}。全画面准确一人一猫。",
            f"【场景与外观】{episode.script.scene}；{episode.script.appearance.description}。",
            f"【活动焦点】{focus}。猫咪必须处于能推动下一步事件的位置，不是边缘装饰。",
            f"【开场镜头】{first_shot.framing}，{first_shot.direction}；机位体现"
            f"{first_shot.camera_move.value}运镜开始前的稳定姿态。可见动作起点："
            f"{'；'.join(item.action for item in first_actions)}。",
            f"【开场关键道具】{_opening_prop_requirements(episode)}。第一动作可以已经开始，"
            "不要为了还原文字起点而重建与镜头方向冲突的静物陈列。",
            f"【画风】{style_profile.prompt_positive()}；排除{style_profile.prompt_negative()}。",
            "【禁止】文字、边框、UI、角色分身、额外动物、尾巴脱离身体、服装断裂、"
            "不可能肢体、明显3D/PBR质感。",
        )
    )
    if retry_feedback:
        text += f"\n【重试修正】{retry_feedback}。"
    return _compiled(text)


def compile_image_review_prompt(
    episode: EpisodePlan,
    *,
    target: str,
    reference_roles: tuple[str, ...],
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> str:
    if target not in {"look", "opening_anchor"}:
        raise PromptCompilationError(f"未知图片审核目标：{target}")
    common = (
        "你是生产图片审核器，只返回给定JSON。最后一张是待审核图，其余是参考图。",
        "参考职责：" + "；".join(reference_roles),
        f"画风：{style_profile.prompt_positive()}；不得出现{style_profile.prompt_negative()}。",
    )
    if target == "look":
        return "\n".join(
            (
                *common,
                f"人物身份：{series_profile.person_identity}；{series_profile.person_hair}；"
                f"{series_profile.person_body}。人物本集完整定妆："
                f"{episode.script.appearance.description}。",
                "待审核图只是一张单人全身定妆图，不是剧情开场图：不得要求猫咪、场景、"
                "活动焦点或剧情道具出现。人物须从头顶到鞋底完整可见，双手、下装、双腿、"
                "鞋袜和全部服装层清楚，身体比例自然，无裁断、重复或严重结构失衡。",
                "identityOk只检查人物身份；styleOk检查定稿二维画风；appearanceOk检查"
                "人物完整服装；compositionOk检查单人全身与四肢完整。criticalPropsOk在"
                "定妆任务中不适用，必须返回true。轻微表情差异只写warnings。",
            )
        )
    focus = _focus_instruction(episode.script.activity_focus)
    return "\n".join(
        (
            *common,
            f"人物身份：{series_profile.person_identity}；{series_profile.person_hair}。"
            f"猫咪身份：{series_profile.cat_identity}。",
            f"人物本集定妆：{episode.script.appearance.description}。场景："
            f"{episode.script.scene}。开场关键道具：{_opening_prop_requirements(episode)}。",
            f"待审核图必须准确一人一猫，活动焦点可读：{focus}。开场画面允许第一动作"
            "刚刚开始；cat_lead只要求猫咪清楚可见、处于即将推动事件的位置，不要求"
            "静态第一帧已经完成猫咪主动作。人物可以在前景持工具或尺寸较大，这只是"
            "必要副活动，不能据此判成person_lead；猫咪正在追逐、观察、探索或触发"
            "下一步信息时即满足cat_lead。",
            "identityOk检查同一人物与同一猫咪；styleOk检查定稿二维画风；"
            "appearanceOk检查可见服装层和猫咪Canon；坐姿、蹲姿或家具遮挡导致鞋脚局部"
            "不可见是合法构图，不得按定妆图标准要求全身展示。compositionOk检查开场"
            "构图与后续活动起点；criticalPropsOk只检查开场中视觉显著关键道具的类别、"
            "数量和不可替换外观。胶带小段等微小耗材允许被手、物体或视角自然遮挡；"
            "不得把第一动作已经出现少量进展误判为道具错误。"
            "轻微表情或合理构图差异只写warnings。",
        )
    )


def compile_video_prompt(
    episode: EpisodePlan,
    *,
    input_plan: VideoInputPlan,
    section: RenderSection,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> CompiledPrompt:
    """为一个初始或延展区段编译实际Seedance Prompt。"""

    shots = [shot for shot in episode.script.shots if shot.order in section.shot_orders]
    if not shots:
        raise PromptCompilationError("渲染区段没有镜头")
    # 视频延展是独立的供应商生命周期：输入已经包含前一段的身份、画风和空间。
    # 若继续重复整集设定和完整关系弧，模型容易重演触发动作而不兑现本段结果。
    if input_plan.operation is RenderOperation.EXTEND:
        return _compile_extension_video_prompt(
            episode,
            input_plan=input_plan,
            section=section,
            shots=shots,
        )
    binding = input_plan.bindings[0].prompt_alias
    binding_text = f"{binding}是严格开场画面，保持其人物、猫咪、服装和空间轴线"
    shot_lines = [_video_shot_line(episode, shot) for shot in shots]
    final_section = section.order == len(build_render_plan(episode).sections)
    continuity = _section_continuity(episode, final_section=final_section)
    ending = (
        f"最终可见回报：{episode.script.ending.result}"
        if final_section
        else "区段末尾停在动作稳定完成后的自然切点，保留下一段继续发展的方向"
    )
    section_boundary = (
        "本区段完成上述关系汇合与关键道具结果。"
        if final_section
        else "本区段只推进当前镜头，不提前完成后续镜头或最终道具状态。"
    )
    text = "\n".join(
        (
            "【整体设定与素材绑定】"
            f"{binding_text}。输出{input_plan.resolution}、9:16竖屏、"
            f"本区段{input_plan.duration_seconds}秒，原生音频。全程只有同一个中性儿童"
            "和同一只灰白猫。"
            f"人物保持{series_profile.person_identity}、{series_profile.person_hair}；"
            f"猫保持{series_profile.cat_identity}。外观：{episode.script.appearance.description}。"
            f"场景：{episode.script.scene}。画风：{style_profile.prompt_positive()}；排除"
            f"{style_profile.prompt_negative()}。活动关系：{_focus_instruction(episode.script.activity_focus)}。",
            "【镜头与动作推进】\n"
            "严格按镜头顺序推进，前序动作不得占满全片；必须完整进入最后一个镜头并"
            "兑现结尾，最后一段持续展示已经完成的可见回报，不保留未完成的关键道具状态。\n"
            + "\n".join(shot_lines),
            "【连续性、声音与结尾回报】"
            f"关系弧：主活动为{episode.script.relationship_arc.lead_activity}；副活动为"
            f"{episode.script.relationship_arc.secondary_activity}；两线汇合为"
            f"{episode.script.relationship_arc.convergence}。全片关键道具闭环：{continuity}。"
            f"{section_boundary}"
            f"声音：{episode.script.sound_design}。{ending}。"
            "人物和猫咪数量固定；禁止角色复制、瞬移、无原因换装、关键道具变类或悬空、"
            "猫咪双足直立或人手式操作、字幕、水印、Logo和供应商UI；不得用原地互看或"
            "完全静止填充时长。",
        )
    )
    return _compiled(text)


def _compile_extension_video_prompt(
    episode: EpisodePlan,
    *,
    input_plan: VideoInputPlan,
    section: RenderSection,
    shots: list[ShotPlan],
) -> CompiledPrompt:
    """为官方视频续写编译只关注“承接与兑现”的紧凑执行Prompt。"""

    binding = input_plan.bindings[0].prompt_alias
    actions = tuple(action for shot in shots for action in _actions_for_shot(episode, shot))
    visible_results = "；".join(action.visible_result for action in actions)
    final_section = section.order == len(build_render_plan(episode).sections)
    final_requirement = (
        f"最终可见回报必须清楚兑现：{episode.script.ending.result}。"
        "后半段持续展示已解决的结果，"
        "最后不得退回未解决状态。"
        if final_section
        else "区段末尾停在动作已完成的稳定状态，为下一段保留明确发展方向。"
    )
    continuity = _section_continuity(episode, final_section=final_section)
    return _compiled(
        "\n".join(
            (
                "【续写起点与固定要素】"
                f"向后延长 {binding}。第一帧直接继承上一版视频末帧的人物、猫咪、服装、"
                "关键道具和空间状态；上一段内容已经发生，不重新建立场景、不回放触发过程。"
                f"输出{input_plan.resolution}、9:16竖屏、本区段{input_plan.duration_seconds}秒，"
                "原生音频。始终只有同一个中性短发儿童和同一只灰白猫，身份、画风和关键"
                "道具外观沿用输入视频。",
                "【本区段唯一推进】"
                "本区段前半完成当前关键变化，后半明确展示结果与人猫关系汇合。"
                + "\n".join(_video_shot_line(episode, shot) for shot in shots)
                + f"必须出现的可见结果：{visible_results}。",
                "【连续性、声音与结束】"
                f"关键道具闭环：{continuity}。{final_requirement}"
                f"声音：{episode.script.sound_design}。禁止重演前文、重复触发意外、以未解决"
                "状态收尾、角色复制、瞬移、无原因换装、道具变类或悬空、猫咪人手式操作、"
                "字幕、水印、Logo和供应商UI。",
            )
        )
    )


def compile_video_prompt_preview(
    episode: EpisodePlan,
    *,
    resolution: str,
    section_order: int = 1,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
) -> CompiledPrompt:
    """不依赖数据库资产的实际执行Prompt预览。"""

    from uuid import UUID

    from .rendering import MediaBinding, MediaModality, ProviderMediaRole, VideoInputPlan

    render_plan = build_render_plan(episode)
    try:
        section = next(item for item in render_plan.sections if item.order == section_order)
    except StopIteration as exc:
        raise PromptCompilationError(f"不存在渲染区段{section_order}") from exc
    operation = RenderOperation.INITIAL if section_order == 1 else RenderOperation.EXTEND
    plan = VideoInputPlan(
        operation=operation,
        resolution=resolution,
        duration_seconds=section.duration_seconds,
        bindings=[
            MediaBinding(
                asset_id=UUID(int=section_order),
                semantic_key="opening:preview" if section_order == 1 else "video:preview",
                modality=MediaModality.IMAGE if section_order == 1 else MediaModality.VIDEO,
                provider_role=(
                    ProviderMediaRole.FIRST_FRAME
                    if section_order == 1
                    else ProviderMediaRole.REFERENCE_VIDEO
                ),
                ordinal=1,
                sha256="0" * 64,
            )
        ],
    )
    return compile_video_prompt(
        episode,
        input_plan=plan,
        section=section,
        series_profile=series_profile,
        style_profile=style_profile,
    )


def compile_video_diagnostic_prompt(
    episode: EpisodePlan,
    *,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> str:
    return "\n".join(
        (
            "你是视频语义诊断器，只返回给定JSON；诊断不自动批准或重新生成。",
            f"人物：{series_profile.person_identity}；{series_profile.person_hair}。猫咪："
            f"{series_profile.cat_identity}。画风：{style_profile.prompt_positive()}。",
            f"活动焦点：{episode.script.activity_focus.value}。主活动："
            f"{episode.script.relationship_arc.lead_activity}；副活动："
            f"{episode.script.relationship_arc.secondary_activity}；汇合："
            f"{episode.script.relationship_arc.convergence}。",
            "按抽帧顺序检查身份、二维画风、猫咪四足动作、关键道具连续性、镜头顺序"
            "和结尾回报。轻微表情和合理切镜变化不得误判为硬失败。",
        )
    )


def summarize_episode_state(episode: EpisodePlan) -> str:
    props = (
        "；".join(f"{item.name}最终为{item.end}" for item in episode.script.critical_props)
        or "无跨时段关键道具"
    )
    return (
        f"{episode.slot.value}已完成：{episode.script.main_event}；活动焦点"
        f"{episode.script.activity_focus.value}；汇合{episode.script.relationship_arc.convergence}；"
        f"结尾{episode.script.ending.result}；{props}"
    )


def _video_shot_line(episode: EpisodePlan, shot: ShotPlan) -> str:
    actors = {"person": "人物", "cat": "灰白猫", "environment": "环境"}
    if episode.script.guest is not None:
        actors[episode.script.guest.id] = episode.script.guest.name
    actions = _actions_for_shot(episode, shot)
    action_text = "；随后".join(
        f"{actors.get(item.actor_id, item.actor_id)}{item.action}，动作结果为{item.visible_result}"
        for item in actions
    )
    camera_move = {
        "fixed": "固定镜头",
        "follow": "平稳跟拍",
        "push": "缓慢推近",
        "pull": "缓慢拉远",
        "pan": "平稳摇摄",
        "track": "平稳横移",
    }[shot.camera_move.value]
    return (
        f"镜头{shot.order}：{camera_move}，{shot.framing}；{shot.direction}；"
        f"{action_text}。动作稳定后再切镜。"
    )


def _section_continuity(episode: EpisodePlan, *, final_section: bool) -> str:
    """只向当前渲染区段暴露必要的道具连续性。

    非最终延展段若收到整集终态，模型容易提前收纳、离场或完成回报。前段因此
    只锁定同一物体和当前起点，最终段才声明完整闭环。
    """

    if not episode.script.critical_props:
        return "本集没有需要额外追踪的关键道具"
    if final_section:
        return "；".join(
            f"{prop.name}：{prop.start} → {prop.end}" for prop in episode.script.critical_props
        )
    return "；".join(
        f"{prop.name}沿用同一个物体，当前从{prop.start}继续推进，暂不进入最终结果"
        for prop in episode.script.critical_props
    )


def _opening_prop_requirements(episode: EpisodePlan) -> str:
    """声明开场需要维持的道具身份，不把完整状态账本塞给图片模型。"""

    if not episode.script.critical_props:
        return "没有必须入镜的关键道具"
    first_shot = episode.script.shots[0]
    first_actions = _actions_for_shot(episode, first_shot)
    visible_text = "；".join(
        (
            first_shot.direction,
            *(item.action for item in first_actions),
            *(item.visible_result for item in first_actions),
        )
    )
    visible_props = tuple(
        prop
        for prop in episode.script.critical_props
        if prop.name in visible_text
        or prop.entity_key in visible_text
        or (len(prop.name) >= 2 and prop.name[-2:] in visible_text)
    )
    if not visible_props:
        return "没有必须在开场清楚出现的关键道具"
    return "；".join(
        f"同一个{prop.name}，开场状态为：{prop.start}" for prop in visible_props
    )


def _actions_for_shot(episode: EpisodePlan, shot: ShotPlan):
    indexed = {item.order: item for item in episode.script.actions}
    try:
        return tuple(indexed[order] for order in shot.action_orders)
    except KeyError as exc:
        raise PromptCompilationError(f"镜头{shot.order}引用未知动作") from exc


def _focus_instruction(focus: ActivityFocus) -> str:
    return {
        ActivityFocus.CAT_LEAD: (
            "灰白猫推动主要可见信息，人物进行自然副活动或回应，最后两条活动线汇合"
        ),
        ActivityFocus.PERSON_LEAD: ("人物推动主要事件，灰白猫产生独立反应并在结尾回到人物关系中"),
        ActivityFocus.BALANCED: "人物与灰白猫共同推动同一事件，最后形成共同回报",
    }[focus]


def _reference_description(role: str) -> str:
    if role.startswith("person:"):
        return "人物面貌、短发与身体比例"
    if role.startswith("cat:"):
        return "灰白猫脸型、斑纹、体型与正常尾巴比例"
    if role.startswith("style:"):
        return "定稿二维画风、线条、色彩和材质"
    if role.startswith("look:"):
        return "本集人物定妆与完整服装"
    if role.startswith("element:"):
        return "本集关键道具的颜色、形状和材质"
    if role.startswith("handoff:"):
        names = role.removeprefix("handoff:").replace(",", "、")
        return f"上一时段画面中的共享道具（{names}），只继承道具造型，不复制人物、猫咪或场景"
    return role


def _pattern_summary(patterns: dict[Slot, StoryPattern]) -> str:
    if not patterns:
        return "由时段导演选择不同结构"
    return "；".join(
        f"{slot.value}={pattern.name}（{'→'.join(pattern.structure)}）"
        for slot, pattern in patterns.items()
    )


def _compiled(text: str) -> CompiledPrompt:
    normalized = text.strip()
    if not normalized:
        raise PromptCompilationError("Prompt不能为空")
    return CompiledPrompt(
        text=normalized,
        char_count=len(normalized),
        utf8_bytes=len(normalized.encode("utf-8")),
    )
