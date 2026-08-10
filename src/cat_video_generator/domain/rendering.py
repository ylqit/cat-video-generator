"""长短视频渲染计划、素材绑定与模型能力。

Episode只保存创作脚本；本模块按总时长确定性生成收费区段。首段只接收开场锚点，
后续区段只接收上一版视频并使用官方延展语义，避免多套输入模式并存。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from .contract_base import StrictModel
from .contracts import EpisodePlan


class RenderMode(StrEnum):
    SINGLE_PASS = "single_pass"
    EXTENDED = "extended"


class RenderOperation(StrEnum):
    INITIAL = "initial"
    EXTEND = "extend"


class MediaModality(StrEnum):
    IMAGE = "image"
    VIDEO = "video"


class ProviderMediaRole(StrEnum):
    FIRST_FRAME = "first_frame"
    REFERENCE_VIDEO = "reference_video"


class RenderSection(StrictModel):
    order: Annotated[int, Field(ge=1, le=3)]
    duration_seconds: Annotated[int, Field(ge=8, le=15)]
    shot_orders: list[Annotated[int, Field(ge=1, le=3)]] = Field(min_length=1, max_length=3)


class RenderPlan(StrictModel):
    """一条Episode的确定性供应商任务划分。"""

    mode: RenderMode
    total_duration_seconds: Annotated[int, Field(ge=8, le=45)]
    sections: list[RenderSection] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_sections(self) -> RenderPlan:
        if [item.order for item in self.sections] != list(range(1, len(self.sections) + 1)):
            raise ValueError("渲染区段order必须从1连续递增")
        if sum(item.duration_seconds for item in self.sections) != self.total_duration_seconds:
            raise ValueError("渲染区段时长之和必须等于Episode总时长")
        shot_orders = [order for section in self.sections for order in section.shot_orders]
        if shot_orders != sorted(shot_orders) or len(shot_orders) != len(set(shot_orders)):
            raise ValueError("镜头必须按顺序且只能属于一个渲染区段")
        if self.mode is RenderMode.SINGLE_PASS and len(self.sections) != 1:
            raise ValueError("single_pass必须只有一个区段")
        if self.mode is RenderMode.EXTENDED and len(self.sections) not in {2, 3}:
            raise ValueError("extended必须包含两个或三个区段")
        return self


class MediaBinding(StrictModel):
    """一次Ark视频请求中按顺序持久化的必要素材。"""

    asset_id: UUID
    semantic_key: Annotated[str, Field(min_length=3, max_length=160)]
    modality: MediaModality
    provider_role: ProviderMediaRole
    ordinal: Annotated[int, Field(ge=1, le=3)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @property
    def prompt_alias(self) -> str:
        prefix = "@图片" if self.modality is MediaModality.IMAGE else "@视频"
        return f"{prefix}{self.ordinal}"


class VideoInputPlan(StrictModel):
    """单个Seedance任务的最终输入，不重复保存模型和operationKey。"""

    operation: RenderOperation
    resolution: Literal["480p", "720p"]
    duration_seconds: Annotated[int, Field(ge=8, le=15)]
    bindings: list[MediaBinding] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_bindings(self) -> VideoInputPlan:
        if [item.ordinal for item in self.bindings] != list(range(1, len(self.bindings) + 1)):
            raise ValueError("视频输入素材序号必须从1连续递增")
        if len({item.asset_id for item in self.bindings}) != len(self.bindings):
            raise ValueError("同一资产不能重复进入一个视频任务")
        if self.operation is RenderOperation.INITIAL:
            if (
                len(self.bindings) != 1
                or self.bindings[0].provider_role is not ProviderMediaRole.FIRST_FRAME
            ):
                raise ValueError("初始视频任务只能接收一张first_frame开场锚点")
        else:
            if (
                len(self.bindings) != 1
                or self.bindings[0].provider_role is not ProviderMediaRole.REFERENCE_VIDEO
            ):
                raise ValueError("延展任务只能接收上一版reference_video")
        return self


@dataclass(frozen=True, slots=True)
class MediaSource:
    asset_id: UUID
    semantic_key: str
    media_type: str
    sha256: str
    metadata: dict[str, Any]


_EXTENSION_MODELS = frozenset({"doubao-seedance-2-0-260128"})


def supports_video_extension(model: str) -> bool:
    """完整Seedance 2.0支持视频延展；Mini不会被自动升级。"""

    return model in _EXTENSION_MODELS


def build_render_plan(episode: EpisodePlan) -> RenderPlan:
    """按总时长和连续镜头确定性分配1至3个任务区段。"""

    total = episode.duration_seconds
    section_count = 1 if total <= 15 else 2 if total <= 30 else 3
    if len(episode.script.shots) < section_count:
        raise ValueError("长视频没有足够镜头划分连续延展区段")

    durations = _balanced_durations(total, section_count)
    shot_groups = _balanced_shot_groups(
        tuple(item.order for item in episode.script.shots),
        section_count,
    )
    return RenderPlan(
        mode=RenderMode.SINGLE_PASS if section_count == 1 else RenderMode.EXTENDED,
        total_duration_seconds=total,
        sections=[
            RenderSection(order=index, duration_seconds=duration, shot_orders=list(shots))
            for index, (duration, shots) in enumerate(zip(durations, shot_groups, strict=True), 1)
        ],
    )


def build_video_input_plan(
    *,
    operation: RenderOperation,
    resolution: str,
    duration_seconds: int,
    source: MediaSource,
) -> VideoInputPlan:
    if resolution not in {"480p", "720p"}:
        raise ValueError(f"不支持的视频分辨率{resolution}")
    expected_type = "image" if operation is RenderOperation.INITIAL else "video"
    if source.media_type != expected_type:
        raise ValueError(f"{operation.value}任务必须使用{expected_type}素材")
    role = (
        ProviderMediaRole.FIRST_FRAME
        if operation is RenderOperation.INITIAL
        else ProviderMediaRole.REFERENCE_VIDEO
    )
    return VideoInputPlan(
        operation=operation,
        resolution=resolution,
        duration_seconds=duration_seconds,
        bindings=[
            MediaBinding(
                asset_id=source.asset_id,
                semantic_key=source.semantic_key,
                modality=MediaModality.IMAGE
                if source.media_type == "image"
                else MediaModality.VIDEO,
                provider_role=role,
                ordinal=1,
                sha256=source.sha256,
            )
        ],
    )


def _balanced_durations(total: int, count: int) -> tuple[int, ...]:
    base, remainder = divmod(total, count)
    values = tuple(base + (1 if index < remainder else 0) for index in range(count))
    if any(value < 8 or value > 15 for value in values):
        raise ValueError("总时长无法划分为8至15秒的连续区段")
    return values


def _balanced_shot_groups(shots: tuple[int, ...], count: int) -> tuple[tuple[int, ...], ...]:
    base, remainder = divmod(len(shots), count)
    groups: list[tuple[int, ...]] = []
    cursor = 0
    for index in range(count):
        size = base + (1 if index < remainder else 0)
        groups.append(shots[cursor : cursor + size])
        cursor += size
    return tuple(groups)
