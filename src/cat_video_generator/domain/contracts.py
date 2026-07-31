"""三时段视频系统的当前业务契约。

本模块只描述导演和生产阶段共同理解的业务对象。供应商字段、数据库主键和
审核实现不进入契约，避免 Ark 输出格式与基础设施互相污染。
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, Field, model_validator

from .continuity import DominantView, VisibleWorldPlan
from .contract_base import StrictModel
from .media_contracts import (
    MediaBinding,
    MediaModality,
    MediaPurpose,
    ProviderMediaRole,
    VideoInputMode,
    VideoInputPlan,
)
from .provider_normalization import normalize_episode_draft_payload

__all__ = [
    "MediaBinding",
    "MediaModality",
    "MediaPurpose",
    "ProviderMediaRole",
    "StrictModel",
    "VideoInputMode",
    "VideoInputPlan",
]


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


class GenerationStrategy(StrEnum):
    """一条Episode的供应商任务组织方式。"""

    SINGLE_PASS = "single_pass"
    MULTI_CLIP = "multi_clip"


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
    actor_id: Annotated[
        str,
        Field(pattern=r"^(person|cat|guest|environment)$"),
    ]
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
    dominant_view: DominantView = DominantView.MIXED
    direction: Annotated[str, Field(min_length=4, max_length=180)]

    @model_validator(mode="after")
    def validate_actions(self) -> ShotPlan:
        if len(set(self.action_orders)) != len(self.action_orders):
            raise ValueError("同一镜头不能重复引用动作阶段")
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


class SceneProp(StrictModel):
    """本时段场景中会出现的家具或道具及其初始空间位置。

    镜头与首尾帧中命名的具体物体必须先在这里建账，避免画面中出现
    未申报物体或已申报物体无故消失。
    """

    name: Annotated[str, Field(min_length=2, max_length=30)]
    placement: Annotated[str, Field(min_length=2, max_length=80)]
    final_placement: Annotated[str, Field(min_length=2, max_length=80)] | None = None


class SegmentPlan(StrictModel):
    """显式multi_clip时的一个自然硬切片段。"""

    order: Annotated[int, Field(ge=1, le=2)]
    shot_order: Annotated[int, Field(ge=1, le=3)]
    action_orders: list[Annotated[int, Field(ge=1, le=4)]] = Field(
        min_length=1,
        max_length=4,
    )
    duration_seconds: Annotated[int, Field(ge=4, le=11)]
    requires_tail_link: bool = False


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
    event_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")
    location_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")
    main_event: Annotated[str, Field(min_length=6, max_length=260)]
    scene: Annotated[str, Field(min_length=6, max_length=320)]
    style_context: Literal["indoor", "outdoor"] = "outdoor"
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
    scene_inventory: list[SceneProp] = Field(default_factory=list, max_length=10)
    visible_world: VisibleWorldPlan
    # 两个关键物体需要同屏时应制作一张组合元素图，避免人物、猫咪和双画风之外
    # 再塞入多张弱相关参考，导致 Seedream 输入超过 3～5 张注意力预算。
    reference_semantic_keys: list[str] = Field(default_factory=list, max_length=1)
    generation_strategy: GenerationStrategy = GenerationStrategy.SINGLE_PASS
    segments: list[SegmentPlan] = Field(default_factory=list, max_length=2)

    @model_validator(mode="before")
    @classmethod
    def normalize_provider_shape(cls, value: Any) -> Any:
        """修正Ark JSON对象模式下可确定推导的字段位置，不改写剧情语义。"""

        return normalize_episode_draft_payload(value)

    def finalize(self, slot: Slot) -> EpisodePlan:
        """补齐不可由导演自由改写的协议字段。"""

        extra_roles = [
            key.split(":", 1)[0]
            for key in self.reference_semantic_keys
            if key.split(":", 1)[0] in {"element", "scene"}
        ]
        return EpisodePlan(
            slot=slot,
            cast=["person", "cat"],
            video_input_mode=VideoInputMode.MULTIMODAL_REFERENCE,
            required_reference_roles=[
                "person",
                "cat",
                "style",
                *dict.fromkeys(extra_roles),
            ],
            **self.model_dump(),
        )


class EpisodePlan(StrictModel):
    """一个时段导演输出的一条8至15秒紧凑脚本。"""

    slot: Slot
    title: Annotated[str, Field(min_length=2, max_length=80)]
    event_key: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$",
    )
    location_key: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$",
    )
    main_event: Annotated[str, Field(min_length=6, max_length=260)]
    scene: Annotated[str, Field(min_length=6, max_length=320)]
    style_context: Literal["indoor", "outdoor"] = "outdoor"
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
    scene_inventory: list[SceneProp] = Field(default_factory=list, max_length=10)
    visible_world: VisibleWorldPlan | None = None
    reference_semantic_keys: list[str] = Field(default_factory=list, max_length=1)
    generation_strategy: GenerationStrategy = GenerationStrategy.SINGLE_PASS
    segments: list[SegmentPlan] = Field(default_factory=list, max_length=2)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_visible_world(cls, value: Any) -> Any:
        """兼容历史Episode；新导演草稿仍必须显式声明每个动作主体。"""

        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        migrated["actions"] = [
            (
                {"actor_id": "person", **item}
                if isinstance(item, dict) and "actor_id" not in item
                else item
            )
            for item in migrated.get("actions", [])
        ]
        if migrated.get("visible_world") is None:
            props = [
                SceneProp.model_validate(item)
                for item in migrated.get("scene_inventory", [])
            ]
            migrated["visible_world"] = VisibleWorldPlan.from_scene_props(
                props
            ).model_dump(mode="json")
        return migrated

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
        if len(set(self.reference_semantic_keys)) != len(
            self.reference_semantic_keys
        ):
            raise ValueError("reference_semantic_keys不能重复")
        for semantic_key in self.reference_semantic_keys:
            prefix = semantic_key.split(":", 1)[0]
            if prefix not in {"element", "scene"}:
                raise ValueError("Episode附加参考只能使用element或scene语义键")
            if prefix not in self.required_reference_roles:
                raise ValueError("reference_semantic_keys必须对应required_reference_roles")
        orders = [stage.order for stage in self.actions]
        if orders != list(range(1, len(self.actions) + 1)):
            raise ValueError("动作阶段order必须从1开始连续递增")
        shot_orders = [shot.order for shot in self.shots]
        if shot_orders and shot_orders != list(range(1, len(self.shots) + 1)):
            raise ValueError("镜头order必须从1开始连续递增")
        referenced_actions = [
            action_order for shot in self.shots for action_order in shot.action_orders
        ]
        if referenced_actions and set(referenced_actions) != set(orders):
            raise ValueError("镜头计划必须完整且仅引用现有动作阶段")
        if len(referenced_actions) != len(set(referenced_actions)):
            raise ValueError("每个动作阶段只能归属于一个镜头")
        if len(set(self.shared_element_ids)) != len(self.shared_element_ids):
            raise ValueError("shared_element_ids不能重复")
        used_ids = [item.element_id for item in self.element_uses]
        if len(set(used_ids)) != len(used_ids):
            raise ValueError("element_uses不能重复引用同一元素")
        if used_ids and set(used_ids) != set(self.shared_element_ids):
            raise ValueError("element_uses必须完整对应shared_element_ids")
        prop_names = [prop.name for prop in self.scene_inventory]
        if len(set(prop_names)) != len(prop_names):
            raise ValueError("scene_inventory道具名称不能重复")
        if self.visible_world is None:
            raise ValueError("Episode必须包含VisibleWorldPlan")
        if self.generation_strategy is GenerationStrategy.SINGLE_PASS:
            if self.segments:
                raise ValueError("single_pass不能声明分段计划")
        else:
            if self.video_input_mode is not VideoInputMode.MULTIMODAL_REFERENCE:
                raise ValueError("multi_clip天然硬切片段必须使用multimodal_reference")
            if len(self.segments) != 2:
                raise ValueError("multi_clip必须且只能声明两个片段")
            if [item.order for item in self.segments] != [1, 2]:
                raise ValueError("multi_clip片段order必须为1、2")
            if sum(item.duration_seconds for item in self.segments) != (
                self.duration_seconds
            ):
                raise ValueError("multi_clip片段总时长必须等于Episode时长")
            if len(self.shots) != 2:
                raise ValueError("multi_clip只允许两个天然硬切镜头")
            if [item.shot_order for item in self.segments] != [
                item.order for item in self.shots
            ]:
                raise ValueError("multi_clip片段必须按顺序与两个镜头一一对应")
            segment_actions = [
                order for segment in self.segments for order in segment.action_orders
            ]
            if len(segment_actions) != len(set(segment_actions)):
                raise ValueError("multi_clip动作阶段不能跨片段重复")
            for segment, shot in zip(self.segments, self.shots, strict=True):
                if segment.action_orders != shot.action_orders:
                    raise ValueError("multi_clip片段动作必须与对应镜头完全一致")
            if self.segments[0].requires_tail_link:
                raise ValueError("只有第二个片段可以要求继承前段真实尾帧")
            covered = {
                order for segment in self.segments for order in segment.action_orders
            }
            if covered != set(orders):
                raise ValueError("multi_clip片段必须完整覆盖动作阶段")
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


class RecentContentSummary(StrictModel):
    """一个已批准或已交付Run用于冷却的结构化摘要。"""

    content_date: date
    event_keys: tuple[str, ...] = ()
    location_keys: tuple[str, ...] = ()
    element_semantic_keys: tuple[str, ...] = ()
    summary_text: str = Field(min_length=1, max_length=2000)
