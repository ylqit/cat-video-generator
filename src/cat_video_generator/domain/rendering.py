"""单镜头视频输入与项目级非破坏性时间轴。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from .contract_base import StrictModel


class RenderOperation(StrEnum):
    SHOT = "shot"
    EDIT = "edit"


class MediaModality(StrEnum):
    IMAGE = "image"
    VIDEO = "video"


class ProviderMediaRole(StrEnum):
    FIRST_FRAME = "first_frame"
    REFERENCE_VIDEO = "reference_video"
    REFERENCE_IMAGE = "reference_image"


class SequenceStatus(StrEnum):
    CONTENT_REVIEW = "content_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class MediaBinding(StrictModel):
    asset_id: UUID
    semantic_key: Annotated[str, Field(min_length=3, max_length=160)]
    modality: MediaModality
    provider_role: ProviderMediaRole
    ordinal: Annotated[int, Field(ge=1, le=4)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @property
    def prompt_alias(self) -> str:
        prefix = "@图片" if self.modality is MediaModality.IMAGE else "@视频"
        return f"{prefix}{self.ordinal}"


class VideoInputPlan(StrictModel):
    operation: RenderOperation
    resolution: Literal["480p", "720p"]
    duration_seconds: Annotated[int, Field(ge=4, le=15)]
    bindings: list[MediaBinding] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def validate_bindings(self) -> VideoInputPlan:
        for modality in MediaModality:
            ordinals = [item.ordinal for item in self.bindings if item.modality is modality]
            if ordinals and ordinals != list(range(1, len(ordinals) + 1)):
                raise ValueError(f"{modality.value}素材序号必须从1连续递增")
        if len({item.asset_id for item in self.bindings}) != len(self.bindings):
            raise ValueError("同一资产不能重复进入一个视频任务")
        if self.operation is RenderOperation.SHOT:
            if self.duration_seconds < 8:
                raise ValueError("镜头视频时长必须在8至15秒")
            first_frames = [
                item
                for item in self.bindings
                if item.provider_role is ProviderMediaRole.FIRST_FRAME
            ]
            if len(first_frames) > 1:
                raise ValueError("一次镜头生成最多使用一张first_frame")
            if first_frames and self.bindings[0] is not first_frames[0]:
                raise ValueError("first_frame必须是第一项素材")
            if any(item.modality is MediaModality.VIDEO for item in self.bindings):
                raise ValueError("初始镜头生成不接收前序完整视频")
        else:
            roles = [item.provider_role for item in self.bindings]
            if roles != [
                ProviderMediaRole.REFERENCE_VIDEO,
                ProviderMediaRole.REFERENCE_IMAGE,
                ProviderMediaRole.REFERENCE_IMAGE,
            ]:
                raise ValueError("区间编辑必须按@视频1、@图片1、@图片2绑定")
        return self


class SequenceClip(StrictModel):
    order: Annotated[int, Field(ge=1)]
    shot_card_id: UUID
    source_asset_id: UUID
    source_start_ms: Annotated[int, Field(ge=0)]
    source_end_ms: Annotated[int, Field(gt=0)]
    timeline_start_ms: Annotated[int, Field(ge=0)]
    timeline_end_ms: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def validate_interval(self) -> SequenceClip:
        if self.source_end_ms <= self.source_start_ms:
            raise ValueError("来源区间必须为正时长")
        if self.timeline_end_ms <= self.timeline_start_ms:
            raise ValueError("时间轴区间必须为正时长")
        if (self.source_end_ms - self.source_start_ms) != (
            self.timeline_end_ms - self.timeline_start_ms
        ):
            raise ValueError("来源区间与时间轴区间时长必须一致")
        return self


class ProjectSequencePlan(StrictModel):
    duration_ms: Annotated[int, Field(gt=0)]
    clips: list[SequenceClip] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_timeline(self) -> ProjectSequencePlan:
        if [clip.order for clip in self.clips] != list(range(1, len(self.clips) + 1)):
            raise ValueError("时间轴片段order必须连续")
        cursor = 0
        for clip in self.clips:
            if clip.timeline_start_ms != cursor:
                raise ValueError("时间轴必须连续且不能重叠")
            cursor = clip.timeline_end_ms
        if cursor != self.duration_ms:
            raise ValueError("时间轴末尾必须等于总时长")
        return self


@dataclass(frozen=True, slots=True)
class MediaSource:
    asset_id: UUID
    semantic_key: str
    media_type: str
    sha256: str
    metadata: dict[str, Any]


def build_shot_input_plan(
    *,
    resolution: str,
    duration_seconds: int,
    anchor: MediaSource | None,
    references: tuple[MediaSource, ...] = (),
) -> VideoInputPlan:
    if resolution not in {"480p", "720p"}:
        raise ValueError(f"不支持的视频分辨率{resolution}")
    maximum_references = 3 if anchor is not None else 4
    if len(references) > maximum_references:
        raise ValueError(f"当前模型输入档案最多允许{maximum_references}项附加参考素材")
    sources = (() if anchor is None else (anchor,)) + references
    if any(source.media_type != "image" for source in sources):
        raise ValueError("镜头生成的锚点与参考素材必须是图片")
    bindings: list[MediaBinding] = []
    for index, source in enumerate(sources, 1):
        bindings.append(
            MediaBinding(
                asset_id=source.asset_id,
                semantic_key=source.semantic_key,
                modality=MediaModality.IMAGE,
                provider_role=(
                    ProviderMediaRole.FIRST_FRAME
                    if anchor is not None and index == 1
                    else ProviderMediaRole.REFERENCE_IMAGE
                ),
                ordinal=index,
                sha256=source.sha256,
            )
        )
    return VideoInputPlan(
        operation=RenderOperation.SHOT,
        resolution=resolution,
        duration_seconds=duration_seconds,
        bindings=bindings,
    )


def build_edit_input_plan(
    *,
    resolution: str,
    duration_seconds: int,
    source_video: MediaSource,
    before_frame: MediaSource,
    after_frame: MediaSource,
) -> VideoInputPlan:
    sources = (source_video, before_frame, after_frame)
    if [item.media_type for item in sources] != ["video", "image", "image"]:
        raise ValueError("区间编辑需要一个视频和两张边界图")
    roles = (
        ProviderMediaRole.REFERENCE_VIDEO,
        ProviderMediaRole.REFERENCE_IMAGE,
        ProviderMediaRole.REFERENCE_IMAGE,
    )
    modalities = (MediaModality.VIDEO, MediaModality.IMAGE, MediaModality.IMAGE)
    ordinals = (1, 1, 2)
    return VideoInputPlan(
        operation=RenderOperation.EDIT,
        resolution=resolution,
        duration_seconds=duration_seconds,
        bindings=[
            MediaBinding(
                asset_id=source.asset_id,
                semantic_key=source.semantic_key,
                modality=modality,
                provider_role=role,
                ordinal=ordinal,
                sha256=source.sha256,
            )
            for source, modality, role, ordinal in zip(
                sources,
                modalities,
                roles,
                ordinals,
                strict=True,
            )
        ],
    )
