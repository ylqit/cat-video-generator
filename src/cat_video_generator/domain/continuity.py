"""可见世界状态契约与确定性连续性校验。

连续性只覆盖本 Episode 明确建账的角色、服装、家具和关键道具。普通背景
装饰不进入硬门，避免通过无限增加规则来追求不可实现的逐像素一致。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContinuityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DominantView(StrEnum):
    FRONT = "front"
    SIDE = "side"
    BACK = "back"
    MIXED = "mixed"


class EntityLifecycle(StrEnum):
    PERSIST = "persist"
    ENTER = "enter"
    EXIT = "exit"
    CONSUME = "consume"
    TRANSFORM = "transform"


class TopologyChange(StrEnum):
    NONE = "none"
    SIT = "sit"
    STAND = "stand"


class RenderRiskLevel(StrEnum):
    """只描述预计生成难度，不参与收费任务准入。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class WorldConsistencyReport:
    """状态重放结果；矛盾阻断，渲染风险只供诊断和人工决策。"""

    world_consistency_status: str
    contradictions: tuple[str, ...]
    render_risk_level: RenderRiskLevel
    render_risk_reasons: tuple[str, ...]
    multi_clip_recommended: bool


class SceneAnchor(ContinuityModel):
    """场景中可承重或界定空间的稳定位置。"""

    anchor_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")
    display_name: str = Field(min_length=1, max_length=80)
    anchor_type: str = Field(
        pattern=r"^(ground|table|seat|shelf|counter|wall|window|vehicle|other)$"
    )
    appears_from_shot: int = Field(default=1, ge=1, le=3)
    persists: bool = True


class TrackedEntity(ContinuityModel):
    """会被操作、承重、包含或跨镜头持续出现的具体实例。"""

    entity_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")
    display_name: str = Field(min_length=1, max_length=80)
    entity_type: str = Field(
        pattern=(
            r"^(person|cat|guest|clothing|prop|food|container|furniture|environment)$"
        )
    )
    count: int = Field(default=1, ge=1, le=3)
    appearance_signature: str = Field(min_length=3, max_length=220)
    initial_anchor_id: str | None = None
    initial_support_id: str | None = None
    initial_contact_id: str | None = None
    initial_container_id: str | None = None
    lifecycle: EntityLifecycle = EntityLifecycle.PERSIST
    non_throwing: bool = True


class ActionTransition(ContinuityModel):
    """一个动作阶段中实体位置、支撑、接触或包含关系的真实变化。"""

    action_order: int = Field(ge=1, le=4)
    shot_order: int = Field(ge=1, le=3)
    actor_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")
    target_entity_ids: list[str] = Field(default_factory=list, max_length=2)
    state_entity_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$",
    )
    no_state_change: bool = False
    before_anchor_id: str | None = None
    after_anchor_id: str | None = None
    support_before: str | None = Field(
        default=None,
        description="精确scene anchor_id或tracked entity_id，不写自然语言句子",
    )
    support_after: str | None = Field(
        default=None,
        description="精确scene anchor_id或tracked entity_id，不写自然语言句子",
    )
    contact_before: str | None = None
    contact_after: str | None = None
    containment_before: str | None = None
    containment_after: str | None = None
    lifecycle_event: EntityLifecycle | None = None
    topology_change: TopologyChange = TopologyChange.NONE
    seat_anchor_id: str | None = None
    continuous_shot: bool = True
    change_reason: str | None = Field(default=None, min_length=2, max_length=160)
    resulting_appearance_signature: str | None = Field(
        default=None,
        min_length=3,
        max_length=220,
    )

    @model_validator(mode="after")
    def validate_topology(self) -> ActionTransition:
        if self.topology_change is not TopologyChange.NONE and not self.seat_anchor_id:
            raise ValueError("坐下或站起动作必须引用已声明的seat锚点")
        if self.no_state_change and _transition_has_state_fields(self):
            raise ValueError("noStateChange动作不能同时声明位置、支撑、包含或生命周期变化")
        return self


