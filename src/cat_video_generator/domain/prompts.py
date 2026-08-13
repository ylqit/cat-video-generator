"""V5造型建议、片段内分镜、场景定妆和视频Prompt编译。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .contracts import (
    SceneDraft,
    SceneLookPlan,
    ShotCardDraft,
    ShotPromptContext,
    VisualProfileDraft,
)
from .rendering import VideoInputPlan
from .shot_assistance import ShotLocalAnalysis
from .visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
    SeriesVisualProfile,
    StyleProfile,
)


class PromptCompilationError(ValueError):
    """业务对象不能安全投影为供应商Prompt。"""


@dataclass(frozen=True, slots=True)
class CompiledPrompt:
    text: str
    char_count: int
    utf8_bytes: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CompiledShotVideoPrompt:
    creative_body: str
    system_shell: CompiledPrompt
    final: CompiledPrompt


def _compiled(text: str) -> CompiledPrompt:
    normalized = text.strip()
    if not normalized:
        raise PromptCompilationError("Prompt不能为空")
    return CompiledPrompt(
        text=normalized,
        char_count=len(normalized),
        utf8_bytes=len(normalized.encode("utf-8")),
    )


def compile_story_diagnosis_prompt(
    *,
    project_title: str,
    scene: SceneDraft,
    visual_profile: VisualProfileDraft,
    previous_scene_summary: str | None,
    next_scene_summary: str | None,
) -> str:
    previous = previous_scene_summary or "无上一场景。"
    following = next_scene_summary or "无下一场景。"
    return f"""你是剧情医生。你的职责是分析用户原始剧情是否连续、物理可表达并适合后续视频生成；
只提出诊断和候选方案，不改写原稿。

【项目】{project_title}
【当前场景】{scene.title}
【用户原始剧情】{scene.source_text}
【补充口述】{scene.context_note or '无'}
【上一场景摘要】{previous}
【下一场景摘要】{following}
【目标视频片段数量】{scene.target_shot_count}
【每片段允许时长】8至15秒
【长期人物约束】{visual_profile.person_identity}；{visual_profile.person_hair}；{visual_profile.person_body}
【长期猫咪约束】{visual_profile.cat_identity}
【系列画风】{'、'.join(visual_profile.style_positive)}

请逐条检查：人物、猫咪、服饰和长期身份是否冲突；道具初始位置与后续流向是否重复或断裂；容器、场景结构、主体尺寸和动作路径是否可能不匹配；动作是否缺少合理起点、接触路径或完成结果；事件数量与目标片段数是否匹配；因果关系和相邻场景是否连续；人猫互动是否符合生活切片定位；哪些描述可以直接生成、哪些应改写或拆分。

输出总体评价和问题列表。每个问题必须引用原文证据，说明生成影响并给出通用修改建议。再分别输出保守修订、平衡优化、创作增强三种方案，说明改动范围与取舍。不要在本阶段输出重写后的完整剧情，不要输出镜头或供应商Prompt。""".strip()


def compile_story_rewrite_prompt(
    *,
    project_title: str,
    scene: SceneDraft,
    visual_profile: VisualProfileDraft,
    accepted_diagnosis: dict[str, object],
) -> str:
    accepted_json = json.dumps(
        accepted_diagnosis,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    return f"""你是剧本编辑。根据已经由用户确认的剧情诊断，
把原始剧情重写为一份完整、连续、尚未拆分镜头的场景剧情。

【项目】{project_title}
【场景】{scene.title}
【原始剧情】{scene.source_text}
【补充口述】{scene.context_note or '无'}
【用户已确认的诊断与方案】
{accepted_json}
【长期人物约束】{visual_profile.person_identity}；{visual_profile.person_hair}；{visual_profile.person_body}
【长期猫咪约束】{visual_profile.cat_identity}

保持用户的核心事件和情绪目标；统一每件道具的初始位置、移动路径和后续流向；把不合理的尺寸、收纳或操作关系改成可生成的合理形态；明确人物与猫咪各自能够完成的动作；场景服饰只能服务本场剧情，不得反向改变长期角色身份。不要拆分视频片段，不要写机位、景别、精确秒点或供应商Prompt。

