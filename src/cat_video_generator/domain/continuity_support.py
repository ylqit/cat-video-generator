"""可见世界状态重放的底层转换与关系检查。

本模块只实现状态机机械规则；公开契约和业务入口仍由``continuity.py``持有。
拆分目的是隔离容易出错的支撑、包含和切镜继承细节，而不是增加一层转发包装。
"""

from __future__ import annotations

from .continuity import (
    ActionTransition,
    EntityLifecycle,
    SceneAnchor,
    ShotBoundaryState,
    TopologyChange,
    TrackedEntity,
    VisibleWorldPlan,
    _EntityState,
)


def validate_topology_transition(
    transition: ActionTransition,
    *,
    anchors: dict[str, SceneAnchor],
    states: dict[str, _EntityState],
    issues: list[str],
) -> None:
    seat_id = transition.seat_anchor_id or ""
    seat = anchors.get(seat_id)
    if seat is None or seat.anchor_type != "seat":
        issues.append("人物坐下或站起必须引用seat类型的可坐表面")
        return
    actor_state = states.get(transition.actor_id)
    if actor_state is None:
        return
    if transition.actor_id not in transition.target_entity_ids:
        issues.append("坐下或站起动作必须把动作主体列为目标实体")
    if transition.topology_change is TopologyChange.SIT:
        if actor_state.anchor_id == seat_id:
            issues.append(f"{transition.actor_id}已经位于座面，不能重复坐下")
        if transition.after_anchor_id not in {None, seat_id}:
            issues.append("坐下动作结束位置必须是声明的seat锚点")
    elif transition.topology_change is TopologyChange.STAND:
        if actor_state.anchor_id != seat_id:
            issues.append(f"{transition.actor_id}站起前并未位于声明的seat锚点")
        if transition.after_anchor_id in {None, seat_id}:
            issues.append("站起动作必须声明离开座面后的合法位置")


def check_before_state(
    transition: ActionTransition,
    entity_id: str,
    state: _EntityState,
    issues: list[str],
) -> None:
    expected = (
        (
            "位置",
            "before_anchor_id",
            "after_anchor_id",
            transition.before_anchor_id,
            transition.after_anchor_id,
            state.anchor_id,
        ),
        (
            "支撑",
            "support_before",
            "support_after",
            transition.support_before,
            transition.support_after,
            state.support_id,
        ),
        (
            "接触",
            "contact_before",
            "contact_after",
            transition.contact_before,
            transition.contact_after,
            state.contact_id,
        ),
        (
            "包含",
            "containment_before",
            "containment_after",
            transition.containment_before,
            transition.containment_after,
            state.container_id,
        ),
    )
    fields_set = transition.model_fields_set
    for label, before_field, after_field, declared, after, current in expected:
        before_set = before_field in fields_set
        after_set = after_field in fields_set
        if not before_set:
            if (
                after_set
                and after != current
                and transition.lifecycle_event is not EntityLifecycle.ENTER
            ):
                issues.append(f"{entity_id}动作前{label}状态缺失，不能从当前状态安全推导")
            continue
        if declared is None and current is not None:
            issues.append(
                f"{entity_id}动作前{label}声明为空，与上一状态{current!r}不一致"
            )
        elif declared is not None and current is None:
            issues.append(f"{entity_id}动作前{label}{declared!r}没有可继承的上一状态")
        elif declared != current:
            issues.append(
                f"{entity_id}动作前{label}{declared!r}与上一状态{current!r}不一致"
            )


def apply_lifecycle(
    transition: ActionTransition,
    entity: TrackedEntity,
    state: _EntityState,
    issues: list[str],
) -> None:
    event = transition.lifecycle_event
    if event in {None, EntityLifecycle.PERSIST}:
        return
    if event is EntityLifecycle.ENTER:
        if state.active:
            issues.append(f"{entity.entity_id}已经存在，不能再次enter")
        state.active = True
    elif event in {EntityLifecycle.EXIT, EntityLifecycle.CONSUME}:
        if not state.active:
            issues.append(f"{entity.entity_id}已经离场，不能再次{event.value}")
        state.active = False
        state.anchor_id = None
        state.support_id = None
        state.contact_id = None
        state.container_id = None
    elif event is EntityLifecycle.TRANSFORM:
        state.appearance_signature = (
            transition.resulting_appearance_signature
            or state.appearance_signature
        )


def apply_after_state(
    transition: ActionTransition,
    state: _EntityState,
) -> None:
    fields_set = transition.model_fields_set
    if "after_anchor_id" in fields_set:
        state.anchor_id = transition.after_anchor_id
    if "support_after" in fields_set:
        state.support_id = transition.support_after
    if "contact_after" in fields_set:
        state.contact_id = transition.contact_after
    if "containment_after" in fields_set:
        state.container_id = transition.containment_after


