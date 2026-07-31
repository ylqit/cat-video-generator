"""Seedance多模态输入计划的纯业务构建与能力校验。

本模块只处理模型能力、素材用途和确定性排序，不读取文件、不调用Ark，也不查询
数据库。Application层先把已批准资产转换为MediaSource，再调用这里生成唯一计划。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from .contracts import (
    EpisodePlan,
    MediaBinding,
    MediaModality,
    MediaPurpose,
    ProviderMediaRole,
    VideoInputMode,
    VideoInputPlan,
)


@dataclass(frozen=True, slots=True)
class MediaSource:
    """构建输入计划所需的最小资产投影。"""

    asset_id: UUID
    role: str
    media_type: str
    sha256: str
    metadata: dict[str, Any]
    semantic_key: str | None = None


@dataclass(frozen=True, slots=True)
class SeedanceCapabilities:
    """当前产品实际启用的Seedance能力边界。"""

    model: str
    image_limit: int = 9
    video_limit: int = 3
    audio_limit: int = 3
    business_asset_limit: int = 5
    min_duration_seconds: int = 4
    max_duration_seconds: int = 15
    resolutions: tuple[str, ...] = ("480p", "720p")


SEEDANCE_MINI_CAPABILITIES = SeedanceCapabilities(
    model="doubao-seedance-2-0-mini-260615"
)
SEEDANCE_FULL_CAPABILITIES = SeedanceCapabilities(
    model="doubao-seedance-2-0-260128"
)
SEEDANCE_CAPABILITY_PROFILES = {
    item.model: item
    for item in (
        SEEDANCE_MINI_CAPABILITIES,
        SEEDANCE_FULL_CAPABILITIES,
    )
}

_ROLE_PURPOSE = {
    "person": MediaPurpose.IDENTITY,
    "cat": MediaPurpose.IDENTITY,
    "style": MediaPurpose.STYLE,
    "element": MediaPurpose.ELEMENT,
    "scene": MediaPurpose.SCENE,
    "motion": MediaPurpose.MOTION,
    "atmosphere": MediaPurpose.ATMOSPHERE,
    "first_frame": MediaPurpose.SEMANTIC_OPENING,
    "last_frame": MediaPurpose.SEMANTIC_ENDING,
}
_ROLE_PRIORITY = {
    "person": 10,
    "cat": 20,
    "first_frame": 30,
    "last_frame": 40,
    "style": 50,
    "element": 60,
    "scene": 70,
    "motion": 80,
    "atmosphere": 90,
}
_MEDIA_TYPE = {
    "image": MediaModality.IMAGE,
    "video": MediaModality.VIDEO,
    "audio": MediaModality.AUDIO,
}
_REFERENCE_ROLE = {
    MediaModality.IMAGE: ProviderMediaRole.REFERENCE_IMAGE,
    MediaModality.VIDEO: ProviderMediaRole.REFERENCE_VIDEO,
    MediaModality.AUDIO: ProviderMediaRole.REFERENCE_AUDIO,
}
_ALIAS_LABEL = {
    MediaModality.IMAGE: "图片",
    MediaModality.VIDEO: "视频",
    MediaModality.AUDIO: "音频",
}


def build_video_input_plan(
    episode: EpisodePlan,
    *,
    model: str,
    resolution: str,
    sources: tuple[MediaSource, ...],
    duration_seconds: int | None = None,
    capabilities: SeedanceCapabilities | None = None,
) -> VideoInputPlan:
    """从已批准素材构建Ark请求与Prompt共用的有序绑定。

    严格首尾帧模式不能混入参考媒体；多模态模式按业务重要性稳定排序。任何
    必需输入无法表达时都在收费任务意图创建前失败。
    """

    selected_capabilities = (
        capabilities
        if capabilities is not None
        else SEEDANCE_CAPABILITY_PROFILES.get(model)
    )
    if selected_capabilities is None:
        raise ValueError(f"视频模型{model}没有已登记的Seedance能力档案")
    if model != selected_capabilities.model:
        raise ValueError(
            f"当前能力档案只允许{selected_capabilities.model}，实际为{model}"
        )
    if resolution not in selected_capabilities.resolutions:
        raise ValueError(f"Seedance模型{model}不支持分辨率{resolution}")
    selected_duration = (
        episode.duration_seconds if duration_seconds is None else duration_seconds
    )
    if not (
        selected_capabilities.min_duration_seconds
        <= selected_duration
        <= selected_capabilities.max_duration_seconds
    ):
        raise ValueError("Seedance任务时长必须在4至15秒")

    selected = _select_sources(episode.video_input_mode, sources)
    if (
        episode.video_input_mode is VideoInputMode.MULTIMODAL_REFERENCE
        and len(selected) > selected_capabilities.business_asset_limit
    ):
        raise ValueError("多模态参考超过5项重要素材，必须删减弱相关输入或重新规划")

    counters = dict.fromkeys(MediaModality, 0)
    bindings: list[MediaBinding] = []
    for source in selected:
        _validate_source_metadata(source)
        try:
            modality = _MEDIA_TYPE[source.media_type]
            purpose = _ROLE_PURPOSE[source.role]
        except KeyError as exc:
            raise ValueError(
                f"不支持的素材角色或媒体类型: {source.role}/{source.media_type}"
            ) from exc
        counters[modality] += 1
        provider_role = _provider_role(
            episode.video_input_mode,
            source.role,
            modality,
        )
        bindings.append(
            MediaBinding(
                asset_id=source.asset_id,
                source_role=source.role,
                semantic_key=source.semantic_key,
                modality=modality,
                purpose=purpose,
                provider_role=provider_role,
                ordinal=counters[modality],
                prompt_alias=f"@{_ALIAS_LABEL[modality]}{counters[modality]}",
                required=True,
                sha256=source.sha256,
            )
        )

    _validate_counts(counters, selected_capabilities)
    if bindings and all(item.modality is MediaModality.AUDIO for item in bindings):
        raise ValueError("Seedance不支持纯音频或没有视觉素材的文本加音频输入")
    return VideoInputPlan(
        model=model,
        input_mode=episode.video_input_mode,
        resolution=resolution,
        duration_seconds=selected_duration,
        bindings=bindings,
    )


def _select_sources(
    mode: VideoInputMode,
    sources: tuple[MediaSource, ...],
) -> tuple[MediaSource, ...]:
    if mode is VideoInputMode.STRICT_FIRST_FRAME:
        expected = ("first_frame",)
    elif mode is VideoInputMode.STRICT_FIRST_LAST:
        expected = ("first_frame", "last_frame")
    else:
        return tuple(
            sorted(
                sources,
                key=lambda item: _ROLE_PRIORITY.get(item.role, 999),
            )
        )

    actual = tuple(item.role for item in sources)
    if actual != expected:
        raise ValueError(f"{mode.value}只允许{expected}，实际输入角色为{actual}")
    return sources


def _provider_role(
    mode: VideoInputMode,
    source_role: str,
    modality: MediaModality,
) -> ProviderMediaRole:
    if mode is VideoInputMode.STRICT_FIRST_FRAME:
        return ProviderMediaRole.FIRST_FRAME
    if mode is VideoInputMode.STRICT_FIRST_LAST:
        return (
            ProviderMediaRole.FIRST_FRAME
            if source_role == "first_frame"
            else ProviderMediaRole.LAST_FRAME
        )
    return _REFERENCE_ROLE[modality]


def _validate_counts(
    counters: dict[MediaModality, int],
    capabilities: SeedanceCapabilities,
) -> None:
    limits = {
        MediaModality.IMAGE: capabilities.image_limit,
        MediaModality.VIDEO: capabilities.video_limit,
        MediaModality.AUDIO: capabilities.audio_limit,
    }
    for modality, count in counters.items():
        if count > limits[modality]:
            raise ValueError(
                f"{modality.value}输入数量{count}超过上限{limits[modality]}"
            )


def _validate_source_metadata(source: MediaSource) -> None:
    """使用已落盘QC元数据检查官方输入边界，不在领域层读取文件。"""

    if source.media_type == "image":
        width = source.metadata.get("width")
        height = source.metadata.get("height")
        if width is None or height is None:
            raise ValueError(f"{source.role}图片缺少宽高QC元数据")
        ratio = float(width) / float(height)
        if not (300 <= int(width) <= 6000 and 300 <= int(height) <= 6000):
            raise ValueError(f"{source.role}图片边长必须在300至6000像素")
        if not 0.4 <= ratio <= 2.5:
            raise ValueError(f"{source.role}图片宽高比必须在0.4至2.5")
        return
    duration = source.metadata.get("durationSeconds")
    if duration is None:
        duration = source.metadata.get("duration")
    if duration is None or not 2 <= float(duration) <= 15:
        raise ValueError(f"{source.role}参考{source.media_type}时长必须在2至15秒")