class ShotBoundaryState(ContinuityModel):
    """切镜前后必须继承的可见实体位置与服装层。"""

    after_shot_order: int = Field(ge=1, le=2)
    next_shot_order: int = Field(ge=2, le=3)
    visible_entity_ids: list[str] = Field(default_factory=list, max_length=20)
    entity_anchor_ids: dict[str, str] = Field(default_factory=dict)
    appearance_layers: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_order(self) -> ShotBoundaryState:
        if self.next_shot_order != self.after_shot_order + 1:
            raise ValueError("切镜边界必须连接相邻镜头")
        return self


class VisibleWorldPlan(ContinuityModel):
    """一条 Episode 的可见世界账本。"""

    scene_anchors: list[SceneAnchor] = Field(default_factory=list, max_length=12)
    tracked_entities: list[TrackedEntity] = Field(default_factory=list, max_length=20)
    action_transitions: list[ActionTransition] = Field(
        default_factory=list,
        max_length=8,
    )
    shot_boundary_states: list[ShotBoundaryState] = Field(
        default_factory=list,
        max_length=2,
    )

    @model_validator(mode="after")
    def validate_graph(self) -> VisibleWorldPlan:
        anchors = {item.anchor_id: item for item in self.scene_anchors}
        entities = {item.entity_id: item for item in self.tracked_entities}
        if len(anchors) != len(self.scene_anchors):
            raise ValueError("sceneAnchors.anchorId不能重复")
        if len(entities) != len(self.tracked_entities):
            raise ValueError("trackedEntities.entityId不能重复")
        display_names = [item.display_name for item in self.tracked_entities]
        if len(display_names) != len(set(display_names)):
            raise ValueError("trackedEntities.displayName不能重复")
        for entity in self.tracked_entities:
            if entity.initial_anchor_id and entity.initial_anchor_id not in anchors:
                raise ValueError(
                    f"{entity.entity_id}引用了不存在的初始锚点{entity.initial_anchor_id}"
                )
            if entity.lifecycle is not EntityLifecycle.ENTER and not entity.initial_anchor_id:
                raise ValueError(f"{entity.entity_id}使用前必须有初始锚点或声明enter")
            if entity.entity_type in {"person", "cat"} and entity.count != 1:
                raise ValueError("固定人物和灰白猫的count必须等于1")

        allowed_supports = set(anchors) | set(entities) | {"person", "cat", "guest"}
        allowed_containers = set(entities)
        for entity in self.tracked_entities:
            if entity.initial_support_id and entity.initial_support_id not in allowed_supports:
                raise ValueError(f"{entity.entity_id}引用了未建账初始支撑")
            if (
                entity.initial_container_id
                and entity.initial_container_id not in allowed_containers
            ):
                raise ValueError(f"{entity.entity_id}引用了未建账初始容器")
        seen_actions: set[int] = set()
        for transition in self.action_transitions:
            if transition.action_order in seen_actions:
                raise ValueError("每个动作阶段只能有一条世界状态转换")
            seen_actions.add(transition.action_order)
            if transition.actor_id not in {"person", "cat", "guest", "environment"}:
                raise ValueError(f"未知动作主体{transition.actor_id}")
            known_targets = set(entities) | set(anchors)
            if any(
                item not in known_targets
                for item in transition.target_entity_ids
            ):
                raise ValueError("actionTransitions引用了未建账实体或场景锚点")
            if (
                transition.state_entity_id is not None
                and transition.state_entity_id not in entities
            ):
                raise ValueError("actionTransitions.stateEntityId引用了未建账实体")
            if (
                transition.state_entity_id is not None
                and transition.state_entity_id not in transition.target_entity_ids
            ):
                raise ValueError("stateEntityId必须同时列入targetEntityIds")
            for anchor_id in (
                transition.before_anchor_id,
                transition.after_anchor_id,
                transition.seat_anchor_id,
            ):
                if anchor_id and anchor_id not in anchors:
                    raise ValueError(f"动作引用了未建账场景锚点{anchor_id}")
            for support in (transition.support_before, transition.support_after):
                if support and support not in allowed_supports:
                    raise ValueError(f"动作引用了未建账支撑{support}")
            for container in (
                transition.containment_before,
                transition.containment_after,
            ):
                if container and container not in allowed_containers:
                    raise ValueError(f"动作引用了未建账容器{container}")
        for boundary in self.shot_boundary_states:
            unknown = set(boundary.visible_entity_ids) - set(entities)
            unknown.update(set(boundary.entity_anchor_ids) - set(entities))
            unknown.update(set(boundary.appearance_layers) - {"person", "cat", "guest"})
            if unknown:
                raise ValueError(f"切镜边界引用了未建账对象: {sorted(unknown)}")
            for anchor_id in boundary.entity_anchor_ids.values():
                if anchor_id not in anchors:
                    raise ValueError("切镜边界引用了未建账场景锚点")
        return self

    @classmethod
    def from_scene_props(cls, props: list[object]) -> VisibleWorldPlan:
        """只读兼容旧 SceneProp；不猜测动作、支撑或生命周期变化。"""

        anchors: list[SceneAnchor] = []
        entities: list[TrackedEntity] = []
        for index, prop in enumerate(props, start=1):
            name = str(prop.name)
            placement = str(prop.placement)
            anchor_id = f"legacy-anchor-{index}"
            entity_id = f"legacy-prop-{index}"
            anchors.append(
                SceneAnchor(
                    anchor_id=anchor_id,
                    display_name=placement,
                    anchor_type="other",
                )
            )
            entities.append(
                TrackedEntity(
                    entity_id=entity_id,
                    display_name=name,
                    entity_type="prop",
                    appearance_signature=f"历史场景物体：{name}",
                    initial_anchor_id=anchor_id,
                )
            )
        return cls(scene_anchors=anchors, tracked_entities=entities)


