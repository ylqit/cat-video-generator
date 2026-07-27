from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import ContentValidationError, validate_render_plan
from .visual_policy import (
    VisualControl,
    VisualInputMode,
    select_visual_input,
    validate_visual_input_asset_ids,
)


@dataclass(frozen=True, slots=True)
class VisualReferences:
    person_asset_id: str
    cat_asset_id: str
    style_asset_ids: tuple[str, ...]


def compile_render_plan(
    episode: dict[str, Any],
    *,
    plan_revision: int,
    render_revision: int,
    references: VisualReferences,
    scene_keyframe_asset_ids: tuple[str, ...] = (),
    retry_after_identity_or_composition_drift: bool = False,
) -> dict[str, Any]:
    control_value = episode["visualControl"]
    decision = select_visual_input(
        VisualControl(
            requires_exact_opening=control_value["requiresExactOpening"],
            requires_exact_ending=control_value["requiresExactEnding"],
            complex_subject_interaction=control_value[
                "complexSubjectInteraction"
            ],
            critical_prop_state=control_value["criticalPropState"],
        ),
        retry_after_identity_or_composition_drift=(
            retry_after_identity_or_composition_drift
        ),
    )
    expected_keyframes = {
        VisualInputMode.DIRECT_REFERENCES: 0,
        VisualInputMode.GENERATED_FIRST_FRAME: 1,
        VisualInputMode.GENERATED_FIRST_LAST_FRAMES: 2,
    }[decision.mode]
    if len(scene_keyframe_asset_ids) != expected_keyframes:
        raise ContentValidationError(
            f"{decision.mode.value} requires exactly {expected_keyframes} "
            "approved scene keyframe asset(s)."
        )
    if not 1 <= len(references.style_asset_ids) <= 7:
        raise ContentValidationError(
            "RenderPlan requires between 1 and 7 approved style references."
        )
    validate_visual_input_asset_ids(
        person_asset_id=references.person_asset_id,
        cat_asset_id=references.cat_asset_id,
        style_reference_asset_ids=references.style_asset_ids,
        scene_keyframe_asset_ids=scene_keyframe_asset_ids,
    )

    timeline_cues = [
        {
            "beatOrder": beat["order"],
            "startMs": beat["startMs"],
            "endMs": beat["endMs"],
            "promptText": _timeline_text(beat),
        }
        for beat in episode["beats"]
    ]
    plan = {
        "schemaVersion": 1,
        "renderPlanId": (
            f"render-{episode['episodeId']}-r{render_revision}"
        ),
        "episodeId": episode["episodeId"],
        "planRevision": plan_revision,
        "renderRevision": render_revision,
        "generationStrategy": "single_pass",
        "clipCount": 1,
        "durationMs": episode["durationMs"],
        "visualInputMode": decision.mode.value,
        "identityReferences": {
            "personAssetId": references.person_asset_id,
            "catAssetId": references.cat_asset_id,
        },
        "styleReferenceAssetIds": list(references.style_asset_ids),
        "sceneKeyframeAssetIds": list(scene_keyframe_asset_ids),
        "visualInputReasonCodes": [
            reason.value for reason in decision.reason_codes
        ],
        "timelineCues": timeline_cues,
        "videoPrompt": _video_prompt(episode, decision.mode, timeline_cues),
        "audioPlan": {
            "mode": "native",
            "allowDialogue": False,
            "allowNarration": False,
            "allowLyrics": False,
            "audioCues": _audio_cues(episode),
            "externalAudioAssetIds": [],
        },
        "finalizationPolicy": "validate_then_passthrough",
        "deliveryProfile": {
            "container": "mp4",
            "videoCodec": "h264",
            "audioCodec": "aac",
            "aspectRatio": "9:16",
            "resolution": "720p",
        },
    }
    validate_render_plan(plan)
    return plan


