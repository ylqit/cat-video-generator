"""三时段视频系统的当前业务契约。

本模块只描述导演和生产阶段共同理解的业务对象。供应商字段、数据库主键和
审核实现不进入契约，避免 Ark 输出格式与基础设施互相污染。
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class Slot(StrEnum):
    """一天中固定的三个内容顺序，而不是定时执行点。"""

    MORNING = "morning"
    NOON = "noon"
    EVENING = "evening"

    @property
    def sort_order(self) -> int:
        return {
            Slot.MORNING: 1,
            Slot.NOON: 2,
            Slot.EVENING: 3,
        }[self]


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


class AppearanceContinuity(StrEnum):
    """本时段外观相对前一时段的连续关系。"""

    CONTINUE = "continue"
    CHANGED = "changed"


class CameraMove(StrEnum):
    """一个镜头只允许选择一种主要运镜。"""

    FIXED = "fixed"
    FOLLOW = "follow"
    PUSH = "push"
    PULL = "pull"
    PAN = "pan"
    TRACK = "track"


class StrictModel(BaseModel):
    """禁止静默接收导演临时发明的字段，避免契约再次失控。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )


class AppearancePlan(StrictModel):
    """单个时段实际可见的服饰、鞋帽、配饰与随身物品。"""

    description: Annotated[str, Field(min_length=4, max_length=300)]
    continuity: AppearanceContinuity | None = None
    changes_from_previous: list[Annotated[str, Field(min_length=2, max_length=100)]] = (
        Field(default_factory=list, max_length=8)
    )
    change_reason: Annotated[str, Field(min_length=4, max_length=200)] | None = None

    @model_validator(mode="before")
    @classmethod
    def infer_legacy_continuity(cls, value: Any) -> Any:
        """旧脚本没有continuity时，根据显式变化列表补齐。"""

        if not isinstance(value, dict) or value.get("continuity") is not None:
            return value
        migrated = dict(value)
        migrated["continuity"] = (
            AppearanceContinuity.CHANGED
            if migrated.get("changes_from_previous")
            else AppearanceContinuity.CONTINUE
        )
        return migrated

    @model_validator(mode="after")
    def validate_change_reason(self) -> AppearancePlan:
        changed = self.continuity is AppearanceContinuity.CHANGED
        if changed and (not self.changes_from_previous or not self.change_reason):
            raise ValueError("服饰或随身物品发生变化时必须说明情景原因")
        if not changed and (self.changes_from_previous or self.change_reason):
            raise ValueError("continue外观不能同时声明变化内容或变化原因")
        return self


class ActionStage(StrictModel):
    """一个连续动作阶段；不是独立视频，也不是精确逐帧剪辑点。"""

    order: Annotated[int, Field(ge=1, le=4)]
    action: Annotated[str, Field(min_length=6, max_length=280)]
    visible_result: Annotated[str, Field(min_length=4, max_length=180)]


class ShotPlan(StrictModel):
    """导演给出的轻量镜头计划；不承担精确秒级剪辑。"""

    order: Annotated[int, Field(ge=1, le=3)]
    action_orders: list[Annotated[int, Field(ge=1, le=4)]] = Field(
        min_length=1,
        max_length=4,
    )
    framing: Annotated[str, Field(min_length=2, max_length=80)]
    camera_move: CameraMove
    direction: Annotated[str, Field(min_length=4, max_length=180)]

    @model_validator(mode="after")
    def validate_actions(self) -> ShotPlan:
        if len(set(self.action_orders)) != len(self.action_orders):
            raise ValueError("同一镜头不能重复引用动作阶段")
        return self


class MediaBinding(StrictModel):
    """Prompt素材别名与Ark content项共用的唯一绑定记录。"""

    asset_id: UUID
    source_role: Annotated[
        str,
        Field(pattern=r"^[a-z][a-z0-9_]{1,63}$"),
    ]
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
    duration_seconds: Annotated[int, Field(ge=8, le=15)]
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

        if self.input_mode is VideoInputMode.STRICT_FIRST_FRAME:
            expected = [ProviderMediaRole.FIRST_FRAME]
        else:
            expected = [
                ProviderMediaRole.FIRST_FRAME,
                ProviderMediaRole.LAST_FRAME,
            ]
        if [item.provider_role for item in self.bindings] != expected or any(
            item.modality is not MediaModality.IMAGE for item in self.bindings
        ):
            raise ValueError("严格帧模式只能按顺序发送对应的图片帧")
        return self