@dataclass(slots=True)
class _EntityState:
    """状态重放中的可变快照，不进入数据库或供应商契约。"""

    anchor_id: str | None
    support_id: str | None = None
    contact_id: str | None = None
    container_id: str | None = None
    active: bool = True
    appearance_signature: str = ""


@dataclass(frozen=True, slots=True)
class ReplayedEntityState:
    """对外只读的Episode终态，供后续时段和Prompt共享同一事实。"""

    anchor_id: str | None
    support_id: str | None
    contact_id: str | None
    container_id: str | None
    active: bool
    appearance_signature: str


# 状态转换机械规则单独维护；这些别名保持现有领域API和调用点稳定。
from .continuity_support import (  # noqa: E402,I001
    apply_after_state as _apply_after_state,
    apply_lifecycle as _apply_lifecycle,
    check_before_state as _check_before_state,
    remember_shot_state as _remember_shot_state,
    resolve_transition_state_entity_id,
    transition_has_state_fields as _transition_has_state_fields,
    validate_boundaries as _validate_boundaries,
    validate_no_dangling_relations as _validate_no_dangling_relations,
    validate_relation_references as _validate_relation_references,
    validate_topology_transition as _validate_topology_transition,
)


def _initial_support(entity: TrackedEntity, anchors: dict[str, SceneAnchor]) -> str | None:
    if entity.initial_support_id is not None:
        return entity.initial_support_id
    anchor = anchors.get(entity.initial_anchor_id or "")
    if anchor is not None and anchor.anchor_type in {
        "ground",
        "table",
        "seat",
        "shelf",
        "counter",
        "vehicle",
    }:
        return anchor.anchor_id
    return None