def resolve_transition_state_entity_id(
    transition: ActionTransition,
    states: dict[str, object] | None = None,
) -> str | None:
    """只把已建账实体解析为状态主体，场景锚点永远不能充当实体。"""

    if transition.state_entity_id is not None:
        return transition.state_entity_id
    if states is None:
        return (
            transition.actor_id
            if transition.actor_id in transition.target_entity_ids
            else None
        )
    if (
        transition.actor_id in states
        and transition.actor_id in transition.target_entity_ids
    ):
        return transition.actor_id
    return next(
        (
            entity_id
            for entity_id in transition.target_entity_ids
            if entity_id in states
        ),
        None,
    )


def transition_has_state_fields(transition: ActionTransition) -> bool:
    return any(
        (
            transition.state_entity_id is not None,
            transition.before_anchor_id is not None,
            transition.after_anchor_id is not None,
            transition.support_before is not None,
            transition.support_after is not None,
            transition.contact_before is not None,
            transition.contact_after is not None,
            transition.containment_before is not None,
            transition.containment_after is not None,
            transition.lifecycle_event not in {None, EntityLifecycle.PERSIST},
            transition.topology_change is not TopologyChange.NONE,
            transition.seat_anchor_id is not None,
            transition.resulting_appearance_signature is not None,
        )
    )


def remember_shot_state(
    shot_states: dict[int, dict[str, _EntityState]],
    shot_order: int,
    states: dict[str, _EntityState],
) -> None:
    shot_states[shot_order] = {
        key: _EntityState(
            anchor_id=value.anchor_id,
            support_id=value.support_id,
            contact_id=value.contact_id,
            container_id=value.container_id,
            active=value.active,
            appearance_signature=value.appearance_signature,
        )
        for key, value in states.items()
    }


def validate_relation_references(
    transition: ActionTransition,
    *,
    anchors: dict[str, SceneAnchor],
    states: dict[str, _EntityState],
    issues: list[str],
) -> None:
    for relation_name, relation_id in (
        ("支撑", transition.support_before),
        ("支撑", transition.support_after),
        ("接触", transition.contact_before),
        ("接触", transition.contact_after),
        ("容器", transition.containment_before),
        ("容器", transition.containment_after),
    ):
        if relation_id is None or relation_id in anchors:
            continue
        related = states.get(relation_id)
        if related is None:
            if relation_name == "接触":
                continue
            issues.append(f"动作引用了未建账{relation_name}{relation_id}")
        elif not related.active:
            issues.append(f"动作引用的{relation_name}{relation_id}已经离场")


def validate_no_dangling_relations(
    removed_id: str,
    *,
    states: dict[str, _EntityState],
    issues: list[str],
) -> None:
    for entity_id, state in states.items():
        if entity_id == removed_id or not state.active:
            continue
        if state.support_id == removed_id:
            issues.append(f"{removed_id}离场后{entity_id}仍由其支撑")
        if state.container_id == removed_id:
            issues.append(f"{removed_id}离场后{entity_id}仍位于其中")
        if state.contact_id == removed_id:
            issues.append(f"{removed_id}离场后{entity_id}仍与其接触")


def validate_boundaries(
    plan: VisibleWorldPlan,
    *,
    shot_states: dict[int, dict[str, _EntityState]],
    issues: list[str],
) -> None:
    boundaries: dict[tuple[int, int], ShotBoundaryState] = {
        (item.after_shot_order, item.next_shot_order): item
        for item in plan.shot_boundary_states
    }
    used_shots = sorted({item.shot_order for item in plan.action_transitions})
    for current, following in zip(used_shots, used_shots[1:], strict=False):
        if following == current + 1 and (current, following) not in boundaries:
            issues.append(f"镜头{current}到{following}缺少shotBoundaryState")

    # 即使后一镜头没有状态变化动作，导演声明的边界也必须核对。
    # 这能发现“切镜后位置回退”之类错误，而不是因缺少下一镜头动作被跳过。
    for boundary in plan.shot_boundary_states:
        state = shot_states.get(boundary.after_shot_order, {})
        for entity_id in boundary.visible_entity_ids:
            entity_state = state.get(entity_id)
            if entity_state is None or not entity_state.active:
                issues.append(f"切镜声明可见的{entity_id}在前一镜头并不存在")
        for entity_id, anchor_id in boundary.entity_anchor_ids.items():
            entity_state = state.get(entity_id)
            if entity_state is None or entity_state.anchor_id != anchor_id:
                actual = None if entity_state is None else entity_state.anchor_id
                issues.append(
                    f"切镜继承的{entity_id}位置{anchor_id!r}与前镜头{actual!r}不一致"
                )