class CriticalRelation(StrictModel):
    """剧情成败依赖的物理关系及其明确起止状态。"""

    subject: Annotated[str, Field(min_length=1, max_length=80)]
    relation: Literal["count", "support", "containment", "boundary", "handoff"]
    initial_state: Annotated[str, Field(min_length=2, max_length=220)]
    final_state: Annotated[str, Field(min_length=2, max_length=220)]

    @model_validator(mode="before")
    @classmethod
    def read_legacy_expected(cls, value: Any) -> Any:
        """旧归档只有 expected；读取时映射为不发生变化的起止关系。"""

        if not isinstance(value, dict) or "expected" not in value:
            return value
        migrated = dict(value)
        expected = migrated.pop("expected")
        migrated.setdefault("initial_state", expected)
        migrated.setdefault("final_state", expected)
        return migrated


class SharedElement(StrictModel):
    """一天内确实需要重复出现的元素，不用于收集普通背景物体。"""

    element_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]
    description: Annotated[str, Field(min_length=4, max_length=240)]
    slots: list[Slot] = Field(min_length=2, max_length=3)

    @model_validator(mode="after")
    def validate_unique_slots(self) -> SharedElement:
        if len(set(self.slots)) != len(self.slots):
            raise ValueError("共享元素的slots不能重复")
        return self


class ElementUse(StrictModel):
    """共享元素在某个时段的实际用途和状态，而不是仅有一个字符串ID。"""

    element_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]
    purpose: Annotated[str, Field(min_length=3, max_length=160)]
    initial_state: Annotated[str, Field(min_length=2, max_length=180)]
    final_state: Annotated[str, Field(min_length=2, max_length=180)]


class SlotBrief(StrictModel):
    """总导演交给单个时段导演的边界，不包含具体分镜。"""

    slot: Slot
    narrative_purpose: Annotated[str, Field(min_length=6, max_length=220)]
    scene_direction: Annotated[str, Field(min_length=6, max_length=260)]
    event_direction: Annotated[str, Field(min_length=6, max_length=260)]
    appearance_intent: Annotated[str, Field(min_length=4, max_length=220)]
    continuity_requirements: list[
        Annotated[str, Field(min_length=3, max_length=160)]
    ] = Field(default_factory=list, max_length=6)


class DayBrief(StrictModel):
    """总导演的一次输出：固定全天方向，但不替时段导演写具体镜头。"""

    content_date: date
    theme: Annotated[str, Field(min_length=4, max_length=160)]
    day_context: Annotated[str, Field(min_length=8, max_length=500)]
    shared_elements: list[SharedElement] = Field(default_factory=list, max_length=8)
    slots: list[SlotBrief] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_slots(self) -> DayBrief:
        if [item.slot for item in self.slots] != list(Slot):
            raise ValueError("DayBrief.slots必须严格按morning、noon、evening排序")
        return self


class EpisodeDirectorDraft(StrictModel):
    """时段导演只负责创意字段；固定ID和输入顺序由本地组装。"""

    title: Annotated[str, Field(min_length=2, max_length=80)]
    main_event: Annotated[str, Field(min_length=6, max_length=260)]
    scene: Annotated[str, Field(min_length=6, max_length=320)]
    appearance: AppearancePlan
    actions: list[ActionStage] = Field(min_length=2, max_length=4)
    shots: list[ShotPlan] = Field(min_length=1, max_length=3)
    ending: Annotated[str, Field(min_length=6, max_length=220)]
    duration_seconds: Annotated[int, Field(ge=8, le=15)]
    shared_element_ids: list[str] = Field(default_factory=list, max_length=6)
    element_uses: list[ElementUse] = Field(default_factory=list, max_length=6)
    critical_relations: list[CriticalRelation] = Field(
        default_factory=list,
        max_length=6,
    )

    def finalize(self, slot: Slot) -> EpisodePlan:
        """补齐不可由导演自由改写的协议字段。"""

        return EpisodePlan(
            slot=slot,
            cast=["person", "cat"],
            video_input_mode=VideoInputMode.MULTIMODAL_REFERENCE,
            required_reference_roles=["person", "cat", "style"],
            **self.model_dump(),
        )


