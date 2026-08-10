"""全天导演、时段脚本与镜头设计的唯一业务契约。

本模块只表达观众能理解的剧情事实，不保存数据库字段、供应商参数或逐动作物理状态。
人物与猫咪身份由 Canon 和视觉锚点保证；这里只追踪跨镜头、跨时段真正重要的道具。
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .contract_base import StrictModel


class Slot(StrEnum):
    """全天固定观察顺序。"""

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


class StoryPatternId(StrEnum):
    """短片叙事骨架；它约束信息组织，不是固定剧情模板。"""

    PARALLEL_CONVERGENCE = "parallel_convergence"
    WATCH_TRIGGER_PAYOFF = "watch_trigger_payoff"
    SETUP_MISHAP_RECOVERY = "setup_mishap_recovery"
    CHOICE_REVEAL = "choice_reveal"
    ROUTINE_TAG = "routine_tag"
    PROCESS_MONTAGE = "process_montage"


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


class DurationIntent(StrictModel):
    requested_mode: DurationMode
    resolved_band: DurationBand
    resolution_reason: Annotated[str, Field(min_length=4, max_length=220)]

    @model_validator(mode="after")
    def validate_resolution(self) -> DurationIntent:
        if (
            self.requested_mode is not DurationMode.ADAPTIVE
            and self.requested_mode.value != self.resolved_band.value
        ):
            raise ValueError("固定时长档不得被总导演改写")
        return self


class RelationshipArc(StrictModel):
    """主活动、副活动和最终汇合的最小叙事合同。"""

    lead_activity: Annotated[str, Field(min_length=6, max_length=260)]
    secondary_activity: Annotated[str, Field(min_length=6, max_length=260)]
    convergence: Annotated[str, Field(min_length=6, max_length=260)]


class AppearancePlan(StrictModel):
    """本时段人物的完整定妆；猫咪外观由Canon和剧情道具表达。"""

    description: Annotated[str, Field(min_length=4, max_length=360)]
    change_reason: Annotated[str, Field(min_length=4, max_length=220)] | None = None


class GuestActor(StrictModel):
    """仅在剧情确实需要时出现的一名临时配角。"""

    id: Annotated[str, Field(pattern=r"^guest_[a-z0-9][a-z0-9_-]{0,63}$")]
    name: Annotated[str, Field(min_length=2, max_length=40)]
    role: Annotated[str, Field(min_length=3, max_length=120)]


class ActionStage(StrictModel):
    """同一主事件内连续发生的一次可见动作变化。"""

    order: Annotated[int, Field(ge=1, le=4)]
    actor_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]
    action: Annotated[str, Field(min_length=6, max_length=320)]
    visible_result: Annotated[str, Field(min_length=4, max_length=220)]


class ShotPlan(StrictModel):
    """镜头只负责视觉表达；每个动作只属于一个镜头。"""

    order: Annotated[int, Field(ge=1, le=3)]
    action_orders: list[Annotated[int, Field(ge=1, le=4)]] = Field(
        min_length=1,
        max_length=4,
    )
    framing: Annotated[str, Field(min_length=2, max_length=100)]
    camera_move: CameraMove
    direction: Annotated[str, Field(min_length=6, max_length=260)]

    @model_validator(mode="after")
    def validate_unique_actions(self) -> ShotPlan:
        if len(self.action_orders) != len(set(self.action_orders)):
            raise ValueError("同一镜头不能重复引用动作")
        return self


class CriticalProp(StrictModel):
    """需要在Prompt中明确起点和结果的关键道具。"""

    entity_key: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]
    name: Annotated[str, Field(min_length=2, max_length=60)]
    start: Annotated[str, Field(min_length=4, max_length=180)]
    end: Annotated[str, Field(min_length=4, max_length=180)]


class EpisodeEnding(StrictModel):
    """必须在画面中兑现的最终回报。"""

    result: Annotated[str, Field(min_length=6, max_length=260)]


class SlotBrief(StrictModel):
    """总导演交给单个时段导演的创作边界。"""

    slot: Slot
    narrative_role: Annotated[str, Field(min_length=6, max_length=220)]
    scene_direction: Annotated[str, Field(min_length=6, max_length=280)]
    event_direction: Annotated[str, Field(min_length=6, max_length=280)]
    appearance_intent: Annotated[str, Field(min_length=4, max_length=240)]
    resolved_activity_focus: ActivityFocus
    relationship_direction: Annotated[str, Field(min_length=6, max_length=280)]
    duration_intent: DurationIntent


class Handoff(StrictModel):
    """上一时段明确交给下一时段的同一逻辑道具或结果。"""

    entity_key: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]
    from_slot: Slot
    to_slot: Slot
    state: Annotated[str, Field(min_length=4, max_length=220)]

    @model_validator(mode="after")
    def validate_order(self) -> Handoff:
        if self.from_slot.sort_order >= self.to_slot.sort_order:
            raise ValueError("时段交接必须从较早时段指向较晚时段")
        return self


class DayBrief(StrictModel):
    """总导演输出的全天主线，不包含具体动作和运镜。"""

    content_date: date
    theme: Annotated[str, Field(min_length=2, max_length=160)]
    day_objective: Annotated[str, Field(min_length=8, max_length=320)]
    day_context: Annotated[str, Field(min_length=8, max_length=520)]
    shared_motif: Annotated[str, Field(min_length=4, max_length=240)]
    slot_briefs: list[SlotBrief] = Field(min_length=3, max_length=3)
    handoffs: list[Handoff] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_day(self) -> DayBrief:
        if [item.slot for item in self.slot_briefs] != list(Slot):
            raise ValueError("DayBrief时段必须按morning、noon、evening排序")
        handoff_keys = [(item.entity_key, item.from_slot, item.to_slot) for item in self.handoffs]
        if len(handoff_keys) != len(set(handoff_keys)):
            raise ValueError("同一时段交接不能重复")
        return self


class EpisodeScript(StrictModel):
    """单个时段导演直接输出的8至45秒可执行脚本。"""

    title: Annotated[str, Field(min_length=2, max_length=80)]
    event_key: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]
    location_key: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]
    story_pattern: StoryPatternId
    episode_question: Annotated[str, Field(min_length=6, max_length=220)]
    main_event: Annotated[str, Field(min_length=6, max_length=320)]
    scene: Annotated[str, Field(min_length=6, max_length=380)]
    style_context: Literal["indoor", "outdoor"]
    appearance: AppearancePlan
    activity_focus: ActivityFocus
    relationship_arc: RelationshipArc
    guest: GuestActor | None = None
    actions: list[ActionStage] = Field(min_length=2, max_length=4)
    shots: list[ShotPlan] = Field(min_length=1, max_length=3)
    ending: EpisodeEnding
    sound_design: Annotated[str, Field(min_length=8, max_length=360)]
    duration_seconds: Annotated[int, Field(ge=8, le=45)]
    critical_props: list[CriticalProp] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_execution(self) -> EpisodeScript:
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

        allowed_actors = {"person", "cat", "environment"}
        if self.guest is not None:
            allowed_actors.add(self.guest.id)
        unknown = {item.actor_id for item in self.actions} - allowed_actors
        if unknown:
            raise ValueError("动作引用未知主体：" + ", ".join(sorted(unknown)))

        prop_keys = [item.entity_key for item in self.critical_props]
        if len(prop_keys) != len(set(prop_keys)):
            raise ValueError("关键道具entityKey不能重复")
        if self.story_pattern is StoryPatternId.PROCESS_MONTAGE and self.duration_seconds <= 15:
            raise ValueError("process_montage只适用于16秒以上内容")
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

    @property
    def day_context(self) -> str:
        return self.day_brief.day_context

    @model_validator(mode="after")
    def validate_day(self) -> DailyProductionPlan:
        if [item.slot for item in self.episodes] != list(Slot):
            raise ValueError("全天Episode必须按morning、noon、evening排序")
        if len({item.script.title for item in self.episodes}) != 3:
            raise ValueError("早中晚标题必须互不重复")
        if len({item.script.event_key for item in self.episodes}) != 3:
            raise ValueError("早中晚必须使用不同的时段事件键")
        props_by_slot = {
            item.slot: {prop.entity_key for prop in item.script.critical_props}
            for item in self.episodes
        }
        briefs = {item.slot: item for item in self.day_brief.slot_briefs}
        for episode in self.episodes:
            brief = briefs[episode.slot]
            if episode.script.activity_focus is not brief.resolved_activity_focus:
                raise ValueError(f"{episode.slot.value}活动焦点与DayBrief不一致")
            minimum, maximum = brief.duration_intent.resolved_band.range
            if not minimum <= episode.duration_seconds <= maximum:
                raise ValueError(
                    f"{episode.slot.value}精确时长不在{brief.duration_intent.resolved_band.value}档"
                )
        for handoff in self.day_brief.handoffs:
            missing_slots = [
                slot.value
                for slot in (handoff.from_slot, handoff.to_slot)
                if handoff.entity_key not in props_by_slot[slot]
            ]
            if missing_slots:
                raise ValueError(
                    f"交接元素{handoff.entity_key}没有在"
                    f"{','.join(missing_slots)}时段使用同一entityKey登记"
                )
        return self


class RecentContentSummary(StrictModel):
    """最近已批准或交付Run的结构化冷却摘要。"""

    content_date: date
    event_keys: tuple[str, ...] = ()
    location_keys: tuple[str, ...] = ()
    element_keys: tuple[str, ...] = ()
    pattern_ids: tuple[str, ...] = ()
    summary_text: Annotated[str, Field(min_length=1, max_length=2000)]
