"""全天导演、时段脚本与镜头设计的唯一业务契约。

本模块只表达观众能理解的剧情事实，不保存数据库字段、供应商参数或逐动作物理状态。
人物与猫咪身份由 Canon 和视觉锚点保证；真正重要的物体关系只用少量自然语言硬约束
表达，不再建立重复道具状态表。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from .contract_base import StrictModel

CURRENT_CONTRACT_VERSION = 3


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


class StoryInputMode(StrEnum):
    """生活故事项目的剧情来源。"""

    THEME_EXPAND = "theme_expand"
    EPISODE_SCRIPTS = "episode_scripts"


class SceneRoute(StrEnum):
    """早中晚场景关系；只指导导演，不作为剧情质量硬门。"""

    ADAPTIVE = "adaptive"
    PROGRESSIVE_LOCATIONS = "progressive_locations"
    SINGLE_LOCATION = "single_location"


class EpisodeSources(StrictModel):
    """用户提供的逐集原文；顺序人工模式允许只先填写一个时段。"""

    morning: Annotated[str, Field(min_length=4)] | None = None
    noon: Annotated[str, Field(min_length=4)] | None = None
    evening: Annotated[str, Field(min_length=4)] | None = None

    def for_slot(self, slot: Slot) -> str | None:
        return {
            Slot.MORNING: self.morning,
            Slot.NOON: self.noon,
            Slot.EVENING: self.evening,
        }[slot]

    @property
    def populated_slots(self) -> tuple[Slot, ...]:
        return tuple(slot for slot in Slot if self.for_slot(slot) is not None)


class StoryProjectInput(StrictModel):
    """生活故事项目的最小创作输入，不混入运行状态或供应商参数。"""

    theme: Annotated[str, Field(min_length=2, max_length=160)]
    input_mode: StoryInputMode = StoryInputMode.THEME_EXPAND
    scene_route: SceneRoute = SceneRoute.ADAPTIVE
    episode_sources: EpisodeSources = Field(default_factory=EpisodeSources)

    @model_validator(mode="after")
    def validate_source_mode(self) -> StoryProjectInput:
        populated = self.episode_sources.populated_slots
        if self.input_mode is StoryInputMode.THEME_EXPAND and populated:
            raise ValueError("主题扩写模式不能同时携带用户逐集原文")
        if self.input_mode is StoryInputMode.EPISODE_SCRIPTS and not populated:
            raise ValueError("已有剧本模式至少需要提供一个时段原文")
        return self


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
    """人工确认的实际成片结果，只用于审计、解锁和可选关联建议。"""

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


class StoryConnectionMode(StrEnum):
    """后续时段与已完成剧情的关系；不存在隐式默认继承。"""

    INDEPENDENT = "independent"
    SELECTED_LINK = "selected_link"
    DIRECT_CONTINUE = "direct_continue"


class StoryConnection(StrictModel):
    """用户确认的轻量剧情关联卡。

    关联卡可以保留但不加载给导演，便于用户比较不同创作路线；后续导演只会
    收到 ``use_for_director`` 为真的卡片正文，不读取完整 AcceptedOutcome。
    """

    use_for_director: Annotated[bool, Field(alias="useForDirector")] = False
    mode: StoryConnectionMode = StoryConnectionMode.INDEPENDENT
    brief: str = ""
    confirmed_at: Annotated[
        datetime | None,
        Field(alias="confirmedAt"),
    ] = None

    @model_validator(mode="after")
    def validate_connection(self) -> StoryConnection:
        normalized = self.brief.strip()
        if self.use_for_director and not normalized:
            raise ValueError("加载剧情关联卡时必须填写关联说明")
        if self.mode is StoryConnectionMode.INDEPENDENT and self.use_for_director:
            raise ValueError("独立成篇模式不能加载前序剧情")
        self.brief = normalized
        return self


class ConnectionSuggestion(StrictModel):
    """可选Planning调用返回的关联草稿；保存前仍需用户编辑和确认。"""

    mode: StoryConnectionMode
    brief: Annotated[str, Field(min_length=1, max_length=1200)]


class CrossSlotReferenceRole(StrEnum):
    IDENTITY = "identity"
    PROP = "prop"
    SCENE = "scene"
    COMPOSITION = "composition"
    MOTION = "motion"


class CrossSlotReferenceTarget(StrEnum):
    OPENING_ANCHOR = "opening_anchor"
    VIDEO = "video"
    BOTH = "both"


class CrossSlotReference(StrictModel):
    """用户明确选择的前序媒体引用；只在目标节点的输入快照中生效。"""

    asset_id: Annotated[UUID, Field(alias="assetId")]
    role: CrossSlotReferenceRole
    apply_to: Annotated[CrossSlotReferenceTarget, Field(alias="applyTo")]


class OutlineEpisode(StrictModel):
    """总导演为一个命名时段留下的场景和剧情方向。"""

    scene: Annotated[str, Field(min_length=4)]
    direction: Annotated[str, Field(min_length=8)]


class OutlineEpisodes(StrictModel):
    """固定命名时段从结构上杜绝重复Noon或第四个时段。"""

    morning: OutlineEpisode
    noon: OutlineEpisode
    evening: OutlineEpisode

    def for_slot(self, slot: Slot) -> OutlineEpisode:
        return {
            Slot.MORNING: self.morning,
            Slot.NOON: self.noon,
            Slot.EVENING: self.evening,
        }[slot]


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


class ProjectOutlineV3(StrictModel):
    """总导演输出的当前契约；不重复保存活动焦点和时长。"""

    content_date: date
    theme: Annotated[str, Field(min_length=2, max_length=160)]
    day_arc: Annotated[str, Field(min_length=12)]
    episodes: OutlineEpisodes
    handoffs: list[Handoff] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_day(self) -> ProjectOutlineV3:
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
    """生活故事项目完成三集规划后的内存聚合。"""

    content_date: date
    project_input: StoryProjectInput
    outline: ProjectOutlineV3 | None = None
    episodes: list[EpisodePlan] = Field(min_length=3, max_length=3)

    @property
    def theme(self) -> str:
        return self.project_input.theme

    @model_validator(mode="after")
    def validate_day(self) -> DailyProductionPlan:
        if self.project_input.input_mode is StoryInputMode.THEME_EXPAND:
            if self.outline is None:
                raise ValueError("主题扩写项目必须包含ProjectOutlineV3")
            if self.outline.theme != self.project_input.theme:
                raise ValueError("ProjectOutlineV3主题与生活故事项目不一致")
            if self.outline.content_date != self.content_date:
                raise ValueError("ProjectOutlineV3日期与生活故事项目不一致")
        else:
            if self.outline is not None:
                raise ValueError("已有剧本项目不得伪造ProjectOutlineV3")
        if [item.slot for item in self.episodes] != list(Slot):
            raise ValueError("全天Episode必须按morning、noon、evening排序")
        return self


class RecentContentSummary(StrictModel):
    """最近已批准或交付Run的结构化冷却摘要。"""

    content_date: date
    event_keys: tuple[str, ...] = ()
    location_keys: tuple[str, ...] = ()
    summary_text: Annotated[str, Field(min_length=1, max_length=2000)]
