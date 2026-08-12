"""V4镜头建议、锚点、单镜头视频和审核Prompt编译。"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import ShotPromptContext
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
) -> str:
    context = (
        "本场景不加载其他剧情。"
        if not context_note
        else f"可选关联说明：{context_note.strip()}。只把它当作建议，不得改写用户原文。"
    )
    return f"""你是生活短片镜头设计师。把用户原始场景转换成可编辑的单镜头视频队列。

项目：{project_title}
场景：{scene_title}
用户原文：{source_text}
{context}

使用内置“生活短片镜头化”规则：
1. 每张镜头卡只包含一个连续机位、一项主要动作链和一个稳定可见结果。
2. 镜头描述用完整自然语言写明景别、机位、唯一运镜、人物与猫咪站位、
   真实动作主体、动作路径、速度、接触对象、结果和稳定切点。
3. 猫咪保持四足自然行为；人物负责需要手和工具的操作。
4. 不虚构用户原文没有的第二个事件，不输出世界状态、数据库字段或生命周期术语。
5. 每镜建议8至15秒，默认优先8秒；不要为了填满时长重复动作。
6. 只输出sceneTitle和shots；每个shot只有title、direction、suggestedDurationSeconds。
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
        f"""【主体、画风和素材职责】输出{input_plan.resolution}、9:16竖屏、{context.duration_seconds}秒单镜头完整视频，使用原生环境声和动作声。{series_profile.person_identity}；{series_profile.person_hair}；{series_profile.person_body}；{series_profile.cat_identity}。采用{style_profile.prompt_positive()}，排除{style_profile.prompt_negative()}。素材：{binding_text}

【单镜头机位、动作路径和结果】项目“{context.project_title}”，场景“{context.scene_title}”，镜头“{context.shot_title}”。{context.direction}

【关键关系、声音和稳定结尾】只完成上述一个连续镜头；动作接触关系必须服从镜头文字，角色与道具数量保持，切点落在动作完成后的稳定状态。原生环境声和接触声与画面同步，无对白、旁白或歌词；禁止角色分身、无原因换装、关键物体悬空或自动恢复、字幕、水印、Logo和供应商UI。{retry}"""
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