def _replay_visible_world(
    plan: VisibleWorldPlan,
) -> tuple[
    dict[str, _EntityState],
    dict[int, dict[str, _EntityState]],
    tuple[str, ...],
]:
    issues: list[str] = []
    entities = {item.entity_id: item for item in plan.tracked_entities}
    anchors = {item.anchor_id: item for item in plan.scene_anchors}
    states = {
        item.entity_id: _EntityState(
            anchor_id=item.initial_anchor_id,
            support_id=_initial_support(item, anchors),
            contact_id=item.initial_contact_id,
            container_id=item.initial_container_id,
            active=item.lifecycle is not EntityLifecycle.ENTER,
            appearance_signature=item.appearance_signature,
        )
        for item in plan.tracked_entities
    }
    ordered = sorted(plan.action_transitions, key=lambda item: item.action_order)
    if [item.action_order for item in ordered] != [
        item.action_order for item in plan.action_transitions
    ]:
        issues.append("actionTransitions必须按actionOrder递增排列")
    if any(
        current.shot_order > following.shot_order
        for current, following in zip(ordered, ordered[1:], strict=False)
    ):
        issues.append("actionTransitions的shotOrder不能随动作推进而倒退")

    # 初始关系也属于可见世界，而不是只有第一个动作开始后才检查。
    # 这会阻止“杯子一开始由尚未入场的托盘支撑”等空壳初态进入Prompt。
    for entity_id, state in states.items():
        for relation_name, relation_id in (
            ("支撑", state.support_id),
            ("接触", state.contact_id),
            ("容器", state.container_id),
        ):
            if relation_id is None or relation_id in anchors:
                continue
            related = states.get(relation_id)
            if related is None:
                if relation_name != "接触":
                    issues.append(f"{entity_id}初始{relation_name}{relation_id}未建账")
            elif not related.active:
                issues.append(
                    f"{entity_id}初始{relation_name}{relation_id}尚未进入场景"
                )

    shot_states: dict[int, dict[str, _EntityState]] = {}
    for transition in ordered:
        actor_state = states.get(transition.actor_id)
        if transition.actor_id != "environment":
            if actor_state is None:
                issues.append(f"动作主体{transition.actor_id}未在trackedEntities建账")
            elif not actor_state.active:
                issues.append(f"动作主体{transition.actor_id}已离场，不能继续执行动作")
        for target_id in transition.target_entity_ids:
            target_state = states.get(target_id)
            if (
                target_state is not None
                and not target_state.active
                and transition.lifecycle_event is not EntityLifecycle.ENTER
            ):
                issues.append(f"目标实体{target_id}已离场或尚未进入场景")
        for anchor_id in (
            transition.before_anchor_id,
            transition.after_anchor_id,
            transition.seat_anchor_id,
        ):
            anchor = anchors.get(anchor_id or "")
            if anchor is not None and anchor.appears_from_shot > transition.shot_order:
                issues.append(
                    f"镜头{transition.shot_order}使用了尚未出现的锚点{anchor.anchor_id}"
                )
        if (
            transition.lifecycle_event
            not in {None, EntityLifecycle.PERSIST}
            and not transition.change_reason
        ):
            issues.append(
                f"动作{transition.action_order}的生命周期变化缺少changeReason"
            )
        if (
            transition.lifecycle_event is EntityLifecycle.TRANSFORM
            and not transition.resulting_appearance_signature
        ):
            issues.append(
                f"动作{transition.action_order}的transform缺少结果外观签名"
            )

        if transition.topology_change is not TopologyChange.NONE:
            _validate_topology_transition(
                transition,
                anchors=anchors,
                states=states,
                issues=issues,
            )

        if transition.no_state_change:
            _remember_shot_state(shot_states, transition.shot_order, states)
            continue
        state_entity_id = resolve_transition_state_entity_id(transition, states)
        if state_entity_id is None:
            issues.append(
                f"动作{transition.action_order}必须声明stateEntityId或noStateChange"
            )
        else:
            entity = entities.get(state_entity_id)
            state = states.get(state_entity_id)
            if entity is None or state is None:
                issues.append(f"动作{transition.action_order}无法解析状态实体")
                _remember_shot_state(shot_states, transition.shot_order, states)
                continue
            _validate_relation_references(
                transition,
                anchors=anchors,
                states=states,
                issues=issues,
            )
            if (
                not state.active
                and transition.lifecycle_event is not EntityLifecycle.ENTER
            ):
                issues.append(f"{state_entity_id}在明确进入场景前或离场后已被使用")
            else:
                _check_before_state(transition, state_entity_id, state, issues)
                _apply_lifecycle(transition, entity, state, issues)
                if transition.lifecycle_event in {
                    EntityLifecycle.EXIT,
                    EntityLifecycle.CONSUME,
                }:
                    _validate_no_dangling_relations(
                        state_entity_id,
                        states=states,
                        issues=issues,
                    )
                if state.active:
                    _apply_after_state(transition, state)
                    if (
                        entity.non_throwing
                        and state.support_id is None
                        and state.anchor_id is None
                    ):
                        issues.append(f"{state_entity_id}动作结束后缺少合法支撑")

        _remember_shot_state(shot_states, transition.shot_order, states)

    _validate_boundaries(plan, shot_states=shot_states, issues=issues)
    return states, shot_states, tuple(dict.fromkeys(issues))