只输出完整重写剧情、修改摘要和仍需人工决定的问题。""".strip()


def compile_shot_suggestion_prompt(
    *,
    project_title: str,
    scene_title: str,
    source_text: str,
    context_note: str | None,
    story_mode: str,
    target_shot_count: int,
    visual_profile: VisualProfileDraft | None = None,
) -> str:
    profile = visual_profile or VisualProfileDraft()
    context = (
        "本场景不加载其他剧情。"
        if not context_note
        else f"可选关联说明：{context_note.strip()}。只把它当作建议，不得改写用户原文。"
    )
    mode = "单片段短片" if story_mode == "single" else "多片段剧情"
    return f"""你是分镜导演。把用户已经确认的场景剧情转换成可编辑的竖屏视频片段队列；
不得回退到未经确认的旧口述。

项目：{project_title}
场景：{scene_title}
已批准剧情：{source_text}
{context}
创作模式：{mode}。严格输出{target_shot_count}个视频片段。
长期人物：{profile.person_identity}；{profile.person_hair}；{profile.person_body}。
长期猫咪：{profile.cat_identity}。

使用内置“生活短片镜头化”规则：
1. 每个视频片段只表达一个生活微事件、一项主要动作链和一个稳定可见结果；
   direction内部写2至4个编号子镜头，让动作和情绪连续，不把多个独立事件挤在一起。
2. 每个子镜头依次写清景别与机位、人物和猫咪的空间关系、真实动作主体、动作路径与接触对象、
   人物配合、互动结果、最多一种主要运镜、环境或接触声，以及稳定收尾切点。
3. 猫咪是主要观察和行动对象，保持自然四足行为；人物负责需要手部或工具完成的拿取、开合和穿戴。
   剧情没有明确要求时，不给猫咪添加帽子、背包，不让猫咪操作复杂工具。
4. 人与猫必须发生可见的因果互动，不能只是在同一画面共存；节奏温和、低冲突、日常治愈。
5. 不虚构用户原文没有的第二个事件，不输出数据库或生命周期术语。
6. 每个片段建议8至15秒；suggestedDurationSeconds填写整数，但不得在direction中编造精确秒点。
7. 同时给出lookPlan：personWardrobe、personAccessories、catAppearance、keyProps、
   environmentStyle、personPose、catPose、composition、additionalInstructions、
   imageRecommended、recommendationReason。只有服饰、配件、关键道具或双主体关系需要视觉确认时才建议定妆图。
8. 只输出sceneTitle、lookPlan和shots；每个shot只有title、direction、suggestedDurationSeconds。
""".strip()


def compile_anchor_prompt(
    context: ShotPromptContext,
    *,
    reference_descriptions: tuple[str, ...],
    regeneration_instruction: str | None = None,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
    visual_profile: VisualProfileDraft | None = None,
) -> CompiledPrompt:
    profile = visual_profile or VisualProfileDraft(
        personIdentity=series_profile.person_identity,
        personHair=series_profile.person_hair,
        personBody=series_profile.person_body,
        catIdentity=series_profile.cat_identity,
        stylePositive=style_profile.positive_features,
        styleNegative=style_profile.excluded_features,
    )
    refs = "；".join(reference_descriptions) or "没有附加参考图，严格按文字设定生成。"
    retry = (
        ""
        if not regeneration_instruction
        else f"\n【本次重做目标】{regeneration_instruction.strip()}"
    )
    return _compiled(
        f"""【任务】生成一张无字9:16竖屏开场锚点，只表现当前镜头动作开始前的稳定状态。
【主体】{profile.person_identity}；{profile.person_hair}；{profile.person_body}；{profile.cat_identity}。全图人物与猫咪数量准确，不复制角色。
【定稿画风】{'、'.join(profile.style_positive)}；排除{'、'.join(profile.style_negative)}。
【素材职责】{refs}任何画风或道具参考不得改写人物和猫咪身份。
【场景】{context.scene_title}。{context.scene_text}
【当前镜头】{context.shot_title}：{context.direction}
【限制】不要提前绘制动作结果，不要文字、编号、边框、UI、Logo或水印。{retry}"""
    )


def compile_scene_look_prompt(
    *,
    project_title: str,
    scene_title: str,
    scene_text: str,
    look_plan: SceneLookPlan,
    reference_descriptions: tuple[str, ...],
    visual_profile: VisualProfileDraft | None = None,
    regeneration_instruction: str | None = None,
) -> CompiledPrompt:
    profile = visual_profile or VisualProfileDraft()
    refs = "；".join(reference_descriptions) or "没有附加参考图，严格按Canon文字设定生成。"
    retry = (
        ""
        if not regeneration_instruction
        else f"\n【本次单项修正】{regeneration_instruction.strip()}。其他已锁定内容保持不变。"
    )
    return _compiled(
        f"""【任务】为项目“{project_title}”的场景“{scene_title}”生成一张无字9:16竖屏场景定妆图，作为该场景所有视频片段共享的服装、配件、道具和人猫比例参考。
