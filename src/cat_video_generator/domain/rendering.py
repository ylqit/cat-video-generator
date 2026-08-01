"""Seedance输入契约、能力边界与确定性素材排序。

模型ID属于WorkflowStep，不在输入计划中重复保存。进入计划的素材均为必需输入；
Prompt别名由模态和序号计算，Ark Gateway按同一顺序创建请求内容。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from .contract_base import StrictModel


class VideoInputMode(StrEnum):
    """多模态参考与严格帧锚定三种互斥模式。"""

    MULTIMODAL_REFERENCE = "multimodal_reference"
    STRICT_FIRST_FRAME = "strict_first_frame"
    STRICT_FIRST_LAST = "strict_first_last"


class MediaModality(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class ProviderMediaRole(StrEnum):
    REFERENCE_IMAGE = "reference_image"
    REFERENCE_VIDEO = "reference_video"
    REFERENCE_AUDIO = "reference_audio"
    FIRST_FRAME = "first_frame"
    LAST_FRAME = "last_frame"


class MediaBinding(StrictModel):
    """一次视频调用中不可变的有序素材绑定。"""

    asset_id: UUID
    semantic_key: Annotated[
        str,
        Field(pattern=r"^[a-z][a-z0-9_]*:[a-z0-9][a-z0-9_-]{0,119}$"),
    ]
    modality: MediaModality
    provider_role: ProviderMediaRole
    ordinal: Annotated[int, Field(ge=1, le=9)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @property
    def prompt_alias(self) -> str:
        labels = {
            MediaModality.IMAGE: "图片",
            MediaModality.VIDEO: "视频",
            MediaModality.AUDIO: "音频",
        }
        return f"@{labels[self.modality]}{self.ordinal}"


class VideoInputPlan(StrictModel):
    """Seedance single-pass任务的最终业务输入。"""

    input_mode: VideoInputMode
    resolution: Literal["480p", "720p"]
    duration_seconds: Annotated[int, Field(ge=8, le=15)]
    bindings: list[MediaBinding] = Field(default_factory=list, max_length=15)

    @model_validator(mode="after")
    def validate_bindings(self) -> VideoInputPlan:
        if len({item.asset_id for item in self.bindings}) != len(self.bindings):
            raise ValueError("同一资产不能重复进入一个视频任务")
        by_modality = {
            modality: [item for item in self.bindings if item.modality is modality]
            for modality in MediaModality
        }
        for modality, limit in (
            (MediaModality.IMAGE, 9),
            (MediaModality.VIDEO, 3),
            (MediaModality.AUDIO, 3),
        ):
            items = by_modality[modality]
            if len(items) > limit:
                raise ValueError(f"{modality.value}素材数量不能超过{limit}")
            if [item.ordinal for item in items] != list(range(1, len(items) + 1)):
                raise ValueError("同一模态素材序号必须从1连续递增")
        if by_modality[MediaModality.AUDIO] and not (
            by_modality[MediaModality.IMAGE] or by_modality[MediaModality.VIDEO]
        ):
            raise ValueError("音频参考必须同时具有视觉输入")

        roles = [item.provider_role for item in self.bindings]
        if self.input_mode is VideoInputMode.STRICT_FIRST_FRAME:
            if roles != [ProviderMediaRole.FIRST_FRAME]:
                raise ValueError("strict_first_frame必须且只能发送一张first_frame")
        elif self.input_mode is VideoInputMode.STRICT_FIRST_LAST:
            if roles != [ProviderMediaRole.FIRST_FRAME, ProviderMediaRole.LAST_FRAME]:
                raise ValueError("strict_first_last必须按顺序发送first_frame和last_frame")
        elif any(
            role not in {
                ProviderMediaRole.REFERENCE_IMAGE,
                ProviderMediaRole.REFERENCE_VIDEO,
                ProviderMediaRole.REFERENCE_AUDIO,
            }
            for role in roles
        ):
            raise ValueError("multimodal_reference只能使用reference媒体角色")
        return self


@dataclass(frozen=True, slots=True)
class MediaSource:
    """Application交给纯构建器的最小已批准资产投影。"""

    asset_id: UUID
    semantic_key: str
    media_type: str
    sha256: str
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RenderingCapabilities:
    """当前产品启用的Seedance能力，不复制供应商完整产品目录。"""

    image_limit: int = 9
    video_limit: int = 3
    audio_limit: int = 3
    recommended_asset_limit: int = 5
    resolutions: tuple[str, ...] = ("480p", "720p")


DEFAULT_CAPABILITIES = RenderingCapabilities()


def build_video_input_plan(
    *,
    input_mode: VideoInputMode,
    resolution: str,
    duration_seconds: int,
    sources: tuple[MediaSource, ...],
    capabilities: RenderingCapabilities = DEFAULT_CAPABILITIES,
) -> VideoInputPlan:
    """按语义优先级构建Prompt和Ark共用的唯一素材顺序。"""

    if resolution not in capabilities.resolutions:
        raise ValueError(f"不支持的视频分辨率{resolution}")
    selected = _select_sources(input_mode, sources)
    if (
        input_mode is VideoInputMode.MULTIMODAL_REFERENCE
        and len(selected) > capabilities.recommended_asset_limit
    ):
        raise ValueError("多模态参考默认最多选择5项必要素材")

    counters = dict.fromkeys(MediaModality, 0)
    bindings: list[MediaBinding] = []
    for source in selected:
        modality = _modality(source.media_type)
        _validate_source(source, modality)
        counters[modality] += 1
        bindings.append(
            MediaBinding(
                asset_id=source.asset_id,
                semantic_key=source.semantic_key,
                modality=modality,
                provider_role=_provider_role(input_mode, source.semantic_key, modality),
                ordinal=counters[modality],
                sha256=source.sha256,
            )
        )
    return VideoInputPlan(
        input_mode=input_mode,
        resolution=resolution,
        duration_seconds=duration_seconds,
        bindings=bindings,
    )


def _select_sources(
    mode: VideoInputMode,
    sources: tuple[MediaSource, ...],
) -> tuple[MediaSource, ...]:
    if mode is VideoInputMode.STRICT_FIRST_FRAME:
        expected = ("frame:first",)
    elif mode is VideoInputMode.STRICT_FIRST_LAST:
        expected = ("frame:first", "frame:last")
    else:
        priorities = {
            "person": 10,
            "cat": 20,
            "style": 30,
            "element": 40,
            "scene": 50,
            "motion": 60,
            "audio": 70,
        }
        return tuple(
            sorted(
                sources,
                key=lambda item: (
                    priorities.get(item.semantic_key.split(":", 1)[0], 99),
                    item.semantic_key,
                ),
            )
        )
    actual = tuple(item.semantic_key for item in sources)
    if actual != expected:
        raise ValueError(f"{mode.value}要求素材{expected}，实际为{actual}")
    return sources


def _modality(media_type: str) -> MediaModality:
    try:
        return MediaModality(media_type)
    except ValueError as exc:
        raise ValueError(f"不支持的参考媒体类型{media_type}") from exc


def _provider_role(
    mode: VideoInputMode,
    semantic_key: str,
    modality: MediaModality,
) -> ProviderMediaRole:
    if mode is VideoInputMode.STRICT_FIRST_FRAME:
        return ProviderMediaRole.FIRST_FRAME
    if mode is VideoInputMode.STRICT_FIRST_LAST:
        return (
            ProviderMediaRole.FIRST_FRAME
            if semantic_key == "frame:first"
            else ProviderMediaRole.LAST_FRAME
        )
    return {
        MediaModality.IMAGE: ProviderMediaRole.REFERENCE_IMAGE,
        MediaModality.VIDEO: ProviderMediaRole.REFERENCE_VIDEO,
        MediaModality.AUDIO: ProviderMediaRole.REFERENCE_AUDIO,
    }[modality]


def _validate_source(source: MediaSource, modality: MediaModality) -> None:
    """使用已落盘QC元数据验证官方输入边界，不在Domain读取文件。"""

    if modality is MediaModality.IMAGE:
        width = source.metadata.get("width")
        height = source.metadata.get("height")
        if width is None or height is None:
            raise ValueError(f"{source.semantic_key}缺少图片宽高QC元数据")
        ratio = float(width) / float(height)
        if not (300 <= int(width) <= 6000 and 300 <= int(height) <= 6000):
            raise ValueError(f"{source.semantic_key}图片边长必须在300至6000像素")
        if not 0.4 <= ratio <= 2.5:
            raise ValueError(f"{source.semantic_key}图片宽高比必须在0.4至2.5")
        return
    duration = source.metadata.get("durationSeconds", source.metadata.get("duration"))
    if duration is None or not 2 <= float(duration) <= 15:
        raise ValueError(f"{source.semantic_key}参考媒体时长必须在2至15秒")