class EpisodePlan(StrictModel):
    """一个时段导演输出的一条8至15秒紧凑脚本。"""

    slot: Slot
    title: Annotated[str, Field(min_length=2, max_length=80)]
    main_event: Annotated[str, Field(min_length=6, max_length=260)]
    scene: Annotated[str, Field(min_length=6, max_length=320)]
    cast: list[Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]] = Field(
        min_length=2, max_length=3
    )
    appearance: AppearancePlan
    actions: list[ActionStage] = Field(min_length=2, max_length=4)
    shots: list[ShotPlan] = Field(default_factory=list, max_length=3)
    ending: Annotated[str, Field(min_length=6, max_length=220)]
    duration_seconds: Annotated[int, Field(ge=8, le=15)]
    video_input_mode: VideoInputMode = Field(
        validation_alias=AliasChoices("video_input_mode", "visual_strategy")
    )
    required_reference_roles: list[
        Literal[
            "person",
            "cat",
            "style",
            "element",
            "scene",
            "motion",
            "atmosphere",
        ]
    ] = Field(default_factory=lambda: ["person", "cat", "style"], max_length=7)
    shared_element_ids: list[str] = Field(default_factory=list, max_length=6)
    element_uses: list[ElementUse] = Field(default_factory=list, max_length=6)
    critical_relations: list[CriticalRelation] = Field(
        default_factory=list,
        max_length=6,
    )

    @model_validator(mode="after")
    def validate_episode(self) -> EpisodePlan:
        if self.cast.count("person") != 1 or self.cast.count("cat") != 1:
            raise ValueError("每条Episode必须恰好包含一个person和一个cat")
        if len(set(self.cast)) != len(self.cast):
            raise ValueError("cast不能包含重复角色")
        if self.required_reference_roles[:3] != ["person", "cat", "style"]:
            raise ValueError("参考素材必须按person、cat、style固定顺序开始")
        if len(set(self.required_reference_roles)) != len(
            self.required_reference_roles
        ):
            raise ValueError("required_reference_roles不能重复")
        orders = [stage.order for stage in self.actions]
        if orders != list(range(1, len(self.actions) + 1)):
            raise ValueError("动作阶段order必须从1开始连续递增")
        shot_orders = [shot.order for shot in self.shots]
        if shot_orders and shot_orders != list(range(1, len(self.shots) + 1)):
            raise ValueError("镜头order必须从1开始连续递增")
        referenced_actions = {
            action_order for shot in self.shots for action_order in shot.action_orders
        }
        if referenced_actions and referenced_actions != set(orders):
            raise ValueError("镜头计划必须完整且仅引用现有动作阶段")
        if len(set(self.shared_element_ids)) != len(self.shared_element_ids):
            raise ValueError("shared_element_ids不能重复")
        used_ids = [item.element_id for item in self.element_uses]
        if len(set(used_ids)) != len(used_ids):
            raise ValueError("element_uses不能重复引用同一元素")
        if used_ids and set(used_ids) != set(self.shared_element_ids):
            raise ValueError("element_uses必须完整对应shared_element_ids")
        return self


class DailyProductionPlan(StrictModel):
    """总导演和三个时段导演依次完成后组装的全天生产方案。"""

    content_date: date
    theme: Annotated[str, Field(min_length=4, max_length=160)]
    day_context: Annotated[str, Field(min_length=8, max_length=500)]
    shared_elements: list[SharedElement] = Field(default_factory=list, max_length=8)
    episodes: list[EpisodePlan] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_day(self) -> DailyProductionPlan:
        expected = [Slot.MORNING, Slot.NOON, Slot.EVENING]
        actual = [episode.slot for episode in self.episodes]
        if actual != expected:
            raise ValueError("episodes必须严格按morning、noon、evening排序")

        if self.episodes[0].appearance.continuity is AppearanceContinuity.CHANGED:
            raise ValueError("morning没有前一时段，不能声明changes_from_previous")

        defined = {
            element.element_id: set(element.slots) for element in self.shared_elements
        }
        for episode in self.episodes:
            for element_id in episode.shared_element_ids:
                if element_id not in defined:
                    raise ValueError(f"Episode引用了未定义共享元素: {element_id}")
                if episode.slot not in defined[element_id]:
                    raise ValueError(
                        f"共享元素{element_id}没有声明用于{episode.slot.value}"
                    )
        return self