【人物身份锁定】{profile.person_identity}；{profile.person_hair}；{profile.person_body}。人物可以按本场景换装，但脸型、五官、发型、年龄感和头身比例必须保持。
【猫咪身份锁定】{profile.cat_identity}。猫咪保持同一只，服饰或场景不得改写脸部、毛色分区、虎斑、眼睛、尾巴和体型。
【参考图职责】{refs}；每张图只承担声明职责，不得改写Canon身份，不得互相改写角色、服装、道具或画风。
【场景原文】{scene_text}
【人物服装】{look_plan.person_wardrobe or '沿用Canon基础服装'}。
【人物配件】{look_plan.person_accessories or '无新增配件'}。
【猫咪外观】猫咪{look_plan.cat_appearance or '保持Canon外观且不增加服饰'}。
【关键道具】{look_plan.key_props or '无新增关键道具'}。
【姿态】人物{
            look_plan.person_pose or '自然站立或蹲坐，双手处于稳定准备状态'
        }；猫咪{look_plan.cat_pose or '保持自然四足站立或坐姿'}。
【画风锁定】{'、'.join(profile.style_positive)}；排除{'、'.join(profile.style_negative)}。当前环境采用{look_plan.environment_style.value}画风变体。
【构图】{
            look_plan.composition
            or '人物与猫咪同处一个可读的稳定准备状态，完整展示服饰、配件、关键道具和相对比例'
        }；不表现后续动作高潮。
【补充要求】{look_plan.additional_instructions or '无'}。
【限制】不要字幕、编号、分格、边框、UI、Logo或水印。{retry}"""
    )


def compile_shot_video_prompt(
    context: ShotPromptContext,
    input_plan: VideoInputPlan,
    *,
    binding_descriptions: tuple[str, ...],
    regeneration_instruction: str | None = None,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
    visual_profile: VisualProfileDraft | None = None,
) -> CompiledPrompt:
    return compile_shot_video_prompt_parts(
        context,
        input_plan,
        binding_descriptions=binding_descriptions,
        regeneration_instruction=regeneration_instruction,
        series_profile=series_profile,
        style_profile=style_profile,
        visual_profile=visual_profile,
    ).final


def compile_shot_video_prompt_parts(
    context: ShotPromptContext,
    input_plan: VideoInputPlan,
    *,
    binding_descriptions: tuple[str, ...],
    regeneration_instruction: str | None = None,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
    visual_profile: VisualProfileDraft | None = None,
) -> CompiledShotVideoPrompt:
    profile = visual_profile or VisualProfileDraft(
        personIdentity=series_profile.person_identity,
        personHair=series_profile.person_hair,
        personBody=series_profile.person_body,
        catIdentity=series_profile.cat_identity,
        stylePositive=style_profile.positive_features,
        styleNegative=style_profile.excluded_features,
    )
    aliases = {item.prompt_alias for item in input_plan.bindings}
    for description in binding_descriptions:
        alias = description.split("=", 1)[0].strip()
        if alias.startswith("@") and alias not in aliases:
            raise PromptCompilationError(f"素材说明引用了未绑定别名{alias}")
    binding_text = "；".join(binding_descriptions) or "本镜头采用纯文本生成，不绑定图片。"
    retry = (
        ""
        if not regeneration_instruction
        else f"\n【本次重做目标】{regeneration_instruction.strip()}"
    )
    prefix = (
        f"【主体、画风和素材职责】输出{input_plan.resolution}、9:16竖屏、"
        f"{context.duration_seconds}秒的一个完整视频片段，使用原生环境声和动作声。"
        f"{profile.person_identity}；{profile.person_hair}；{profile.person_body}；"
        f"{profile.cat_identity}。采用{'、'.join(profile.style_positive)}，"
        f"排除{'、'.join(profile.style_negative)}。"
        "项目视觉档案负责长期人物和猫咪身份及系列画风；"
        "场景基础定妆和片段素材只承担各自声明的视觉职责，不得反向改写长期身份。"
        f"素材：{binding_text}"
    )
    suffix = (
        "【系统技术限制】严格执行已确认创作正文，"
        "不由系统补写剧情、动作、空间关系、节奏或声音。"
        "保持输入图片对应主体与素材数量；禁止角色分身、无原因换装、"
        f"关键物体悬空或自动恢复、字幕、水印、Logo和供应商UI。{retry}"
    )
    system_shell = _compiled(
        f"""{prefix}

