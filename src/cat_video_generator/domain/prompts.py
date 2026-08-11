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
    DurationBand,
    EpisodePlan,
    OutlineEpisode,
    RecentContentSummary,
    RunCreativeControls,
    SceneRoute,
    ShotDirection,
    Slot,
    StoryConnection,
    StoryInputMode,
    StoryProjectInput,
)
from .rendering import (
    MediaModality,
    RenderOperation,
    RenderSection,
    VideoInputPlan,
    build_render_plan,
)
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
    text: str
    char_count: int
    utf8_bytes: int
    warnings: tuple[str, ...] = ()


def compile_day_director_prompt(
    *,
    target_date: date,
    project_input: StoryProjectInput,
    recent_summaries: tuple[RecentContentSummary, ...] = (),
    event_seeds: tuple[str, ...] = (),
    story_patterns: dict[Slot, StoryPattern] | None = None,
    creative_controls: RunCreativeControls | None = None,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> str:
    """主题扩写时生成ProjectOutlineV3；已有逐集剧本不应调用本函数。"""

    if project_input.input_mode is not StoryInputMode.THEME_EXPAND:
        raise PromptCompilationError("已有逐集剧本不需要调用全天总导演")
    controls = creative_controls or RunCreativeControls()
    recent = "；".join(item.summary_text for item in recent_summaries[-6:]) or "无"
    seeds = "；".join(event_seeds[:3]) or "无适用事件种子，可原创"
    patterns = _pattern_summary(story_patterns or {})
    return "\n".join(
        (
            "你是持续角色生活流视频的总导演。只输出一个符合JSON Schema的ProjectOutlineV3，"
            "不得输出具体动作、镜头、精确秒数、视频Prompt或候选数组。",
            f"内容日期：{target_date.isoformat()}。用户主题：{project_input.theme}。"
            f"场景路线：{project_input.scene_route.value}。",
            "用户冻结的活动焦点和时长仅作为时段容量边界，不得写入Outline："
            + json.dumps(controls.model_dump(mode="json"), ensure_ascii=False),
            f"近期已批准内容：{recent}。事件方向种子：{seeds}。模式建议：{patterns}。",
            "每个时段的主次关系必须服从用户冻结的活动焦点；猫咪通过探索、发现、追逐、"
            "等待、误触或自然反应参与事件，不代替人物完成复杂工具劳动。",
            "早间建立当天活动，中午推进变化或主要事件，傍晚回收前文并兑现关系与情绪。"
            "day_arc用一段完整文字说明全天连续生活弧。episodes必须是包含morning、noon、"
            "evening三个固定键的对象；每个时段只输出scene和direction。scene说明主要地点、"
            "时间和环境阶段，direction说明本时段作用、事件方向以及如何承接前后时段。"
            "活动焦点、时长档、精确事件和镜头均由项目配置与时段导演决定，不得写进Outline。",
            _scene_route_instruction(project_input.scene_route),
            f"固定人物：{series_profile.person_identity}；{series_profile.person_hair}；"
            f"{series_profile.person_body}。固定猫咪：{series_profile.cat_identity}。",
            f"猫咪行为边界：{series_profile.cat_motion_rules}。全日画风："
            f"{style_profile.prompt_positive()}；排除{style_profile.prompt_negative()}。",
            "handoffs只用name、fromSlot、toSlot和continuity登记真正跨时段延续的同一"
            "关键道具或结果；人物、猫咪和普通背景不得登记。不要输出额外时段或可重复时段数组，"
            "不要重复任何命名时段，也不要输出第四个时段。",
        )
    )


def compile_day_repair_prompt(
    *,
    original_prompt: str,
    rejected_candidate: dict[str, object],
    validation_error: str,
) -> str:
    """让总导演用同一创作意图重新输出完整ProjectOutlineV3。"""

    return "\n".join(
        (
            original_prompt,
            "【上次输出的契约修复】上次Provider已成功返回，但JSON未通过ProjectOutlineV3契约。",
            "上次完整候选："
            + json.dumps(rejected_candidate, ensure_ascii=False, separators=(",", ":")),
            f"精确错误：{validation_error}",
            "请重新输出一份完整ProjectOutlineV3，不要输出补丁或解释。episodes必须是"
            "morning、noon、evening三个固定键组成的对象，每个值只含scene和direction。"
            "保留原主题、场景路线和handoff，不得借修复改变剧情方向。",
        )
    )


def compile_connection_suggestion_prompt(
    *,
    project_theme: str,
    target_slot: Slot,
    previous_outcomes: tuple[str, ...],
    target_source_text: str | None,
    scene_route: SceneRoute,
) -> str:
    """为用户生成一张可编辑关联草稿；它不会直接进入后续导演。"""

    if target_slot is Slot.MORNING:
        raise PromptCompilationError("上午没有前序时段，不需要剧情关联建议")
    outcomes = "；".join(previous_outcomes) or "无可继承的前序事实"
    target = target_source_text or "用户尚未填写本时段原文，只根据项目主题给出宽松连接"
    return "\n".join(
        (
            "你是生活故事项目的剧情衔接编辑。只输出符合JSON Schema的"
            "ConnectionSuggestion，不输出Markdown或解释。",
            f"项目主题：{project_theme}。目标时段：{target_slot.value}。"
            f"场景路线：{scene_route.value}。",
            f"前序人工确认结果：{outcomes}。",
            f"目标时段原文或方向：{target}。",
            "只建议是否独立成篇、选择性关联或直接续接。brief使用一段自然语言，"
            "只保留对目标剧情有价值的道具、情绪、地点或结果，不复制完整前序剧情，"
            "不把分身、错误配饰、错误连接等偶发媒体问题写成事实。该输出只是用户可编辑草稿。",
        )
    )


def compile_episode_director_prompt(
    *,
    project_theme: str,
    scene_route: SceneRoute,
    slot: Slot,
    outline_episode: OutlineEpisode | None,
    activity_focus: ActivityFocus,
    duration_band: DurationBand,
    story_connection: StoryConnection | None = None,
    retry_reason: str | None = None,
    rejected_candidate: dict[str, object] | None = None,
    validation_errors: tuple[str, ...] = (),
    story_pattern: StoryPattern | None = None,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> str:
    """时段导演在总导演边界内生成精确事件、时长和镜头。"""

    return _episode_director_body(
        project_theme=project_theme,
        scene_route=scene_route,
        slot=slot,
        outline_episode=outline_episode,
        activity_focus=activity_focus,
        duration_band=duration_band,
        story_connection=story_connection,
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
    project_theme: str,
    scene_route: SceneRoute,
    slot: Slot,
    activity_focus: ActivityFocus,
    duration_band: DurationBand,
    story_connection: StoryConnection | None = None,
    retry_reason: str | None = None,
    rejected_candidate: dict[str, object] | None = None,
    validation_errors: tuple[str, ...] = (),
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> str:
    """把用户本集原文映射为同一EpisodeScript，不增删情节。"""

    return _episode_director_body(
        project_theme=project_theme,
        scene_route=scene_route,
        slot=slot,
        outline_episode=None,
        activity_focus=activity_focus,
        duration_band=duration_band,
        story_connection=story_connection,
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
    project_theme: str,
    scene_route: SceneRoute,
    slot: Slot,
    outline_episode: OutlineEpisode | None,
    activity_focus: ActivityFocus,
    duration_band: DurationBand,
    story_connection: StoryConnection | None,
    retry_reason: str | None,
    rejected_candidate: dict[str, object] | None,
    validation_errors: tuple[str, ...],
    story_pattern: StoryPattern | None,
    series_profile: SeriesVisualProfile,
    style_profile: StyleProfile,
    user_episode_text: str | None,
) -> str:
    connection = (
        "本时段已确认剧情关联：" + story_connection.brief
        if story_connection is not None and story_connection.use_for_director
        else "本时段不加载前序剧情关联；独立完成当前事件，不得自行继承前序媒体偶发内容"
    )
    requested = duration_band.range
    if user_episode_text is not None:
        source = "用户本集原文（核心事件、场景与结尾不得改写）：" + user_episode_text
    elif outline_episode is not None:
        source = "根据ProjectOutlineV3原创一个具体生活事件。"
    else:
        source = "根据用户项目主题生成一个具体生活事件；是否关联前序仅由关联卡决定。"
    pattern = (
        f"建议叙事模式：{story_pattern.name}；节拍：{'→'.join(story_pattern.structure)}。"
        if story_pattern is not None
        else "选择最适合本时段的叙事模式。"
    )
    if duration_band is DurationBand.SHORT:
        duration_capacity = (
            "短片容量规则：只完整表现一个需要精确接触的关键交互闭环；同一镜头不得串联"
            "多个交接、穿戴、容器收纳或带线工具操作。其余准备过程应在稳定切镜后以已经"
            "完成的可见状态呈现，不逐件演示，不得为了保留原文每个动作而挤压因果闭环。"
        )
    else:
        duration_capacity = (
            "每个镜头最多完整表现一个需要精确接触的关键交互闭环；交接、穿戴、容器收纳"
            "和带线工具操作分配到不同稳定段落，不得在同一镜头内连续堆叠。"
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
            f"你是{slot.value}时段导演。只输出一个符合JSON Schema的"
            "EpisodeScript，不输出slot、解释或Markdown。",
            source,
            f"生活故事主题：{project_theme}。场景路线：{scene_route.value}。",
            (
                "本时段Outline："
                + json.dumps(outline_episode.model_dump(mode="json"), ensure_ascii=False)
                if outline_episode is not None
                else "用户已明确选择AI根据项目主题生成本集；"
                "当前不存在总导演Outline，不得伪造全天边界或继承未确认候选。"
            ),
            f"{connection}。{pattern}",
            f"精确时长必须在{requested[0]}至{requested[1]}秒之间；根据可见动作需要选择"
            "整数秒，不用停顿填时长。8至15秒使用1至3镜头；16至30秒至少2镜头；"
            "31至45秒至少3镜头并可使用process_montage。全程只有一个主事件。",
            duration_capacity,
            f"本时段活动焦点固定为{activity_focus.value}。"
            + _episode_focus_direction(activity_focus),
            "猫咪保持四足自然行为：可追逐、嗅闻、拨弄、轻拍、蹭、跳上低矮物或叼"
            "轻小物；不得直立、双足行走、人手式抓握或操作复杂工具。猫咪靠近绳线、"
            "线轴、鱼竿等连接物或工具时，优先用耳朵、视线、尾巴和四足位移提示信息，"
            "不要设计持续抬起前躯、拨动被连接物牵制的部件或替人物排除工具故障。",
            "story_text用连贯长段文字完整描述场景、剧情起因、主导活动、另一角色的独立反应"
            "和可见回报，不拆成动作字段。shots为1至3个，每项只填写order和direction；"
            "direction必须是可以直接交给视频模型的完整镜头段落，同时写清镜头目的、景别、"
            "机位、唯一运镜、人物与猫咪站位、实际动作主体、肢体路径和速度、接触对象、"
            "可见结果及稳定切点。每个镜头最多安排一个关键接触关系变化；猫咪与人物不得"
            "同时操作同一工具或连接物。不要把这些信息再拆成其他字段。",
            "开场立即进入一个尚未完成的小事件或可感知信号，不用空镜等待进入剧情。"
            + _episode_opening_direction(activity_focus)
            + "每个镜头必须带来与上一镜不同的新动作、发现、声音或关系"
            "变化；同一动作、景别和情绪功能不得重复填充时长。结尾回应开场期待，并让猫咪"
            "活动线和人物活动线形成可见汇合。镜头只在动作落稳、接触关系清楚时切换。",
            "hard_constraints只登记会直接影响画面正确性的自然语言关系事实，并用"
            "shot_orders限定适用镜头；空shot_orders表示全片。普通走动、视线、背景和轻微"
            "表情不要登记。连接类必须写清两端归属及不得接触的角色，例如风筝线只能连接"
            "人物手中线轴与风筝，不得经过或缠绕猫咪。除非故事成败确实依赖某只手，约束"
            "只写人物持续支撑或持有，不锁死左右手；story_text、shots和hard_constraints中的"
            "持有者、接触方式必须一致。人物携带带线物体移动前，先将松线完整收纳在线轴或"
            "容器内，猫咪位于连接线的另一侧，不让松线跨过猫咪或行走路径。",
            "交接关系必须描述为连续的控制权转移：交出者先稳定支撑物品，接收者建立可靠接触后，"
            "交出者立即释放；两者在交接瞬间短暂同时接触同一物品是必要的过渡，不得写成禁止。"
            "只有交接完成后仍长期共同持有、物品复制、落地或转交给错误对象才属于失败。",
            "猫咪保持真实猫科动作，但不得把自然的坐姿、伏低，或前爪对低矮物体的拨、按、扶、"
            "轻拍写成违规；只有以后腿直立、人手式抓握或用前爪搬运物品才需要禁止。轻小物品的"
            "位移由猫咪用嘴叼取完成。",
            "appearance用一段文字只描述人物本时段稳定不变的完整定妆，不得混入猫咪、场景或道具状态。"
            "必须明确上装、下装、鞋袜，以及稳定穿戴的外套、帽子或配饰；会在镜头中被拿起、放下、"
            "打开、收纳或改变位置的背包和随身物只写进story_text、相关镜头和必要硬约束，"
            "不得写成appearance中的固定穿戴状态。"
            "描述必须足以生成一张从头顶到鞋底均完整可见的单人全身定妆图。"
            "猫咪配饰如草帽直接写进story_text、相关镜头和必要硬约束，不写入appearance。",
            "ending用一段可见结果兑现relationship_arc，不能用原地互看或完全静止填时长。"
            "sound_design明确环境底声、动作声和结尾声音回报，无对白、旁白或歌词。",
            f"人物身份：{series_profile.person_identity}；{series_profile.person_hair}；"
            f"{series_profile.person_body}。猫咪：{series_profile.cat_identity}；"
            f"{series_profile.cat_motion_rules}。",
            f"画风：{style_profile.prompt_positive()}；排除{style_profile.prompt_negative()}。",
            _scene_route_episode_instruction(scene_route, slot, user_episode_text is not None),
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
            f"{series_profile.person_body}。本集外观：{episode.script.appearance}。",
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
    references = "；".join(
        f"图{index}只负责{_reference_description(role)}"
        for index, role in enumerate(reference_roles, 1)
    )
    focus = _focus_instruction(episode.script.activity_focus)
    opening_constraints = _constraint_requirements(episode, (first_shot.order,))
    text = "\n".join(
        (
            "【任务】生成一张独立无字9:16开场视觉锚点，用于Seedance第一帧。",
            f"【参考职责】{references}。定妆图负责人物外观，猫咪图负责斑纹体型和正常尾巴，"
            "画风图只负责视觉媒介，道具图只负责对应道具。",
            f"【固定身份】{series_profile.person_identity}；{series_profile.person_hair}；"
            f"{series_profile.cat_identity}。全画面准确一人一猫。",
            f"【人物定妆】{episode.script.appearance}。场景、站位、穿戴状态和道具位置只以开场镜头为准，"
            "不得把完整剧情中的后续动作或结果提前画入第一帧。若定妆参考图中出现了会在剧情中移动的"
            "包或随身物，只继承人物身份和服装，不继承该物体位置；同一背包、容器或工具在画面中只能"
            "出现一次，严格放在开场镜头声明的位置，不得因参考图中的携带状态复制一份。",
            f"【活动焦点】{focus}。猫咪必须处于能推动下一步事件的位置，不是边缘装饰。",
            f"【开场镜头】{first_shot.direction}。只呈现该镜头动作开始时的稳定画面，"
            "不要提前完成后续动作，也不要为展示物品而重建镜头未要求的静物陈列。",
            "镜头描述包含整段动作与稳定切点，但开场锚点只截取第一个动作尚未推进的初始状态；"
            "必须忽略描述中的随后动作、可见结果和切点状态。静态开场允许猫咪坐定、伏低或停步观察，"
            "不要为了表现后续运动而把它改成正在奔跑、跳跃或已经到达终点。",
            "【感知线索】声音、影子、反光、气味和视线目标都只是环境线索，不得实体化为"
            "额外角色、道具或其复制品。",
            *(
                (f"【开场硬约束】{opening_constraints}。",)
                if opening_constraints
                else ()
            ),
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
                f"{episode.script.appearance}。",
                "待审核图只是一张单人全身定妆图，不是剧情开场图：不得要求猫咪、场景、"
                "活动焦点或剧情道具出现。人物须从头顶到鞋底完整可见，双手、下装、双腿、"
                "鞋袜和全部服装层清楚，身体比例自然，无裁断、重复或严重结构失衡。",
                "identityOk只检查人物身份；styleOk检查定稿二维画风；appearanceOk检查"
                "人物完整服装；compositionOk检查单人全身与四肢完整。constraintsOk在"
                "定妆任务中不适用，必须返回true。轻微表情差异只写warnings。",
            )
        )
    focus = _focus_instruction(episode.script.activity_focus)
    opening_constraints = _constraint_requirements(
        episode,
        (episode.script.shots[0].order,),
    )
    return "\n".join(
        (
            *common,
            f"人物身份：{series_profile.person_identity}；{series_profile.person_hair}。"
            f"猫咪身份：{series_profile.cat_identity}。",
            f"人物本集定妆：{episode.script.appearance}。剧情背景：{episode.script.story_text}。"
            f"开场镜头：{episode.script.shots[0].direction}。"
            + (f"开场硬约束：{opening_constraints}。" if opening_constraints else ""),
            "开场镜头文字包含完整动作过程和稳定切点，仅用于理解后续方向。审核对象是第一动作尚未"
            "推进时的静态初始状态，不得要求图片同时呈现随后动作、动作结果或切点状态；猫咪坐定、"
            "伏低或停步观察都可以是合法动作起点。",
            f"待审核图必须准确一人一猫，活动焦点可读：{focus}。开场画面允许第一动作"
            "刚刚开始；cat_lead只要求猫咪清楚可见、处于即将推动事件的位置，不要求"
            "静态第一帧已经完成猫咪主动作。人物可以在前景持工具或尺寸较大，这只是"
            "必要副活动，不能据此判成person_lead；猫咪正在追逐、观察、探索或触发"
            "下一步信息时即满足cat_lead。",
            "identityOk检查同一人物与同一猫咪；styleOk检查定稿二维画风；"
            "appearanceOk检查可见服装层和猫咪Canon；坐姿、蹲姿或家具遮挡导致鞋脚局部"
            "不可见是合法构图，不得按定妆图标准要求全身展示。compositionOk检查开场"
            "构图与后续活动起点；constraintsOk只检查硬约束中视觉显著对象的类别、"
            "数量和关系。胶带小段等微小耗材允许被手、物体或视角自然遮挡；不得把"
            "第一动作已经出现少量进展误判为道具错误。若声明了连接、承重、包含、接触、"
            "交接或穿戴关系，必须检查关系两端是否"
            "归属于正确对象；连接线不得转移、穿过或缠绕未声明的角色。"
            "猫咪自然坐着或伏低时，前爪搭在低矮容器边缘、轻按、拨动或扶住物体仍属于正常"
            "四足猫科行为，只能记为warning；只有以后腿直立、用前爪形成人手式抓握或搬运"
            "物品时，才可令constraintsOk=false。"
            "没有写入开场硬约束的普通花草、光影、漂浮绒毛等环境线索，其精确数量、姿态或形状差异"
            "只写warnings，不得据此令compositionOk或constraintsOk失败。轻微表情或合理构图差异也只写warnings。",
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
    if len(input_plan.bindings) > 1:
        binding_text += "；" + "；".join(
            f"{item.prompt_alias}仅作为{item.semantic_key.removeprefix('previous:')}参考，"
            "不得覆盖开场画面的主体身份、服装和空间关系"
            for item in input_plan.bindings[1:]
        )
    shot_lines = [_video_shot_line(episode, shot) for shot in shots]
    final_section = section.order == len(build_render_plan(episode).sections)
    constraints = _constraint_requirements(
        episode,
        tuple(shot.order for shot in shots),
    ) or "本区段没有额外硬约束"
    ending = (
        f"最终可见回报：{episode.script.ending}"
        if final_section
        else "区段末尾停在动作稳定完成后的自然切点，保留下一段继续发展的方向"
    )
    section_boundary = (
        "本区段完成上述关系汇合与关键道具结果。"
        if final_section
        else "本区段只推进当前镜头，暂不进入最终结果，不提前完成后续镜头或最终道具状态。"
    )
    progression_instruction = (
        "严格按镜头顺序推进，前序动作不得占满全片；必须完整进入最后一个镜头并"
        "兑现结尾，最后一段持续展示已经完成的可见回报，不保留未完成的关键道具状态。"
        if final_section
        else "严格只执行本区段列出的镜头；在当前镜头的稳定切点结束，保留下一延展区段"
        "所需的人物、猫咪、服装、空间轴线和关键道具状态，不提前演出后续镜头或最终回报。"
    )
    text = "\n".join(
        (
            "【整体设定与素材绑定】"
            f"{binding_text}。输出{input_plan.resolution}、9:16竖屏、"
            f"本区段{input_plan.duration_seconds}秒，原生音频。全程只有同一个中性儿童"
            "和同一只灰白猫。"
            f"人物保持{series_profile.person_identity}、{series_profile.person_hair}；"
            f"猫保持{series_profile.cat_identity}。外观：{episode.script.appearance}。"
            f"视觉环境：{episode.script.visual_context}。画风：{style_profile.prompt_positive()}；排除"
            f"{style_profile.prompt_negative()}。活动关系：{_focus_instruction(episode.script.activity_focus)}。",
            "【镜头与动作推进】\n"
            f"{progression_instruction}\n"
            + "\n".join(shot_lines),
            "【连续性、声音与结尾回报】"
            f"本区段硬约束：{constraints}。{section_boundary}"
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
    shots: list[ShotDirection],
) -> CompiledPrompt:
    """为官方视频续写编译只关注“承接与兑现”的紧凑执行Prompt。"""

    binding = input_plan.bindings[0].prompt_alias
    final_section = section.order == len(build_render_plan(episode).sections)
    final_requirement = (
        f"最终可见回报必须清楚兑现：{episode.script.ending}。"
        "后半段持续展示已解决的结果，"
        "最后不得退回未解决状态。"
        if final_section
        else "区段末尾停在动作已完成的稳定状态，为下一段保留明确发展方向。"
    )
    constraints = _constraint_requirements(
        episode,
        tuple(shot.order for shot in shots),
    ) or "本区段没有额外硬约束"
    return _compiled(
        "\n".join(
            (
                "【续写起点与固定要素】"
                f"向后延长 {binding}。第一帧直接继承上一版视频末帧的人物、猫咪、服装、"
                "关键道具和空间状态；上一段内容已经发生，不重新建立场景、不回放触发过程。"
                f"输出{input_plan.resolution}、9:16竖屏、本区段{input_plan.duration_seconds}秒，"
                "原生音频。始终只有同一个中性短发儿童和同一只灰白猫，身份、画风和关键"
                "道具外观沿用输入视频。",
                "【本区段唯一推进】\n"
                + "\n".join(_video_shot_line(episode, shot) for shot in shots)
                + "\n只执行上述当前区段镜头。前序剧情已经由输入视频承载，不在本Prompt中"
                "复述，也不得重新演出前序触发、意外或人物和猫咪已经完成的动作。",
                "【连续性、声音与结束】"
                f"本区段硬约束：{constraints}。关系目标：沿用输入视频末帧的既有关系，"
                "只完成当前镜头并兑现本段结果。"
                f"{final_requirement}"
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
    references: tuple[tuple[str, MediaModality], ...] = (),
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
) -> CompiledPrompt:
    """不依赖数据库资产的实际执行Prompt预览。"""

    from uuid import UUID

    from .rendering import MediaBinding, ProviderMediaRole, VideoInputPlan

    render_plan = build_render_plan(episode)
    try:
        section = next(item for item in render_plan.sections if item.order == section_order)
    except StopIteration as exc:
        raise PromptCompilationError(f"不存在渲染区段{section_order}") from exc
    operation = RenderOperation.INITIAL if section_order == 1 else RenderOperation.EXTEND
    bindings = [
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
    ]
    if section_order != 1 and references:
        raise PromptCompilationError("只有初始视频Prompt允许附加前序参考素材")
    modality_counts = {MediaModality.IMAGE: 1, MediaModality.VIDEO: 0}
    for index, (semantic_key, modality) in enumerate(references, start=1):
        modality_counts[modality] += 1
        bindings.append(
            MediaBinding(
                asset_id=UUID(int=section_order * 10 + index),
                semantic_key=semantic_key,
                modality=modality,
                provider_role=(
                    ProviderMediaRole.REFERENCE_IMAGE
                    if modality is MediaModality.IMAGE
                    else ProviderMediaRole.REFERENCE_VIDEO
                ),
                ordinal=modality_counts[modality],
                sha256=f"{index:x}" * 64,
            )
        )
    plan = VideoInputPlan(
        operation=operation,
        resolution=resolution,
        duration_seconds=section.duration_seconds,
        bindings=bindings,
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
    constraints = _constraint_requirements(episode)
    sample_times = "、".join(
        f"{index * episode.duration_seconds / 11:.2f}"
        for index in range(12)
    )
    return "\n".join(
        (
            "你是视频语义诊断器，只返回给定JSON；诊断不自动批准或重新生成。",
            f"人物：{series_profile.person_identity}；{series_profile.person_hair}。猫咪："
            f"{series_profile.cat_identity}。画风：{style_profile.prompt_positive()}。",
            f"活动焦点：{episode.script.activity_focus.value}。关系弧："
            f"{episode.script.relationship_arc}。完整剧情：{episode.script.story_text}。",
            *((f"硬约束：{constraints}。",) if constraints else ()),
            "按抽帧顺序检查身份、二维画风、猫咪四足动作、关键道具连续性、镜头顺序"
            "和结尾回报。每条evidence必须分别填写timestamp、object、observation和"
            "relationError；没有关系错误时relationError为null。"
            "四足自然动作不等于每一帧四只脚都必须着地：猫咪以后腿稳定支撑、双前爪"
            "短暂搭在人物膝部或低矮物体上属于真实猫科搭靠，只能记为warning；只有无支撑"
            "直立、双足行走、前爪形成人手式抓握或操作工具时才可判为动作硬失败。"
            "检查交接时必须区分过渡与终态：交出者保持支撑、接收者建立接触、随后交出者释放的"
            "短暂双向接触属于正确交接，只能按动作连续性记录；只有交接完成后仍长期共同持有、"
            "物品复制、落地或归属错误时，才可判为constraintsOk=false。"
            "连接线、承重物、容器、交接物或穿戴物归属错误必须判为constraintsOk=false。"
            "轻微表情和合理切镜变化不得误判为硬失败。",
            f"本次固定按12张均匀抽帧审核，对应估算秒点依次为：{sample_times}。"
            "timestamp必须使用最接近的上述秒点，不得虚构更精确时间。",
            "shotBoundariesSeconds只填写抽帧中能够确认的稳定切镜时刻（秒），按升序输出；"
            "不要填写0或片尾；连续单镜头或无法确认时返回空数组，不得按总时长机械均分。",
            "actualOutcome只总结抽帧中确实可见的最终事实，不用原剧本补写未实现内容。"
            "carryForward只列出后续剧情可以安全继承的实际人物、关系、道具或环境结果；"
            "doNotCarryForward列出分身、错误配饰、错误道具连接、空间突变等偶发生成错误，"
            "后续导演必须明确忽略这些错误。三个字段都使用简短自然语言。",
        )
    )


def compile_video_range_edit_prompt(
    episode: EpisodePlan,
    *,
    instruction: str,
    duration_seconds: int,
    source_start_ms: int,
    source_end_ms: int,
    relevant_constraints: tuple[str, ...] = (),
) -> CompiledPrompt:
    """编译单点区间修复Prompt；不复述整集剧情。"""

    edit = instruction.strip()
    if len(edit) < 4:
        raise PromptCompilationError("区间编辑指令必须说明需要修复的具体问题")
    constraints = "；".join(item.strip() for item in relevant_constraints if item.strip())
    return _compiled(
        "\n".join(
            (
                "【编辑对象与边界】严格编辑@视频1中"
                f"约{source_start_ms / 1000:.2f}秒至{source_end_ms / 1000:.2f}秒的唯一选定区间。"
                "@图片1是编辑前边界帧，"
                "@图片2是编辑后边界帧；生成内容必须从@图片1自然进入并在@图片2状态前稳定结束。"
                f"输出9:16竖屏、约{duration_seconds}秒，沿用源视频的二维画风和原有运动方向。",
                f"【唯一修改】{edit}。只修复这一项，不重演区间外剧情，不新增角色、道具或任务。",
                "【保持不变】同一个中性儿童、同一只灰白猫、服装、机位、空间轴线、光线、"
                "动作方向和区间外对象保持不变。"
                + (f"相关硬约束：{constraints}。" if constraints else "")
                + f"本集关系目标仅作为语义边界：{episode.script.relationship_arc}。"
                "原视频音轨由本地无损继承，本次生成声音不进入最终替换成片。禁止字幕、水印、"
                "Logo、角色复制、镜头重置和无原因换装。",
            )
        )
    )


def summarize_episode_state(episode: EpisodePlan) -> str:
    return (
        f"{episode.slot.value}已完成：{episode.script.story_text}；活动焦点"
        f"{episode.script.activity_focus.value}；关系弧{episode.script.relationship_arc}；"
        f"结尾{episode.script.ending}"
    )


def _video_shot_line(episode: EpisodePlan, shot: ShotDirection) -> str:
    return f"镜头{shot.order}：{shot.direction}"


def _constraint_requirements(
    episode: EpisodePlan,
    shot_orders: tuple[int, ...] | None = None,
) -> str | None:
    """按镜头投影硬约束；空shot_orders表示全片适用。"""

    selected = tuple(
        item
        for item in episode.script.hard_constraints
        if shot_orders is None
        or not item.shot_orders
        or set(item.shot_orders).intersection(shot_orders)
    )
    if not selected:
        return None
    return "；".join(
        (
            f"镜头{'、'.join(str(order) for order in item.shot_orders)}：{item.text}"
            if item.shot_orders
            else f"全片：{item.text}"
        )
        for item in selected
    )


def _focus_instruction(focus: ActivityFocus) -> str:
    return {
        ActivityFocus.CAT_LEAD: (
            "灰白猫推动主要可见信息，人物进行自然副活动或回应，最后两条活动线汇合"
        ),
        ActivityFocus.PERSON_LEAD: ("人物推动主要事件，灰白猫产生独立反应并在结尾回到人物关系中"),
        ActivityFocus.BALANCED: "人物与灰白猫共同推动同一事件，最后形成共同回报",
    }[focus]


def _episode_focus_direction(focus: ActivityFocus) -> str:
    return {
        ActivityFocus.CAT_LEAD: (
            "relationship_arc写清猫咪如何推动主要可见信息、人物如何承担副活动或回应，"
            "以及两条活动线如何汇合；猫咪不能只是画面边缘装饰。"
        ),
        ActivityFocus.PERSON_LEAD: (
            "relationship_arc写清人物如何推动主事件、猫咪如何产生独立且自然的反应，"
            "以及猫咪反应怎样在结尾回到人物关系中；不得把主导任务改写给猫咪。"
        ),
        ActivityFocus.BALANCED: (
            "relationship_arc写清人物和猫咪如何共同推动同一个目标并在结尾汇合；"
            "双方可以分别贡献新信息，但不得形成两个互不相干的任务。"
        ),
    }[focus]


def _episode_opening_direction(focus: ActivityFocus) -> str:
    return {
        ActivityFocus.CAT_LEAD: (
            "先由猫咪的观察、追逐、等待、误触或声音反应触发第一条可见信息，人物随后回应。"
        ),
        ActivityFocus.PERSON_LEAD: (
            "先让人物正在进行的未完成活动产生第一条可见变化，猫咪随后给出独立自然反应。"
        ),
        ActivityFocus.BALANCED: (
            "开场直接建立双方共同面对的同一目标，让人物和猫咪以不同动作共同推进第一条信息。"
        ),
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
    return role


def _pattern_summary(patterns: dict[Slot, StoryPattern]) -> str:
    if not patterns:
        return "由时段导演选择不同结构"
    return "；".join(
        f"{slot.value}={pattern.name}（{'→'.join(pattern.structure)}）"
        for slot, pattern in patterns.items()
    )


def _scene_route_instruction(scene_route: SceneRoute) -> str:
    return {
        SceneRoute.PROGRESSIVE_LOCATIONS: (
            "本项目明确采用关联多场景：上午建立准备或出发场景，中午进入主要活动场景，"
            "傍晚进入收束、归途或归家场景。三个scene必须清楚不同，但通过同一关键道具、"
            "前序结果、动作方向、光线或声音自然承接。"
        ),
        SceneRoute.SINGLE_LOCATION: (
            "本项目采用单地点递进：三个时段保持同一主要地点，但时间、活动阶段和关系回报"
            "必须持续推进，不得重复同一事件。"
        ),
        SceneRoute.ADAPTIVE: (
            "根据主题选择场景路线：钓鱼、放风筝、旅行等出行活动优先采用准备地点→主要活动"
            "地点→收束或归家地点；采茶、居家等原地生活主题可以采用同地点阶段递进。"
        ),
    }[scene_route]


def _scene_route_episode_instruction(
    scene_route: SceneRoute,
    slot: Slot,
    uses_user_script: bool,
) -> str:
    if uses_user_script:
        return (
            "用户原文已经决定本时段场景；只做镜头化适配，不得把它改回前一时段地点，"
            "也不得为了统一画面而删掉原文的出发、换场、归途或归家关系。"
        )
    if scene_route is SceneRoute.PROGRESSIVE_LOCATIONS:
        role = {
            Slot.MORNING: "准备、建立目标或出发",
            Slot.NOON: "进入主要活动地点并完成当天核心变化",
            Slot.EVENING: "进入归途、归家或情绪收束地点并回收前文",
        }[slot]
        return f"关联多场景中本时段职责是：{role}；不得无理由复用相邻时段的主要场景。"
    if scene_route is SceneRoute.SINGLE_LOCATION:
        return "本时段保持同一主要地点，但必须通过时间、活动阶段和关系变化推进新内容。"
    return "按主题和本时段职责选择最自然的场景；出行主题不得把准备、主活动和归家全部压在同一地点。"


def _compiled(text: str) -> CompiledPrompt:
    normalized = text.strip()
    if not normalized:
        raise PromptCompilationError("Prompt不能为空")
    return CompiledPrompt(
        text=normalized,
        char_count=len(normalized),
        utf8_bytes=len(normalized.encode("utf-8")),
    )