def required_visual_mode(
    episode: dict[str, Any],
    *,
    retry_after_identity_or_composition_drift: bool = False,
) -> VisualInputMode:
    value = episode["visualControl"]
    return select_visual_input(
        VisualControl(
            requires_exact_opening=value["requiresExactOpening"],
            requires_exact_ending=value["requiresExactEnding"],
            complex_subject_interaction=value["complexSubjectInteraction"],
            critical_prop_state=value["criticalPropState"],
        ),
        retry_after_identity_or_composition_drift=(
            retry_after_identity_or_composition_drift
        ),
    ).mode


def compile_keyframe_prompt(
    episode: dict[str, Any],
    *,
    frame_role: str,
) -> str:
    if frame_role not in {"first", "last"}:
        raise ContentValidationError("frame_role must be first or last.")
    beat = episode["beats"][0] if frame_role == "first" else episode["beats"][-1]
    context = episode["contextReads"]
    frame_name = "首帧" if frame_role == "first" else "尾帧"
    return (
        f"生成竖屏9:16视频的{frame_name}合成参考图。"
        "图1只约束固定人物身份，图2只约束固定猫咪身份，其余图片只约束画风。"
        "如果图1或图2是同一角色的正侧背三视图设定表，只生成一个对应角色，"
        "绝不能把不同视角当成多个角色。"
        f"场景地点：{context['locationId']}；天气：{context['weather']}；"
        f"人物服装版本：{context['wardrobeVersionIds']['person']}；"
        f"猫咪外观版本：{context['wardrobeVersionIds']['cat']}；"
        f"可见道具：{', '.join(context['propIds']) or '无'}。"
        f"画面动作与状态：{beat['visualAction']}。"
        "保持人物和猫咪的脸、体型、毛色、比例与参考图一致。"
        "不添加文字、字幕、对白气泡、水印、UI、额外人物或额外动物。"
    )


def _timeline_text(beat: dict[str, Any]) -> str:
    emotion = beat.get("emotion")
    return (
        f"{beat['purpose']}：{beat['visualAction']}"
        + (f"；情绪为{emotion}" if emotion else "")
        + "。保持人物、猫咪、服装、道具和画风连续。"
    )


def _audio_cues(episode: dict[str, Any]) -> list[dict[str, Any]]:
    cues: list[dict[str, Any]] = []
    for beat in episode["beats"]:
        if not beat["sfx"]:
            continue
        cues.append(
            {
                "startMs": beat["startMs"],
                "endMs": beat["endMs"],
                "category": (
                    "environment"
                    if beat["actors"] == ["environment"]
                    else "action_sfx"
                ),
                "precision": "suggested",
                "description": "、".join(beat["sfx"]),
            }
        )
    return cues


def _video_prompt(
    episode: dict[str, Any],
    mode: VisualInputMode,
    timeline_cues: list[dict[str, Any]],
) -> str:
    if mode is VisualInputMode.DIRECT_REFERENCES:
        visual_rule = (
            "输入图片按固定顺序：图1仅锁定人物身份，图2仅锁定猫咪身份，"
            "图3起仅锁定画风；图1和图2如为同一角色的三视图设定表，"
            "每张设定表只代表一个角色，绝不能生成分身；"
            "场景由文字建立，不照搬参考图背景。"
        )
    elif mode is VisualInputMode.GENERATED_FIRST_FRAME:
        visual_rule = "图1是已审核的合成首帧，从该画面自然连续运动。"
    else:
        visual_rule = (
            "图1是已审核的合成首帧，图2是已审核的合成尾帧；"
            "生成自然连续、物理合理的中间过程。"
        )
    timeline = " ".join(
        (
            f"[{cue['startMs'] / 1000:.1f}-{cue['endMs'] / 1000:.1f}秒]"
            f"{cue['promptText']}"
        )
        for cue in timeline_cues
    )
    forbidden = "、".join(episode["forbiddenContent"])
    return (
        f"生成一条连续的{episode['durationMs'] / 1000:g}秒竖屏生活流短视频。"
        f"{visual_rule}"
        f"主题：{episode['logline']}。"
        f"语义节奏如下，时间码只表示动作顺序与大致节奏，不要求硬切：{timeline}"
        "使用原生环境声和自然动作音效；无角色对白、无旁白、无歌词、无字幕。"
        f"禁止：{forbidden}、水印、UI、身份漂移、额外肢体。"
    )
