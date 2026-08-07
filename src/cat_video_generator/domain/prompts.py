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
from .rendering import VideoInputPlan
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
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
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
            f"全日唯一视觉风格：{style_profile.prompt_positive()}；排除"
            f"{style_profile.prompt_negative()}。早中晚只允许随天气、地点和光线自然变化，"
            "不得切换成其它插画、摄影或三维渲染体系。",
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
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
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
            "产生独立反应、探索或关系作用，不要求它每次都负责解决问题。实际"
            "操作的关键道具优先控制在0至2个，复杂度更高时先保证主事件可读。",
            "每个Shot必须明确景别、主体位置与画面方向、唯一一种camera_move、进入"
            "状态、主要变化和动作稳定后的切点。高风险拿取、落下或交接过程应在同一"
            "镜头内完成，不把物理变化藏在切镜中。",
            "动作主体使用SceneContinuity实体ID（固定人物person、灰白猫cat）；"
            "ActionStage只保存order、actor_id、action和visible_result。停步、转头、"
            "蹲下、嗅闻、走动等姿态只写入动作文本。每个action必须由实际执行主体出发，"
            "说明肢体动作、自然速度、移动路径、接触对象及动作停止位置；visible_result"
            "只写镜头中能直接看到的动作后结果，不用抽象情绪代替画面。",
            "SceneContinuity只登记人物、猫咪、被操作或跨镜头延续的关键道具，以及"
            "真正参与承重的桌面、座椅等锚点。普通植物、屋檐、远山和装饰不要建账。",
            "每个实体声明kind、逻辑entity_key、start_state、end_state、lifecycle和稳定"
            "form_key。**entity_key在账本内必须唯一**：每个实体一个独立键（如fishing_rod、"
            "fish_bucket、cat_hat_big），禁止把同一类别词（如fishing_gear）复用给多个实体。"
            "form_key只表示类别或固定外观，禁止加入crouch、sniffing、walking等"
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
            f"{series_profile.cat_motion_rules}。"
            f"人物行为符合{series_profile.person_personality}；猫咪行为符合"
            f"{series_profile.cat_personality}；{series_profile.humor_style}。"
            "猫咪动作必须四足可执行：剧情需要拟人效果时做生物力学适配——抱物改为"
            "用嘴叼住或前爪在四足姿态下轻扶，递物改为用鼻子顶过去，不得写猫咪直立、"
            "双足行走或人手式抓握；这属于执行方式适配，visible_result的剧情意图保持不变。"
            "服装、鞋帽和背包按剧情自然变化。appearance直接描述本时段外观；"
            "若相对前一时段有变化，列入changes_from_previous并给出change_reason，"
            "没有变化时两者保持空值。",
            f"本集必须服从全日定稿画风：{style_profile.prompt_positive()}；排除"
            f"{style_profile.prompt_negative()}。style_context只选择indoor或outdoor场景参考，"
            "不得重新定义画风、媒介或角色造型体系。",
            "ending必须给出result和key_entity_ids；key_entity_ids可引用continuity中已登记的"
            "实体或场景锚点；只有结尾物体状态或构图必须精确锁定"
            "时，才将visual_critical设为true。脚本不选择Seedance输入模式，也不声明"
            "参考素材。",
            "sound_design必须结合本集场景和动作，明确环境底声、关键动作声和结尾声音回报；"
            "不使用对白、旁白或歌词，也不要只写笼统的“自然环境声”。",
        )
    )


def storyboard_panel_count(episode: EpisodePlan) -> int:
    """按镜头结构确定组图数量；一次Episode永远只产生一个组图任务。"""

    return 4 if len(episode.script.shots) == 3 else 3