def validate_visible_world(plan: VisibleWorldPlan) -> tuple[str, ...]:
    """按动作顺序重放世界状态，只返回真实自相矛盾。

    交互次数、坐下再站起或跨镜头物理动作不会在这里判错；它们属于渲染风险，
    由 :func:`assess_visible_world` 作为非阻断诊断返回。
    """

    return _replay_visible_world(plan)[2]


def replay_terminal_state(
    plan: VisibleWorldPlan,
) -> dict[str, ReplayedEntityState]:
    """返回与连续性校验完全同源的Episode终态，不再由Prompt代码另行猜测。"""

    states, _, _ = _replay_visible_world(plan)
    return {
        entity_id: ReplayedEntityState(
            anchor_id=state.anchor_id,
            support_id=state.support_id,
            contact_id=state.contact_id,
            container_id=state.container_id,
            active=state.active,
            appearance_signature=state.appearance_signature,
        )
        for entity_id, state in states.items()
    }


def assess_visible_world(plan: VisibleWorldPlan) -> WorldConsistencyReport:
    """返回一致性与渲染风险；风险永远不升级为收费准入硬门。"""

    contradictions = validate_visible_world(plan)
    reasons: list[str] = []
    physical_changes = 0
    topology_changes = 0
    cross_cut_changes = 0
    multi_target_actions = 0
    lifecycle_changes = 0
    entity_types = {
        item.entity_id: item.entity_type for item in plan.tracked_entities
    }
    for transition in plan.action_transitions:
        target_types = {
            entity_types.get(entity_id, "unknown")
            for entity_id in transition.target_entity_ids
        }
        changed = any(
            (
                transition.lifecycle_event is not None,
                transition.before_anchor_id != transition.after_anchor_id
                and transition.after_anchor_id is not None,
                transition.support_before != transition.support_after
                and transition.support_after is not None,
                transition.containment_before != transition.containment_after
                and transition.containment_after is not None,
            )
        )
        if changed and target_types & {
            "clothing",
            "prop",
            "food",
            "container",
            "furniture",
            "person",
            "cat",
            "guest",
        }:
            physical_changes += 1
            if not transition.continuous_shot:
                cross_cut_changes += 1
        if transition.topology_change is not TopologyChange.NONE:
            topology_changes += 1
        if len(transition.target_entity_ids) > 1:
            multi_target_actions += 1
        if transition.lifecycle_event not in {None, EntityLifecycle.PERSIST}:
            lifecycle_changes += 1

    if physical_changes >= 3:
        reasons.append(f"包含{physical_changes}次可见物理状态变化")
    if topology_changes >= 2:
        reasons.append(f"包含{topology_changes}次坐下或站起")
    if cross_cut_changes:
        reasons.append(f"包含{cross_cut_changes}次跨镜头物理变化")
    if multi_target_actions:
        reasons.append(f"包含{multi_target_actions}个多目标动作")
    if lifecycle_changes:
        reasons.append(f"包含{lifecycle_changes}次实体生命周期变化")

    if physical_changes >= 4 or cross_cut_changes or lifecycle_changes >= 2:
        level = RenderRiskLevel.HIGH
    elif physical_changes >= 2 or topology_changes >= 2 or multi_target_actions:
        level = RenderRiskLevel.MEDIUM
    else:
        level = RenderRiskLevel.LOW
    return WorldConsistencyReport(
        world_consistency_status=(
            "contradictory" if contradictions else "consistent"
        ),
        contradictions=contradictions,
        render_risk_level=level,
        render_risk_reasons=tuple(reasons),
        multi_clip_recommended=(
            level is RenderRiskLevel.HIGH
            and len({item.shot_order for item in plan.action_transitions}) > 1
        ),
    )
