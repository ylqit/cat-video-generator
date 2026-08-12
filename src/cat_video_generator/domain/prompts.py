"""V5造型建议、片段内分镜、场景定妆和视频Prompt编译。"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import SceneLookPlan, ShotPromptContext
from .rendering import VideoInputPlan
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


def _compiled(text: str) -> CompiledPrompt:
    normalized = text.strip()
    if not normalized:
        raise PromptCompilationError("Prompt不能为空")
    return CompiledPrompt(
        text=normalized,
        char_count=len(normalized),
        utf8_bytes=len(normalized.encode("utf-8")),
    )


def compile_shot_suggestion_prompt(
    *,
    project_title: str,
    scene_title: str,
    source_text: str,
    context_note: str | None,
    story_mode: str,
    target_shot_count: int,
) -> str:
    context = (
        "本场景不加载其他剧情。"
        if not context_note
        else f"可选关联说明：{context_note.strip()}。只把它当作建议，不得改写用户原文。"
    )
    mode = "单片段短片" if story_mode == "single" else "多片段剧情"
    return f"""你是生活短片导演。把用户原始场景转换成可编辑的竖屏视频片段队列。

项目：{project_title}
场景：{scene_title}
用户原文：{source_text}
{context}
创作模式：{mode}。严格输出{target_shot_count}个视频片段。

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
) -> CompiledPrompt:
    refs = "；".join(reference_descriptions) or "没有附加参考图，严格按文字设定生成。"
    retry = (
        ""
        if not regeneration_instruction
        else f"\n【本次重做目标】{regeneration_instruction.strip()}"
    )
    return _compiled(
        f"""【任务】生成一张无字9:16竖屏开场锚点，只表现当前镜头动作开始前的稳定状态。
【主体】{series_profile.person_identity}；{series_profile.person_hair}；{series_profile.person_body}；{series_profile.cat_identity}。全图人物与猫咪数量准确，不复制角色。
【定稿画风】{style_profile.prompt_positive()}；排除{style_profile.prompt_negative()}。
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
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> CompiledPrompt:
    refs = "；".join(reference_descriptions) or "没有附加参考图，严格按Canon文字设定生成。"
    return _compiled(
        f"""【任务】为项目“{project_title}”的场景“{scene_title}”生成一张无字9:16竖屏场景定妆图，作为该场景所有视频片段共享的服装、配件、道具和人猫比例参考。
【Canon身份】{series_profile.person_identity}；{series_profile.person_hair}；{series_profile.person_body}；{series_profile.cat_identity}。参考图职责：{refs}；不得改写Canon身份、复制人物或复制猫咪。
【场景原文】{scene_text}
【人物服装】{look_plan.person_wardrobe or '沿用Canon基础服装'}。
【人物配件】{look_plan.person_accessories or '无新增配件'}。
【猫咪外观】猫咪{look_plan.cat_appearance or '保持Canon外观且不增加服饰'}。
【关键道具】{look_plan.key_props or '无新增关键道具'}。
【画风】{style_profile.prompt_positive()}；排除{style_profile.prompt_negative()}。
【构图】人物与猫咪同处一个可读的稳定准备状态，完整展示服饰、配件、关键道具和相对比例；不表现后续动作高潮。
【限制】不要字幕、编号、分格、边框、UI、Logo或水印。"""
    )


def compile_shot_video_prompt(
    context: ShotPromptContext,
    input_plan: VideoInputPlan,
    *,
    binding_descriptions: tuple[str, ...],
    regeneration_instruction: str | None = None,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> CompiledPrompt:
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
    return _compiled(
        f"""【主体、画风和素材职责】输出{input_plan.resolution}、9:16竖屏、{context.duration_seconds}秒的一个完整视频片段，使用原生环境声和动作声。{series_profile.person_identity}；{series_profile.person_hair}；{series_profile.person_body}；{series_profile.cat_identity}。采用{style_profile.prompt_positive()}，排除{style_profile.prompt_negative()}。素材：{binding_text}

【片段内子镜头、动作路径和结果】项目“{context.project_title}”，场景“{context.scene_title}”，视频片段“{context.shot_title}”。严格按下列编号子镜头的顺序、空间连续性和因果关系执行：{context.direction}

【关键关系、声音和稳定结尾】整个片段只表达一个生活微事件，2至4个子镜头连续完成同一动作链；猫咪是主要观察和行动对象，人物负责手部或工具操作。动作接触关系必须服从片段文字，角色与道具数量保持，最后切点落在交互结果完成后的稳定状态。原生环境声和接触声与画面同步，无对白、旁白或歌词；禁止角色分身、无原因换装、关键物体悬空或自动恢复、字幕、水印、Logo和供应商UI。{retry}"""
    )


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
