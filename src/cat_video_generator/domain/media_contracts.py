"""Seedance多模态输入的纯业务契约。

本模块只定义素材用途、供应商角色和组合约束，不包含Ark请求实现。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from .contract_base import StrictModel


class VideoInputMode(StrEnum):
    """Seedance输入模式；严格帧锚定与多模态参考保持互斥。"""

    MULTIMODAL_REFERENCE = "multimodal_reference"
    STRICT_FIRST_FRAME = "strict_first_frame"
    STRICT_FIRST_LAST = "strict_first_last"


class MediaModality(StrEnum):
    """Ark视频任务支持的三类参考媒体。"""

    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class MediaPurpose(StrEnum):
    """素材在本条视频中的业务用途，而不是供应商字段名。"""

    IDENTITY = "identity"
    STYLE = "style"
    ELEMENT = "element"
    SCENE = "scene"
    MOTION = "motion"
    ATMOSPHERE = "atmosphere"
    SEMANTIC_OPENING = "semantic_opening"
    SEMANTIC_ENDING = "semantic_ending"


class ProviderMediaRole(StrEnum):
    """Ark content数组中的媒体角色。"""

    REFERENCE_IMAGE = "reference_image"
    REFERENCE_VIDEO = "reference_video"
    REFERENCE_AUDIO = "reference_audio"
    FIRST_FRAME = "first_frame"
    LAST_FRAME = "last_frame"


class MediaBinding(StrictModel):
    """Prompt素材别名与Ark content项共用的唯一绑定记录。"""

    asset_id: UUID
    source_role: Annotated[
        str,
        Field(pattern=r"^[a-z][a-z0-9_]{1,63}$"),
    ]
    semantic_key: Annotated[
        str,
        Field(pattern=r"^[a-z][a-z0-9_]*:[a-z0-9][a-z0-9_-]{0,119}$"),
    ] | None = None
    modality: MediaModality
    purpose: MediaPurpose
    provider_role: ProviderMediaRole
    ordinal: Annotated[int, Field(ge=1, le=9)]
    prompt_alias: Annotated[
        str,
        Field(pattern=r"^@(图片|视频|音频)[1-9]$"),
    ]
    required: bool = True
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def validate_prompt_alias(self) -> MediaBinding:
        labels = {
            MediaModality.IMAGE: "图片",
            MediaModality.VIDEO: "视频",
            MediaModality.AUDIO: "音频",
        }
        expected = f"@{labels[self.modality]}{self.ordinal}"
        if self.prompt_alias != expected:
            raise ValueError(f"素材别名必须与模态和顺序一致，期望{expected}")
        return self


class VideoInputPlan(StrictModel):
    """一次Seedance任务最终使用的模型、规格与有序多模态输入。"""

    model: Annotated[str, Field(min_length=3, max_length=200)]
    input_mode: VideoInputMode
    resolution: Literal["480p", "720p"]
    # 完整Episode限制为8～15秒；这里放宽到4秒供multi_clip片段复用。
    duration_seconds: Annotated[int, Field(ge=4, le=15)]
    native_audio: bool = True
    prompt_dialect: Literal["seedance_skill_v1"] = "seedance_skill_v1"
    bindings: list[MediaBinding] = Field(default_factory=list, max_length=15)

    @model_validator(mode="after")
    def validate_bindings(self) -> VideoInputPlan:
        aliases = [item.prompt_alias for item in self.bindings]
        if len(set(aliases)) != len(aliases):
            raise ValueError("多模态素材别名不能重复")
        asset_ids = [item.asset_id for item in self.bindings]
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError("同一资产不能重复绑定到一个视频任务")

        by_modality = {
            modality: [item for item in self.bindings if item.modality is modality]
            for modality in MediaModality
        }
        limits = {
            MediaModality.IMAGE: 9,
            MediaModality.VIDEO: 3,
            MediaModality.AUDIO: 3,
        }
        for modality, items in by_modality.items():
            if len(items) > limits[modality]:
                raise ValueError(f"{modality.value}素材数量不能超过{limits[modality]}")
            if [item.ordinal for item in items] != list(range(1, len(items) + 1)):
                raise ValueError("同一模态的素材序号必须从1开始连续递增")

        if self.bindings and not (
            by_modality[MediaModality.IMAGE] or by_modality[MediaModality.VIDEO]
        ):
            raise ValueError("音频参考必须与图片或视频视觉输入共同使用")

        if self.input_mode is VideoInputMode.MULTIMODAL_REFERENCE:
            expected_roles = {
                MediaModality.IMAGE: ProviderMediaRole.REFERENCE_IMAGE,
                MediaModality.VIDEO: ProviderMediaRole.REFERENCE_VIDEO,
                MediaModality.AUDIO: ProviderMediaRole.REFERENCE_AUDIO,
            }
            if any(
                item.provider_role is not expected_roles[item.modality]
                for item in self.bindings
            ):
                raise ValueError("多模态参考模式只能使用reference媒体角色")
            return self

        expected = (
            [ProviderMediaRole.FIRST_FRAME]
            if self.input_mode is VideoInputMode.STRICT_FIRST_FRAME
            else [ProviderMediaRole.FIRST_FRAME, ProviderMediaRole.LAST_FRAME]
        )
        if [item.provider_role for item in self.bindings] != expected or any(
            item.modality is not MediaModality.IMAGE for item in self.bindings
        ):
            raise ValueError("严格帧模式只能按顺序发送对应的图片帧")
        return self
