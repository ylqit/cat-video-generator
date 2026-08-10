"""全天导演、时段脚本与镜头设计的唯一业务契约。

本模块只表达观众能理解的剧情事实，不保存数据库字段、供应商参数或逐动作物理状态。
人物与猫咪身份由 Canon 和视觉锚点保证；真正重要的物体关系只用少量自然语言硬约束
表达，不再建立重复道具状态表。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .contract_base import StrictModel

CURRENT_CONTRACT_VERSION = 2


class ContractVersionError(ValueError):
    """数据库Run与当前导演契约不兼容，禁止猜测补齐旧字段。"""


class Slot(StrEnum):
    """全天固定观察顺序。"""

    MORNING = "morning"
    NOON = "noon"
    EVENING = "evening"

    @property
    def sort_order(self) -> int:
        return {Slot.MORNING: 1, Slot.NOON: 2, Slot.EVENING: 3}[self]


class ActivityFocus(StrEnum):
    """单集可见事件由谁主导；默认猫咪推动观众注意力。"""

    CAT_LEAD = "cat_lead"
    PERSON_LEAD = "person_lead"
    BALANCED = "balanced"


class ActivityFocusMode(StrEnum):
    INHERIT = "inherit"
    CAT_LEAD = "cat_lead"
    PERSON_LEAD = "person_lead"
    BALANCED = "balanced"
    ADAPTIVE = "adaptive"


class DurationMode(StrEnum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"
    ADAPTIVE = "adaptive"


class DurationBand(StrEnum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"

    @property
    def range(self) -> tuple[int, int]:
        return {
            DurationBand.SHORT: (8, 15),
            DurationBand.MEDIUM: (16, 30),
            DurationBand.LONG: (31, 45),
        }[self]


class SlotCreativeControl(StrictModel):
    slot: Slot
    activity_focus: ActivityFocusMode = ActivityFocusMode.INHERIT
    duration_mode: DurationMode = DurationMode.ADAPTIVE


class RunCreativeControls(StrictModel):
    """Web在总导演调用前冻结的创作偏好。"""

    default_activity_focus: ActivityFocusMode = ActivityFocusMode.CAT_LEAD
    slot_controls: list[SlotCreativeControl] = Field(
        default_factory=lambda: [SlotCreativeControl(slot=slot) for slot in Slot],
        min_length=3,
        max_length=3,
    )

    @model_validator(mode="after")
    def validate_controls(self) -> RunCreativeControls:
        if self.default_activity_focus is ActivityFocusMode.INHERIT:
            raise ValueError("全天默认活动焦点不能是inherit")
        if [item.slot for item in self.slot_controls] != list(Slot):
            raise ValueError("创作控制必须按morning、noon、evening排序")
        return self

    def requested_focus(self, slot: Slot) -> ActivityFocusMode:
        selected = next(item for item in self.slot_controls if item.slot is slot)
        return (
            self.default_activity_focus
            if selected.activity_focus is ActivityFocusMode.INHERIT
            else selected.activity_focus
        )


class ShotDirection(StrictModel):
    """一段完整镜头导演文字，不再把同一动作拆成重复字段。"""

    order: Annotated[int, Field(ge=1, le=3)]
    direction: Annotated[str, Field(min_length=12)]


class HardConstraint(StrictModel):
    """仅保留会直接影响生成正确性的自然语言关系事实。

    ``shot_orders``为空表示全片适用；否则只投影到指定镜头。
    """

    shot_orders: list[Annotated[int, Field(ge=1, le=3)]] = Field(
        default_factory=list,
        max_length=3,
    )
    text: Annotated[str, Field(min_length=8)]

    @model_validator(mode="after")
    def validate_shot_orders(self) -> HardConstraint:
        if self.shot_orders != sorted(set(self.shot_orders)):
            raise ValueError("硬约束的shotOrders必须唯一且递增")
        return self


class AcceptedOutcome(StrictModel):
    """人工确认的实际成片结果，是下一时段导演唯一可继承的媒体事实。"""

    summary: Annotated[str, Field(min_length=8)]
    carry_forward: list[Annotated[str, Field(min_length=2)]] = Field(
        default_factory=list,
        alias="carryForward",
        max_length=8,
    )
    do_not_carry_forward: list[Annotated[str, Field(min_length=2)]] = Field(
        default_factory=list,
        alias="doNotCarryForward",
        max_length=8,
    )
    confirmed_at: Annotated[datetime, Field(alias="confirmedAt")]

    @model_validator(mode="after")
    def validate_outcome(self) -> AcceptedOutcome:
        if set(self.carry_forward) & set(self.do_not_carry_forward):
            raise ValueError("同一事实不能同时继承和禁止继承")
        return self


class SlotBrief(StrictModel):
    """总导演交给单个时段导演的创作边界。"""

    slot: Slot
    narrative_role: Annotated[str, Field(min_length=4)]
    event_direction: Annotated[str, Field(min_length=8)]
    appearance_intent: Annotated[str, Field(min_length=4)]
    activity_focus: ActivityFocus
    duration_band: DurationBand
    decision_reason: Annotated[str, Field(min_length=4)]


class Handoff(StrictModel):
    """上一时段明确交给下一时段的同一逻辑道具或结果。"""

    name: Annotated[str, Field(min_length=2, max_length=80)]
    from_slot: Slot
    to_slot: Slot
    continuity: Annotated[str, Field(min_length=4)]

    @model_validator(mode="after")
    def validate_order(self) -> Handoff:
        if self.from_slot.sort_order >= self.to_slot.sort_order:
            raise ValueError("时段交接必须从较早时段指向较晚时段")
        return self


class DayBrief(StrictModel):
    """总导演输出的全天主线，不包含具体动作和运镜。"""

    content_date: date
    theme: Annotated[str, Field(min_length=2, max_length=160)]
    day_arc: Annotated[str, Field(min_length=12)]
    slot_briefs: list[SlotBrief] = Field(min_length=3, max_length=3)
    handoffs: list[Handoff] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_day(self) -> DayBrief:
        if [item.slot for item in self.slot_briefs] != list(Slot):
            raise ValueError("DayBrief时段必须按morning、noon、evening排序")
        handoff_keys = [(item.name, item.from_slot, item.to_slot) for item in self.handoffs]
        if len(handoff_keys) != len(set(handoff_keys)):
            raise ValueError("同一时段交接不能重复")
        return self


class EpisodeScript(StrictModel):
    """长剧情正文加极小自动化控制壳的时段导演契约。"""

    title: Annotated[str, Field(min_length=2, max_length=80)]
    event_key: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]
    location_key: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]
    visual_context: Literal["indoor", "outdoor"]
    activity_focus: ActivityFocus
    duration_seconds: Annotated[int, Field(ge=8, le=45)]
    appearance: Annotated[str, Field(min_length=6)]
    story_text: Annotated[str, Field(min_length=24)]
    relationship_arc: Annotated[str, Field(min_length=12)]
    shots: list[ShotDirection] = Field(min_length=1, max_length=3)
    hard_constraints: list[HardConstraint] = Field(default_factory=list, max_length=8)
    sound_design: Annotated[str, Field(min_length=8)]
    ending: Annotated[str, Field(min_length=6)]

    @model_validator(mode="after")
    def validate_execution(self) -> EpisodeScript:
        shot_orders = [item.order for item in self.shots]
        if shot_orders != list(range(1, len(self.shots) + 1)):
            raise ValueError("镜头order必须从1连续递增")
        shot_order_set = set(shot_orders)
        unknown_constraint_shots = {
            order
            for constraint in self.hard_constraints
            for order in constraint.shot_orders
            if order not in shot_order_set
        }
        if unknown_constraint_shots:
            raise ValueError(
                "硬约束引用不存在的镜头："
                + ", ".join(str(item) for item in sorted(unknown_constraint_shots))
            )
        required_shots = (
            1 if self.duration_seconds <= 15 else 2 if self.duration_seconds <= 30 else 3
        )
        if len(self.shots) < required_shots:
            raise ValueError("当前时长没有足够的连续镜头承载渲染区段")
        return self


class EpisodePlan(StrictModel):
    """关系字段Slot与导演脚本的最小聚合。"""

    slot: Slot
    script: EpisodeScript

    @property
    def title(self) -> str:
        return self.script.title

    @property
    def duration_seconds(self) -> int:
        return self.script.duration_seconds


class DailyProductionPlan(StrictModel):
    """DayBrief与三个顺序时段脚本的内存聚合。"""

    day_brief: DayBrief
    episodes: list[EpisodePlan] = Field(min_length=3, max_length=3)

    @property
    def content_date(self) -> date:
        return self.day_brief.content_date

    @property
    def theme(self) -> str:
        return self.day_brief.theme

    @model_validator(mode="after")
    def validate_day(self) -> DailyProductionPlan:
        if [item.slot for item in self.episodes] != list(Slot):
            raise ValueError("全天Episode必须按morning、noon、evening排序")
        if len({item.script.title for item in self.episodes}) != 3:
            raise ValueError("早中晚标题必须互不重复")
        if len({item.script.event_key for item in self.episodes}) != 3:
            raise ValueError("早中晚必须使用不同的时段事件键")
        briefs = {item.slot: item for item in self.day_brief.slot_briefs}
        for episode in self.episodes:
            brief = briefs[episode.slot]
            if episode.script.activity_focus is not brief.activity_focus:
                raise ValueError(f"{episode.slot.value}活动焦点与DayBrief不一致")
            minimum, maximum = brief.duration_band.range
            if not minimum <= episode.duration_seconds <= maximum:
                raise ValueError(
                    f"{episode.slot.value}精确时长不在{brief.duration_band.value}档"
                )
        return self


class RecentContentSummary(StrictModel):
    """最近已批准或交付Run的结构化冷却摘要。"""

    content_date: date
    event_keys: tuple[str, ...] = ()
    location_keys: tuple[str, ...] = ()
    summary_text: Annotated[str, Field(min_length=1, max_length=2000)]