def compile_day_structuring_prompt(
    *,
    user_story: UserStory,
    target_date: date,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> str:
    """扩写模式：把用户写好的完整三集剧情结构化为DayBrief，不做任何创作。"""

    episodes = "\n".join(
        f"剧本{index}（{slot.value}）：\n{text}"
        for index, (slot, text) in enumerate(
            zip((Slot.MORNING, Slot.NOON, Slot.EVENING), user_story.episodes),
            1,
        )
    )
    return "\n".join(
        (
            "你是持续角色生活流短视频的总导演。用户已经写好全天三集完整剧情，"
            "你的任务是把它们结构化为一个符合JSON Schema的DayBrief，"
            "**不得增删情节、道具、幽默点或角色行为**，不输出Episode、解释或Markdown。",
            f"内容日期：{target_date.isoformat()}。全天主题：{user_story.theme}。",
            f"用户剧情原文：\n{episodes}",
            "从原文提炼：全天主题与氛围、天气与光线随早中晚的自然推进、地点范围、"
            "真正跨时段复用的关键道具（shared_elements使用逻辑entity_key，"
            "如钓鱼装备、风筝；没有就保持空数组），以及每个时段的叙事目的、"
            "场景方向、事件方向与服饰意图（slots严格按morning、noon、evening排序，"
            "自然语言字段全部使用中文）。**每个shared_element的slots必须覆盖该道具在"
            "用户原文中出现的全部时段**——例如水桶在准备、垂钓、返程三集都出现，"
            "slots就必须是morning、noon、evening三个，漏掉的时段导演将无法合法使用它。",
            f"固定主体为同一个中性儿童和同一只灰白猫：{series_profile.person_identity}；"
            f"{series_profile.cat_identity}。人物性格：{series_profile.person_personality}。"
            f"猫咪性格：{series_profile.cat_personality}。",
            f"全日唯一视觉风格：{style_profile.prompt_positive()}；排除"
            f"{style_profile.prompt_negative()}。",
        )
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
    """扩写模式：把用户写好的本集剧情逐拍改编为EpisodeScript契约。

    契约规则段落与创作模式完全一致；任务从"创作"换成"改编"——情节、道具、
    幽默节拍与温情收束一律以用户原文为准，模型只负责结构化映射。
    """

    previous = "；".join(previous_state_summaries) or "当天第一条，无前序状态"
    repair = ""
    if rejected_candidate is not None:
        repair = (
            "上次候选存在确定性矛盾，必须重新输出完整EpisodeScript而不是局部补丁，"
            "剧情内容仍以用户原文为准。错误："
            f"{'；'.join(validation_errors)}。上次候选："
            f"{json.dumps(rejected_candidate, ensure_ascii=False, separators=(',', ':'))}"
        )
    retry = f"局部重规划原因：{retry_reason}。" if retry_reason else ""
    return "\n".join(
        (
            f"你是{slot_brief.slot.value}时段导演。用户已经写好本集剧情，你的任务是"
            "把它**逐拍改编**为一个符合JSON Schema的EpisodeScript，"
            "不输出slot、解释或Markdown。",
            f"用户本集剧情原文（改编的唯一依据）：\n{user_episode_text}",
            "**情节、道具、幽默节拍、角色行为与温情收束一律不得增删改动**；"
            "你只负责把原文映射为动作阶段、镜头、连续性账本与声音设计。"
            "原文没有明写的动作不要新增；原文写到的道具必须出现在连续性账本或画面中。",
            "DayBrief：" + json.dumps(day_brief.model_dump(mode="json"), ensure_ascii=False),
            "本时段边界：" + json.dumps(slot_brief.model_dump(mode="json"), ensure_ascii=False),
            f"前序真实终态摘要：{previous}。{retry}{repair}",
            "输出8至15秒生活流视频脚本：一个主事件、2至4个连续动作阶段，默认使用"
            "2至3个镜头和一个可见收束。结尾不得靠静止互看填时长。",
            "每个Shot必须明确景别、主体位置与画面方向、唯一一种camera_move、进入"
            "状态、主要变化和动作稳定后的切点。高风险拿取、落下或交接过程应在同一"
            "镜头内完成，不把物理变化藏在切镜中。",
            "动作主体使用SceneContinuity实体ID（固定人物person、灰白猫cat）；"
            "ActionStage只保存order、actor_id、action和visible_result。停步、转头、"
            "蹲下、嗅闻、走动等姿态只写入动作文本。每个action必须由实际执行主体出发，"
            "说明肢体动作、自然速度、移动路径、接触对象及动作停止位置；visible_result"
            "只写镜头中能直接看到的动作后结果，不用抽象情绪代替画面。",
            "SceneContinuity只登记人物、猫咪、被操作或跨镜头延续的关键道具，以及"
            "真正参与承重的桌面、座椅等锚点。普通植物、屋檐、远山和装饰不要建账。",
            "每个实体声明kind、逻辑entity_key、start_state、end_state、lifecycle和稳定"
            "form_key。**entity_key在账本内必须唯一**：每个实体一个独立键（如fishing_rod、"
            "fish_bucket），禁止把同一类别词复用给多个实体。form_key只表示类别或固定外观，"
            "禁止加入crouch、sniffing、walking等姿势。人物和猫咪固定persist；其他实体按"
            "persist、enter、exit、consume或transform声明起终态，变化时说明原因。",
            "首个动作里提到的人物、猫咪和关键道具起点必须逐项等于对应实体的start_state；"
            "后续动作与end_state也必须保持同一空间语义。",
            "关键实体不得无原因出现、消失、复制或改变类别；承担发现或结尾回报的实体"
            "必须登记，并在ending.key_entity_ids中引用。跨时段道具使用DayBrief中的同一"
            "entity_key。导演不得生成数据库资产semantic_key。",
            f"人物保持{series_profile.person_identity}；{series_profile.person_hair}；"
            f"{series_profile.person_body}。猫保持{series_profile.cat_identity}。"
            "appearance直接描述本时段外观；若相对前一时段有变化，列入"
            "changes_from_previous并给出change_reason，没有变化时两者保持空值。",
            f"本集必须服从全日定稿画风：{style_profile.prompt_positive()}；排除"
            f"{style_profile.prompt_negative()}。style_context只选择indoor或outdoor场景参考。",
            "ending必须给出result和key_entity_ids；只有结尾物体状态或构图必须精确锁定"
            "时，才将visual_critical设为true。脚本不选择Seedance输入模式，也不声明参考素材。",
            "sound_design必须结合本集场景和动作，明确环境底声、关键动作声和结尾声音回报；"
            "不使用对白、旁白或歌词；必须是一句话字符串，不要输出列表。",
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
            "identityOk只检查是否明显为同一个人物及合理身体比例；styleOk检查批准的日系二维治愈插画画风；"
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
            _storyboard_reference_instruction(index, role)
            for index, role in enumerate(reference_roles, 1)
        )
        or "没有额外参考图"
    )
    panels = _storyboard_panels(episode)
    look_reference_index = next(
        (
            index
            for index, role in enumerate(reference_roles, 1)
            if role.startswith("look:")
        ),
        None,
    )
    person_source = (
        f"图{look_reference_index}定妆人物"
        if look_reference_index is not None
        else "已批准人物Canon"
    )
    identity_invariant = (
        f"本面板继承{person_source}的主要面貌、固定短发、儿童头身比例和完整服装，"
        "并继承同一只灰白猫的脸型、主要斑纹、体型及正常尾巴比例"
    )
    panel_lines = "\n".join(
        f"面板{index}：{description}；{identity_invariant}；"
        f"{_storyboard_prop_invariants(episode)}。"
        for index, description in enumerate(panels, 1)
    )
    script = episode.script
    text = "\n".join(
        (
            f"【任务】一次生成{len(panels)}张相互连贯但彼此独立的9:16竖屏故事板图；"
            "每张都是完整画面，不要拼成网格。",
            f"【参考职责】{references}。",
            f"【固定主体】{series_profile.person_identity}；{series_profile.person_hair}；"
            f"{series_profile.cat_identity}。{series_profile.cat_motion_rules}。"
            "整组叙事世界始终只有这一人一猫；特写可以让"
            "非焦点主体局部可见或暂时画外，但不得生成第二个人物或第二只猫。",
            f"【画风】{style_profile.prompt_positive()}；排除{style_profile.prompt_negative()}。",
            f"【场景与服饰】{script.scene}；{script.appearance.description}。",
            f"【有序面板】\n{panel_lines}",
            f"【连续性】{_describe_character_boundaries(episode)}；{_describe_continuity(episode)}。"
            "跨面板保持同一人物、同一灰白猫、人与猫的相对体型、"
            "同场景服装和关键道具类别、颜色、形状与数量。同一镜头内保持空间轴线、"
            "角色左右关系和相对尺度；只有明确切镜时才允许重新取景。容器的把手、开口、"
            "材质与结构不得在面板间改变；食物和小物件不得复制。",
            "【禁止】不得包含文字、序号、对白框、边框、九宫格、UI、Logo、水印、"
            "角色分身、明显3D/PBR质感或与面板顺序冲突的状态；猫咪不得直立、双足站立"
            "或拟人抱物；不得新增脚本未声明的"
            "杯子、食物、书本或其他可搬运道具。",
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
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    **_: object,
) -> CompiledPrompt:
    """根据最终有序故事板输入生成三段式Seedance执行Prompt。"""

    if input_plan.duration_seconds != episode.script.duration_seconds:
        raise ValueError("VideoInputPlan时长与EpisodeScript不一致")
    return _compile_video_body(
        episode,
        resolution=input_plan.resolution,
        duration_seconds=input_plan.duration_seconds,
        bindings=_describe_bindings(input_plan),
        style_profile=style_profile,
        series_profile=series_profile,
    )


def compile_video_prompt_preview(
    episode: EpisodePlan,
    *,
    resolution: str,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
) -> CompiledPrompt:
    """不绑定真实资产的执行Prompt预览；长度仅用于Web展示。"""

    if resolution not in {"480p", "720p"}:
        raise ValueError("视频分辨率只允许480p或720p")
    count = storyboard_panel_count(episode)
    bindings = _storyboard_binding_text(count)
    return _compile_video_body(
        episode,
        resolution=resolution,
        duration_seconds=episode.script.duration_seconds,
        bindings=bindings,
        style_profile=style_profile,
        series_profile=series_profile,
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
            f"本集地点与空间语义：{episode.script.scene}。每张面板都必须属于这个地点类型；"
            "参考图中若带有与本集不同的茶园、山坡、室内房间、海滨等背景，只能参考画法，"
            "不得把这些背景迁移到待审核故事板。地点类型错误必须令spatialContinuityOk=false。"
            f"结尾：{episode.script.ending.result}。",
            "布尔字段按轻量关系弧判定：identityOk检查每个可见面板中的人物主要面貌、固定短发、"
            "猫咪脸型与灰白斑纹及角色数量；bodyProportionOk检查头身比例、四肢结构、猫咪体型与"
            "尾巴是否从后躯自然连接；poseNaturalnessOk检查动物姿态是否符合真实猫科生物力学——"
            "猫咪必须四足行走、坐卧或跳跃，出现直立、双足站立、拟人行走或人手式抱物抓握即判false，"
            "前爪只能在四足姿态下拨按扶；actionSequenceOk检查建立、反应或探索、关系回报的顺序；"
            "spatialContinuityOk检查每张面板的地点类型、同一镜头内空间轴线、左右关系和相对尺度；"
            "明确切镜后的合理重新取景不算漂移，但切镜不能把本集地点替换为参考图背景；"
            "propContinuityOk检查关键道具类别、数量、服装层和必要座位；"
            "endingOk检查最后一张是否兑现本集回报。不要因左右手切换、轻微姿势、眼睛画法或"
            "道具朝向变化判失败。硬失败只包括明显换人、角色复制、严重身体失衡、猫尾连接异常、"
            "明显3D画风、同镜头无原因位置或尺度跳变、关键道具复制消失或变类、整层服装断裂、"
            "最终回报不可见。其他差异写入warnings。violations和evidence必须逐项指出面板序号，"
            "禁止只返回“身份不一致”这类无法指导重试的笼统描述。",
        )
    )


def compile_video_diagnostic_prompt(
    episode: EpisodePlan,
    *,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
    shot_order: int | None = None,
) -> str:
    """生成视频抽帧诊断Prompt；诊断不自动批准成片。

    ``shot_order``非空时只诊断该镜头片段：动作范围限定到该镜头覆盖的动作，
    结尾兑现按镜头切点或全集结尾判定。
    """

    if shot_order is None:
        scoped_actions = tuple(episode.script.actions)
        ending = episode.script.ending.result
    else:
        scoped_actions = _actions_for_shot(episode, shot_order)
        ending = (
            episode.script.ending.result
            if shot_order == len(episode.script.shots)
            else "本镜头以稳定切点结束，姿态与空间状态可衔接下一镜头"
        )
    actions = "；".join(
        f"{item.order}.{_actor_name(item.actor_id)}{item.action}" for item in scoped_actions
    )
    entities = _describe_continuity(episode)
    return "\n".join(
        (
            "你是生活流短视频抽帧诊断器，图片按时间顺序排列，只返回给定JSON。",
            f"关键实体：{entities}。动作：{actions}。结尾：{ending}。"
            f"声音设计：{episode.script.sound_design}。",
            f"检查同一人物和灰白猫、定稿画风（{style_profile.prompt_positive()}；排除"
            f"{style_profile.prompt_negative()}）、关键实体与服装连续性、动作先后和"
            "结尾兑现。对每个已登记关键道具逐帧核对数量、位置和持有者；任何一帧中"
            "同一道具同时留在原位置又出现在手中，均属于复制并使worldContinuityOk=false。"
            "猫咪必须保持四足姿态，出现直立、双足站立或拟人抱物使narrativeOrderOk=false。"
            "单帧遮挡不等于消失；证据必须包含帧序号。",
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
    """把动态镜头投影成可审核的静态状态，避免单张面板提前演完整段动作。"""

    script = episode.script
    shots = script.shots
    if len(shots) == 1:
        shot = shots[0]
        actions = _actions_for_shot(episode, shot.order)
        return (
            f"镜头1开场状态；{_describe_storyboard_entry(shot, actions[0])}。",
            f"镜头1动作进展；保持同一机位、空间轴线、角色左右关系和相对尺度；"
            f"{_describe_storyboard_result(shot, actions)}。",
            f"镜头1结束状态；保持同一机位和角色位置连续，在动作稳定后呈现"
            f"{script.ending.result}；关键结束画面为{_describe_end_states(episode)}。",
        )
    if len(shots) == 2:
        first, second = shots
        return (
            "镜头1关键状态；"
            f"{_describe_storyboard_result(first, _actions_for_shot(episode, first.order))}；"
            "动作稳定后形成可切换姿态。",
            f"镜头2进入状态；从镜头1稳定切点进入；"
            f"{_describe_storyboard_entry(second, _actions_for_shot(episode, second.order)[0])}。",
            f"镜头2结束状态；沿用镜头2的机位、空间轴线、角色左右关系和相对尺度，"
            f"不重新设计人物、猫咪或场景；呈现{script.ending.result}；关键结束画面为"
            f"{_describe_end_states(episode)}。",
        )
    first, second, third = shots
    return (
        "镜头1关键状态；"
        f"{_describe_storyboard_result(first, _actions_for_shot(episode, first.order))}；"
        "动作稳定后形成可切换姿态。",
        f"镜头2关键状态；从镜头1稳定切点进入；"
        f"{_describe_storyboard_result(second, _actions_for_shot(episode, second.order))}；"
        "动作稳定后形成可切换姿态。",
        f"镜头3进入状态；从镜头2稳定切点进入；"
        f"{_describe_storyboard_entry(third, _actions_for_shot(episode, third.order)[0])}。",
        f"镜头3结束状态；沿用镜头3的机位、空间轴线、角色左右关系和相对尺度，"
        f"不重新设计人物、猫咪或场景；呈现{script.ending.result}；关键结束画面为"
        f"{_describe_end_states(episode)}。",
    )


def _compile_video_body(
    episode: EpisodePlan,
    *,
    resolution: str,
    duration_seconds: int,
    bindings: str,
    style_profile: StyleProfile,
    series_profile: SeriesVisualProfile,
    shot_order: int | None = None,
) -> CompiledPrompt:
    """编译视频Prompt；``shot_order``非空时只编译单镜头片段（逐镜头生成）。"""

    script = episode.script
    shot_lines: list[str] = []
    for shot in script.shots:
        if shot_order is not None and shot.order != shot_order:
            continue
        transition = (
            "；动作与道具稳定后再切入下一镜头"
            if shot.order < len(script.shots)
            else f"；动作稳定后呈现最终可见回报：{script.ending.result}"
        )
        if shot_order is not None and shot.order < len(script.shots):
            # 逐镜头模式下非末镜头以稳定切点收尾，由下一镜首帧自然衔接。
            # 边界纪律：单镜头片段只演绎分配到本镜头的动作，提前演绎后续动作
            # 会让尾帧携带超前状态，下一镜继承后造成道具复制或状态矛盾。
            transition = (
                "；动作稳定后保持本镜头结束的姿态与空间状态，"
                "形成可衔接下一镜头的稳定切点；本片段只演绎本镜头分配的动作，"
                "禁止提前演绎后续镜头的动作，禁止提前改变关键道具的持有者或位置"
            )
        shot_lines.append(
            f"镜头{shot.order}：{_shot_directing_text(shot)}；"
            f"{_action_sequence_text(_actions_for_shot(episode, shot.order))}{transition}。"
        )
    shots = "\n".join(shot_lines)
    continuity = _describe_continuity(episode)
    lighting = _lighting_direction(script.scene, script.style_context)
    segment = "完整视频" if shot_order is None else f"单镜头片段（镜头{shot_order}）"
    return _compiled(
        "\n".join(
            (
                "【整体设定与素材绑定】"
                f"{bindings}生成{resolution}、9:16、{duration_seconds}秒{segment}。"
                f"人物身份：{series_profile.person_identity}；{series_profile.person_hair}；"
                f"{series_profile.person_body}。猫咪身份：{series_profile.cat_identity}。"
                f"猫咪动作约束：{series_profile.cat_motion_rules}。"
                f"本集外观：{script.appearance.description}。场景：{script.scene}。"
                f"画风：{style_profile.prompt_positive()}；排除{style_profile.prompt_negative()}；"
                f"光影：{lighting}。",
                f"【镜头顺序】\n{shots}",
                f"【质量、连续性与声音】关键实体闭环：{continuity}。声音：{script.sound_design}。"
                "人物主要面貌、固定短发、儿童头身比例和完整服装层保持稳定；猫咪脸型、灰白"
                "斑纹、体型及从后躯自然连接的尾巴保持稳定。动作惯性自然，面部与四肢结构清楚，"
                "同一镜头内空间轴线、角色左右关系和相对尺度连续。猫咪全程保持四足姿态，禁止"
                "直立、双足站立、拟人行走或人手式抓握抱物。禁止角色分身、关键道具无原因"
                "出现或消失、物体穿透悬空、服装断裂、字幕、水印、Logo和供应商UI；结尾不得以"
                "原地互看或完全静止填充时长。",
            )
        )
    )


def compile_shot_video_prompt(
    episode: EpisodePlan,
    *,
    shot_order: int,
    input_plan: VideoInputPlan,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
) -> CompiledPrompt:
    """逐镜头生成的单镜头片段Prompt；只含该镜头的动作与首帧锚定。"""

    if not any(item.order == shot_order for item in episode.script.shots):
        raise PromptCompilationError(f"Episode没有镜头{shot_order}")
    return _compile_video_body(
        episode,
        resolution=input_plan.resolution,
        duration_seconds=input_plan.duration_seconds,
        bindings=_describe_bindings(input_plan),
        style_profile=style_profile,
        series_profile=series_profile,
        shot_order=shot_order,
    )


def _lighting_direction(scene: str, style_context: str) -> str:
    """从真实场景语义提炼光影，不按早中晚写死模板。"""

    scene_text = scene.lower()
    if any(word in scene_text for word in ("雨", "阴", "雾", "潮湿")):
        return "使用雨后或阴天的柔和散射光、低反差与湿润环境反光"
    if any(word in scene_text for word in ("夜", "灯", "傍晚", "黄昏", "夕阳")):
        return "使用环境中已有灯光或低角度暖光形成自然层次，不添加舞台式高光"
    if style_context == "indoor":
        return "使用场景中窗户或室内灯具提供的柔和方向光，阴影克制"
    return "使用符合天气与地点的柔和自然环境光，保持日系二维插画的哑光水彩质感和克制景深"


def _reference_role_description(semantic_key: str) -> str:
    """把资产语义键翻译成供应商可理解的职责，不向Prompt泄露内部哈希。"""

    if semantic_key == "person:headshot":
        return "人物主要面貌与短发身份"
    if semantic_key == "person:fullbody":
        return "人物全身比例与体型"
    if semantic_key in {"person:front", "person:side", "person:back"}:
        view = semantic_key.split(":", 1)[1]
        view_name = {"front": "正面", "side": "侧面", "back": "背面"}[view]
        return f"人物{view_name}轮廓、短发形状与身体朝向"
    if semantic_key.startswith("look:"):
        return "本时段已批准的人物面貌、全身服饰与最终二维画风"
    if semantic_key.startswith("cat:"):
        return "同一只灰白猫的体型与主要斑纹"
    if semantic_key == "style:line_texture":
        return "定稿日系二维治愈插画的细线、水彩质感与哑光色彩"
    if semantic_key in {"style:indoor", "style:outdoor"}:
        return "与本集场景匹配的定稿自然色彩、光线与环境氛围"
    if semantic_key.startswith("element:"):
        return "本集关键道具的颜色、形状与材质"
    if semantic_key.startswith("scene:"):
        return "本集场景的空间与环境元素"
    return "本集必要视觉参考"


def _storyboard_reference_instruction(index: int, semantic_key: str) -> str:
    """为Seedream建立互斥参考职责，避免角色来源彼此竞争。"""

    prefix = f"图{index}"
    if semantic_key == "style_chain:previous_panel":
        return (
            f"{prefix}是上一时段已批准故事板的画风基准：只提取线条、水彩质感、"
            "哑光色彩与光影处理，使全天三集画面风格统一；禁止带入其中的场景、"
            "人物动作、道具与构图内容"
        )
    if semantic_key.startswith("look:"):
        return (
            f"{prefix}是全组唯一人物与最终画风基准，人物面貌、固定短发、体型、本集服装、"
            "线条、水彩质感和哑光色彩均以此图为准；只继承画法，不继承其简化背景"
        )
    if semantic_key.startswith("cat:"):
        return f"{prefix}中的灰白猫是全组唯一猫咪基准，脸型、斑纹、体型和尾巴比例均以此图为准"
    if semantic_key.startswith("style:"):
        return f"{prefix}只提供线条、色彩、光影和环境画风，不提供人物或猫咪造型"
    if semantic_key.startswith("element:"):
        return f"{prefix}只提供关键道具的颜色、形状与材质，不改变角色或场景"
    if semantic_key.startswith("scene:"):
        return f"{prefix}只提供场景空间和环境元素，不改变人物、猫咪或服装"
    return f"{prefix}只负责{_reference_role_description(semantic_key)}"


def _storyboard_binding_text(count: int) -> str:
    """把有序面板翻译为Seedance可理解的镜头职责。"""

    return _panel_role_text(tuple(f"@图片{index}" for index in range(1, count + 1)), count)


def _panel_role_text(aliases: tuple[str, ...], count: int) -> str:
    """按面板数量分配镜头职责；别名来自真实绑定序号。"""

    roles = {
        1: ("本镜头严格开场画面",),
        2: ("严格开场画面", "严格结尾画面"),
        3: ("开场与镜头1构图", "中段反应或进展", "最后镜头的结果与回报"),
        4: ("镜头1构图", "镜头2进展", "镜头3进入状态", "镜头3结果与最终回报"),
    }
    if count not in roles:
        raise PromptCompilationError("Seedance故事板输入只允许1至4张")
    return "；".join(f"{alias}作为{role}" for alias, role in zip(aliases, roles[count])) + "。"


def _describe_bindings(input_plan: VideoInputPlan) -> str:
    """描述素材绑定：身份参考锚定本体，面板承担镜头构图职责。"""

    if not input_plan.bindings:
        raise PromptCompilationError("Seedance故事板输入不能为空")
    identity = tuple(
        item
        for item in input_plan.bindings
        if item.semantic_key.startswith(("person:", "cat:"))
    )
    # 帧素材=故事板面板或上一镜头真实尾帧（逐镜头生成）。
    panels = tuple(
        item
        for item in input_plan.bindings
        if item.semantic_key.startswith(("storyboard:panel-", "shot_tail:"))
    )
    parts: list[str] = []
    for binding in identity:
        if binding.semantic_key.startswith("person:"):
            parts.append(
                f"{binding.prompt_alias}是人物的本体身份基准：面容、发型、"
                "头身比例严格以此为准；其服装不作为约束，人物衣着以下文"
                "本集外观描述为准"
            )
        else:
            parts.append(
                f"{binding.prompt_alias}是猫咪的本体身份基准：脸型、灰白斑纹、"
                "体型与尾巴严格以此为准；佩戴的帽饰背包按本集剧情需要呈现"
            )
    if panels:
        parts.append(
            _panel_role_text(
                tuple(item.prompt_alias for item in panels),
                len(panels),
            )
        )
    return "".join(f"{item}。" if not item.endswith("。") else item for item in parts)


def _describe_continuity(episode: EpisodePlan) -> str:
    """用自然语言描述必要道具关系，不暴露数据库ID或生命周期术语。"""

    continuity = episode.script.continuity
    props = [item for item in continuity.entities if item.kind is EntityKind.PROP]
    if not props:
        return "同一个中性儿童和同一只灰白猫始终在场，服饰与数量保持一致"
    rendered: list[str] = []
    for item in props:
        start = _placement_text(episode, item.start_state)
        end = _placement_text(episode, item.end_state)
        description = f"{item.name}开场{start}，结尾{end}"
        if item.change_reason:
            description += f"，变化原因是{item.change_reason}"
        if item.start_state.present and item.end_state.present and start != end:
            description += "；移动完成后原位置为空，不得保留同一物体的副本"
        rendered.append(description)
    return "；".join(rendered)


def _describe_character_boundaries(episode: EpisodePlan) -> str:
    """把人物与猫咪的承重范围写成自然语言，避免无动作跳上其他家具。"""

    characters = [
        item
        for item in episode.script.continuity.entities
        if item.kind in {EntityKind.PERSON, EntityKind.CAT}
    ]
    rendered: list[str] = []
    for item in characters:
        start = _placement_text(episode, item.start_state)
        end = _placement_text(episode, item.end_state)
        if start == end:
            rendered.append(
                f"{item.name}全组只在{start.removeprefix('位于')}所代表的地面、座面或活动范围内，"
                "不得无动作跳到桌面、柜面或其他家具表面"
            )
        else:
            rendered.append(f"{item.name}开场{start}，只按动作路径移动，结尾{end}")
    return "；".join(rendered) or "人物与猫咪只在镜头声明的合法承重表面活动"


def _storyboard_prop_invariants(episode: EpisodePlan) -> str:
    """把关键道具的唯一性写入每张面板，防止组图在中间帧复制物体。"""

    props = [
        item
        for item in episode.script.continuity.entities
        if item.kind is EntityKind.PROP
    ]
    if not props:
        return "本面板不新增可搬运道具"
    names = "、".join(item.name for item in props)
    return (
        f"本面板中的关键道具只有{names}，每种全画面最多一个实例；"
        "道具被手持时原位置必须为空，道具落在地面或家具上时人物手中不得保留副本"
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


def _describe_storyboard_result(
    shot: ShotPlan,
    actions: tuple[ActionStage, ...],
) -> str:
    """生成单张故事板能稳定表达的镜头结果，不重复动态动作全文。"""

    results = "；".join(
        f"{_actor_name(action.actor_id)}的动作结果为{action.visible_result}"
        for action in actions
    )
    return f"{_shot_directing_text(shot)}；本面板冻结在动作完成后的清晰状态：{results}"


def _describe_storyboard_entry(shot: ShotPlan, action: ActionStage) -> str:
    """描述新镜头刚进入的稳定瞬间，防止故事板提前呈现本镜头回报。"""

    direction_start = _first_visual_clause(shot.direction)
    action_start = _first_visual_clause(action.action)
    return (
        f"{_shot_camera_text(shot)}；画面方向与主体位置起点为{direction_start}；"
        f"执行主体为{_actor_name(action.actor_id)}，动作只进行到“{action_start}”这一进入瞬间；"
        f"尚未出现后续动作结果“{action.visible_result}”，并继承上一面板的服装、道具和空间状态"
    )


def _first_visual_clause(text: str) -> str:
    """提取导演动作的首个可见瞬间，供进入面板使用。"""

    stripped = text.strip()
    indexes = [stripped.find(mark) for mark in ("，", "；", "。", "\n")]
    boundaries = [index for index in indexes if index > 0]
    return stripped[: min(boundaries)].strip() if boundaries else stripped


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
    return f"{_shot_camera_text(shot)}，画面方向与主体位置：{shot.direction}"


def _shot_camera_text(shot: ShotPlan) -> str:
    """输出镜头的稳定摄影参数；进入面板可复用而不携带完整动作结果。"""

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
        f"唯一运镜为{moves[shot.camera_move.value]}"
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
        rendered.append(
            f"执行主体为{_actor_name(item.actor_id)}：{action}；动作后画面清楚形成"
            f"{item.visible_result}"
        )
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
