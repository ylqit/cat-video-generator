"""导演、规划和生产共同使用的唯一业务契约。

导演直接输出 ``EpisodeScript``；本地只把固定时段包装成 ``EpisodePlan``。
数据库关系字段和供应商请求字段不进入脚本，避免同一事实出现多份表达。
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .continuity import (
    DominantView,
    SceneContinuity,
)
from .contract_base import StrictModel


class Slot(StrEnum):
    """固定内容顺序，不表示命令必须在对应时钟执行。"""

    MORNING = "morning"
    NOON = "noon"
    EVENING = "evening"

    @property
    def sort_order(self) -> int:
        return {Slot.MORNING: 1, Slot.NOON: 2, Slot.EVENING: 3}[self]


class CameraMove(StrEnum):
    FIXED = "fixed"
    FOLLOW = "follow"
    PUSH = "push"
    PULL = "pull"
    PAN = "pan"
    TRACK = "track"


class AppearancePlan(StrictModel):
    """本时段实际可见服饰；身份Canon不包含衣服、鞋帽和背包。"""

    description: Annotated[str, Field(min_length=4, max_length=300)]
    changes_from_previous: list[Annotated[str, Field(min_length=2, max_length=100)]] = Field(
        default_factory=list, max_length=8
    )
    change_reason: Annotated[str, Field(min_length=4, max_length=200)] | None = None

    @model_validator(mode="after")
    def validate_change(self) -> AppearancePlan:
        if self.changes_from_previous and not self.change_reason:
            raise ValueError("外观变化必须列出变化内容和剧情原因")
        if not self.changes_from_previous and self.change_reason:
            raise ValueError("没有外观变化时不应单独声明变化原因")
        return self


class ActionStage(StrictModel):
    """属于同一主事件的一个连续动作阶段。"""

    order: Annotated[int, Field(ge=1, le=4)]
    actor_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]
    action: Annotated[str, Field(min_length=6, max_length=280)]
    visible_result: Annotated[str, Field(min_length=4, max_length=180)]


class ShotPlan(StrictModel):
    """轻量镜头意图；状态变化只保存在ActionStage。"""

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
    def validate_unique_actions(self) -> ShotPlan:
        if len(self.action_orders) != len(set(self.action_orders)):
            raise ValueError("同一镜头不能重复引用动作")
        return self


class SharedElement(StrictModel):
    """DayBrief中跨时段复用的逻辑实体，不等同于数据库参考资产。"""

    entity_key: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]
    description: Annotated[str, Field(min_length=4, max_length=240)]
    slots: list[Slot] = Field(min_length=2, max_length=3)

    @model_validator(mode="after")
    def validate_slots(self) -> SharedElement:
        if len(self.slots) != len(set(self.slots)):
            raise ValueError("共享元素时段不能重复")
        if self.entity_key in {"person", "cat"}:
            raise ValueError("固定人物和猫咪不应重复声明为共享元素")
        return self


class SlotBrief(StrictModel):
    """总导演交给单个时段导演的创作边界。"""

    slot: Slot
    narrative_purpose: Annotated[str, Field(min_length=6, max_length=220)]
    scene_direction: Annotated[str, Field(min_length=6, max_length=260)]
    event_direction: Annotated[str, Field(min_length=6, max_length=260)]
    appearance_intent: Annotated[str, Field(min_length=4, max_length=220)]
    continuity_requirements: list[Annotated[str, Field(min_length=3, max_length=160)]] = Field(
        default_factory=list, max_length=6
    )


class DayBrief(StrictModel):
    """总导演输出的全天方向，不包含时段动作与镜头。"""

    content_date: date
    theme: Annotated[str, Field(min_length=4, max_length=160)]
    day_context: Annotated[str, Field(min_length=8, max_length=500)]
    shared_elements: list[SharedElement] = Field(default_factory=list, max_length=8)
    slots: list[SlotBrief] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_slot_order(self) -> DayBrief:
        if [item.slot for item in self.slots] != list(Slot):
            raise ValueError("DayBrief时段必须按morning、noon、evening排序")
        keys = [item.entity_key for item in self.shared_elements]
        if len(keys) != len(set(keys)):
            raise ValueError("DayBrief共享元素entityKey不能重复")
        return self


class EpisodeEnding(StrictModel):
    """主事件的可见结果，以及是否必须由严格首尾帧锁定。"""

    result: Annotated[str, Field(min_length=6, max_length=220)]
    visual_critical: bool = False
    key_entity_ids: list[
        Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]
    ] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_entity_ids(self) -> EpisodeEnding:
        if len(self.key_entity_ids) != len(set(self.key_entity_ids)):
            raise ValueError("结尾关键实体ID不能重复")
        return self


class EpisodeScript(StrictModel):
    """一个时段导演直接输出的8至15秒可执行脚本。"""

    title: Annotated[str, Field(min_length=2, max_length=80)]
    event_key: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]
    location_key: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]
    main_event: Annotated[str, Field(min_length=6, max_length=260)]
    scene: Annotated[str, Field(min_length=6, max_length=320)]
    style_context: Literal["indoor", "outdoor"]
    appearance: AppearancePlan
    actions: list[ActionStage] = Field(min_length=2, max_length=4)
    shots: list[ShotPlan] = Field(min_length=1, max_length=3)
    ending: EpisodeEnding
    duration_seconds: Annotated[int, Field(ge=8, le=15)]
    continuity: SceneContinuity

    @model_validator(mode="after")
    def validate_execution_graph(self) -> EpisodeScript:
        action_orders = [item.order for item in self.actions]
        if action_orders != list(range(1, len(self.actions) + 1)):
            raise ValueError("动作order必须从1连续递增")
        shot_orders = [item.order for item in self.shots]
        if shot_orders != list(range(1, len(self.shots) + 1)):
            raise ValueError("镜头order必须从1连续递增")
        referenced = [order for shot in self.shots for order in shot.action_orders]
        if referenced != sorted(referenced) or set(referenced) != set(action_orders):
            raise ValueError("镜头必须按顺序完整覆盖全部动作")
        if len(referenced) != len(set(referenced)):
            raise ValueError("每个动作只能属于一个镜头")
        entity_ids = {item.id for item in self.continuity.entities}
        unknown_actors = {
            item.actor_id
            for item in self.actions
            if item.actor_id != "environment" and item.actor_id not in entity_ids
        }
        if unknown_actors:
            raise ValueError(f"动作引用未登记主体：{', '.join(sorted(unknown_actors))}")
        anchor_ids = {item.id for item in self.continuity.anchors}
        unknown_ending_entities = set(self.ending.key_entity_ids) - entity_ids - anchor_ids
        if unknown_ending_entities:
            raise ValueError(
                "结尾引用未登记关键实体或场景锚点："
                + ", ".join(sorted(unknown_ending_entities))
            )
        return self


class EpisodePlan(StrictModel):
    """关系字段Slot与导演脚本的聚合，不重复保存脚本字段。"""

    slot: Slot
    script: EpisodeScript

    @property
    def title(self) -> str:
        return self.script.title

    @property
    def duration_seconds(self) -> int:
        return self.script.duration_seconds


class DailyProductionPlan(StrictModel):
    """DayBrief与三个时段脚本的内存聚合；数据库分别落Run和Episode。"""

    day_brief: DayBrief
    episodes: list[EpisodePlan] = Field(min_length=3, max_length=3)

    @property
    def content_date(self) -> date:
        return self.day_brief.content_date

    @property
    def theme(self) -> str:
        return self.day_brief.theme

    @property
    def day_context(self) -> str:
        return self.day_brief.day_context

    @model_validator(mode="after")
    def validate_day(self) -> DailyProductionPlan:
        if [item.slot for item in self.episodes] != list(Slot):
            raise ValueError("全天Episode必须按morning、noon、evening排序")
        declared = {item.entity_key: set(item.slots) for item in self.day_brief.shared_elements}
        observed: dict[str, set[Slot]] = {}
        for episode in self.episodes:
            for entity in episode.script.continuity.entities:
                if entity.entity_key in declared:
                    observed.setdefault(entity.entity_key, set()).add(episode.slot)
        for key, slots in declared.items():
            if observed.get(key, set()) != slots:
                raise ValueError(f"共享元素{key}未在声明的全部时段以同一entityKey出现")
        return self


class RecentContentSummary(StrictModel):
    """最近已批准或交付Run的结构化冷却摘要。"""

    content_date: date
    event_keys: tuple[str, ...] = ()
    location_keys: tuple[str, ...] = ()
    element_keys: tuple[str, ...] = ()
    pattern_ids: tuple[str, ...] = ()
    summary_text: Annotated[str, Field(min_length=1, max_length=2000)]
