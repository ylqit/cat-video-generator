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
from .contracts import EpisodePlan
from .visual_profiles import SeriesVisualProfile, StyleProfile


class VideoInputMode(StrEnum):
    """故事板参考与严格首尾帧两种互斥模式。"""

    STORYBOARD_REFERENCE = "storyboard_reference"
    STRICT_FIRST_LAST = "strict_first_last"


class MediaModality(StrEnum):
    IMAGE = "image"


class ProviderMediaRole(StrEnum):
    REFERENCE_IMAGE = "reference_image"
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
        return f"@图片{self.ordinal}"


class VideoInputPlan(StrictModel):
    """Seedance single-pass任务的最终业务输入。"""

    input_mode: VideoInputMode
    resolution: Literal["480p", "720p"]
    duration_seconds: Annotated[int, Field(ge=8, le=15)]
    bindings: list[MediaBinding] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def validate_bindings(self) -> VideoInputPlan:
        if len({item.asset_id for item in self.bindings}) != len(self.bindings):
            raise ValueError("同一资产不能重复进入一个视频任务")
        if [item.ordinal for item in self.bindings] != list(
            range(1, len(self.bindings) + 1)
        ):
            raise ValueError("故事板素材序号必须从1连续递增")

        roles = [item.provider_role for item in self.bindings]
        if self.input_mode is VideoInputMode.STRICT_FIRST_LAST:
            if roles != [ProviderMediaRole.FIRST_FRAME, ProviderMediaRole.LAST_FRAME]:
                raise ValueError("strict_first_last必须按顺序发送first_frame和last_frame")
        elif any(
            role
            not in {
                ProviderMediaRole.REFERENCE_IMAGE,
            }
            for role in roles
        ):
            raise ValueError("storyboard_reference只能使用reference媒体角色")
        return self


@dataclass(frozen=True, slots=True)
class MediaSource:
    """Application交给纯构建器的最小已批准资产投影。"""

    asset_id: UUID
    semantic_key: str
    media_type: str
    sha256: str
    metadata: dict[str, Any]


def storyboard_reference_keys(
    episode: EpisodePlan,
    style_profile: StyleProfile,
    series_profile: SeriesVisualProfile,
    *,
    look_key: str | None = None,
    explicit_episode_keys: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """返回故事板请求使用的确定性参考顺序。

    人物、猫咪和画风由系统 Canon 提供；导演只描述剧情逻辑实体，不能把场景
    文字伪装成数据库资产键。只有用户为当前 Episode 显式上传的素材才会追加。
    """

    first_view = (
        episode.script.shots[0].dominant_view.value
        if episode.script.shots
        else "front"
    )
    view = first_view if first_view in {"front", "side", "back"} else "front"
    contextual_style = (
        style_profile.indoor_reference_key
        if episode.script.style_context == "indoor"
        else style_profile.outdoor_reference_key
    )
    # 已批准定妆图已经融合人物面貌、短发、体型、本集服装和最终画风。
    # 它既是人物唯一基准，也是故事板的画风载体。此时再混入带有完整场景的
    # indoor/outdoor 画风图，会把参考图里的茶园、房间等内容误带入当前场景。
    # 只有没有定妆图的非生产预览/兼容调用才退回人物 Canon 与独立画风参考。
    if look_key is not None:
        base = (look_key, f"cat:{view}")
    else:
        base = (
            *series_profile.person_reference_keys,
            f"cat:{view}",
            contextual_style,
        )
    optional_key = explicit_episode_keys[-1] if explicit_episode_keys else None
    # 关键道具最多追加一张；不能为了凑素材数量重新加入弱相关或场景冲突图片。
    return tuple(dict.fromkeys((*base, *((optional_key,) if optional_key else ()))))


def build_video_input_plan(
    *,
    input_mode: VideoInputMode,
    resolution: str,
    duration_seconds: int,
    sources: tuple[MediaSource, ...],
) -> VideoInputPlan:
    """按语义优先级构建Prompt和Ark共用的唯一素材顺序。"""

    if resolution not in {"480p", "720p"}:
        raise ValueError(f"不支持的视频分辨率{resolution}")
    selected = _select_sources(input_mode, sources)
    if input_mode is VideoInputMode.STORYBOARD_REFERENCE and not 3 <= len(selected) <= 4:
        raise ValueError("storyboard_reference必须按顺序提供3至4张故事板面板")

    bindings: list[MediaBinding] = []
    for ordinal, source in enumerate(selected, 1):
        _validate_source(source)
        bindings.append(
            MediaBinding(
                asset_id=source.asset_id,
                semantic_key=source.semantic_key,
                modality=MediaModality.IMAGE,
                provider_role=_provider_role(input_mode, source.semantic_key),
                ordinal=ordinal,
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
    ordered = tuple(sorted(sources, key=lambda item: item.semantic_key))
    if any(not item.semantic_key.startswith("storyboard:panel-") for item in ordered):
        raise ValueError("Seedance生产输入只能使用已批准故事板面板")
    if mode is VideoInputMode.STRICT_FIRST_LAST:
        if len(ordered) != 2:
            raise ValueError("strict_first_last必须只发送故事板首尾两张面板")
        return ordered
    return ordered


def _provider_role(
    mode: VideoInputMode,
    semantic_key: str,
) -> ProviderMediaRole:
    if mode is VideoInputMode.STRICT_FIRST_LAST:
        return (
            ProviderMediaRole.FIRST_FRAME
            if semantic_key.endswith("01")
            else ProviderMediaRole.LAST_FRAME
        )
    return ProviderMediaRole.REFERENCE_IMAGE


def _validate_source(source: MediaSource) -> None:
    """使用已落盘QC元数据验证官方输入边界，不在Domain读取文件。"""

    if source.media_type != "image":
        raise ValueError(f"{source.semantic_key}必须是故事板图片")
    width = source.metadata.get("width")
    height = source.metadata.get("height")
    if width is None or height is None:
        raise ValueError(f"{source.semantic_key}缺少图片宽高QC元数据")
    ratio = float(width) / float(height)
    if not (300 <= int(width) <= 6000 and 300 <= int(height) <= 6000):
        raise ValueError(f"{source.semantic_key}图片边长必须在300至6000像素")
    if not 0.4 <= ratio <= 2.5:
        raise ValueError(f"{source.semantic_key}图片宽高比必须在0.4至2.5")