【片段内子镜头、动作路径和结果】项目“{context.project_title}”，场景“{context.scene_title}”，视频片段“{context.shot_title}”。正文由片段已确认正文注入，此技术外壳不改写创作内容。

{suffix}"""
    )
    final = _compiled(
        f"""{prefix}

【片段内子镜头、动作路径和结果】项目“{context.project_title}”，场景“{context.scene_title}”，视频片段“{context.shot_title}”。严格按下列编号子镜头的顺序、空间连续性和因果关系执行：{context.direction}

{suffix}"""
    )
    return CompiledShotVideoPrompt(
        creative_body=context.direction,
        system_shell=system_shell,
        final=final,
    )


def compile_shot_assistance_prompt(
    *,
    project_title: str,
    scene_title: str,
    scene_text: str,
    current: ShotCardDraft,
    previous: ShotCardDraft | None,
    following: ShotCardDraft | None,
    visual_profile: VisualProfileDraft,
    local_analysis: ShotLocalAnalysis,
    reference_manifest: tuple[str, ...],
) -> str:
    previous_text = (
        "无上一片段"
        if previous is None
        else f"{previous.title}：{previous.direction}"
    )
    following_text = (
        "无下一片段"
        if following is None
        else f"{following.title}：{following.direction}"
    )
    references = "\n".join(reference_manifest) or "本次没有选择图片。"
    return f"""你是二维治愈生活短片的片段级创作分析器。
只返回符合Schema的候选建议，不直接覆盖用户稿，也不直接提交视频生成。

【项目与场景】项目“{project_title}”，场景“{scene_title}”：{scene_text}
【长期角色与画风】人物：{visual_profile.person_identity}；{visual_profile.person_hair}；{visual_profile.person_body}。猫咪：{visual_profile.cat_identity}。画风：{'、'.join(visual_profile.style_positive)}。排除：{'、'.join(visual_profile.style_negative)}。
【上一片段】{previous_text}
【当前片段】标题：{current.title}；目标总时长：{current.duration_seconds}秒；场景定妆策略：{current.scene_look_usage.value}；锚点方式：{current.anchor_mode.value}；完整分镜：{current.direction}
【下一片段】{following_text}
【免费本地诊断】{local_analysis.model_dump_json(by_alias=True)}
【本次实际分析图片，按顺序】
{references}

请实际查看每张图片，而不是只根据素材ID判断。重点比较图片里的动作起始状态、人物与猫咪位置、
道具所在位置、姿态、构图和当前分镜是否一致；判断场景定妆更适合off、appearance_only、
full_reference还是derive_anchor，并说明是否需要独立开场锚点。

分析动作密度、2至4个连续子镜头的定性节奏、推荐总时长、当前与相邻片段的重复、遗漏、
因果断裂、每张参考图职责、锚点方式以及Prompt风险。输出一份可编辑的Seedance 创作正文；
必要时再给出保守版和稳定版两个候选正文。缩短时优先删除重复建立、次要道具动作和冗余反应；
延长时只增加观察、动作完成过程、互动反馈或稳定收尾，不增加第二个故事事件。
猫咪是主要观察和行动对象，人物承担手部和工具操作。不要为子镜头编造精确秒点。
patch只给出确有必要且等待用户勾选接受的字段修改稿，
且direction必须与creativeBody完全一致。""".strip()


def compile_video_review_prompt(context: ShotPromptContext) -> str:
    return f"""检查这组按时间顺序抽取的视频帧，只给出创作建议，不自动批准或拒绝。
镜头：{context.shot_title}
预期：{context.direction}
请按时间点指出人物或猫咪身份、肢体结构、关键道具、动作顺序、构图和画风问题；轻微表情或普通背景变化只记为提示。""".strip()


def compile_range_edit_prompt(
    context: ShotPromptContext,
    *,
    instruction: str,
    source_start_ms: int,
    source_end_ms: int,
) -> CompiledPrompt:
    if not instruction.strip():
        raise PromptCompilationError("区间编辑目标不能为空")
    return _compiled(
        f"""严格编辑@视频1中{source_start_ms}ms至{source_end_ms}ms对应的单一问题：{instruction.strip()}。
@图片1是选区前边界，@图片2是选区后边界。保持人物、猫咪、服装、场景轴线、动作方向和镜头运动连续。
当前镜头预期：{context.direction}
只生成修复候选；区间外将由本地时间轴沿用原视频素材。"""
    )
