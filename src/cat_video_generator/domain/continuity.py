"""紧凑可见世界契约与确定性状态重放。

本模块只检查当前Episode明确登记的实体。它不预测Seedance难度，也不读取数据库；
未知实体、状态断链、无支撑、容器失活和无原因变化会被拒绝，复杂动作只由上层诊断。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .contract_base import StrictModel

EntityType = Literal[
    "person",
    "cat",
    "guest",
    "clothing",
    "prop",
    "food",
    "container",
    "furniture",
    "environment",
]
AnchorType = Literal[
    "ground",
    "table",
    "seat",
    "shelf",
    "counter",
    "wall",
    "window",
    "vehicle",
    "other",
]


class DominantView(StrEnum):
    """镜头主要展示的主体视角。"""

    FRONT = "front"
    SIDE = "side"
    BACK = "back"
    MIXED = "mixed"


class WorldAnchor(StrictModel):
    """场景中稳定存在的承重面或空间位置。"""

    id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    type: AnchorType


class EntityState(StrictModel):
    """一个实体在动作边界上的完整状态快照。

    所有字段均要求导演显式给出，包括 ``None``。完整快照使状态重放不必猜测
    “没写”究竟表示保持不变还是遗失信息。
    """

    anchor_id: str | None
    support_id: str | None
    container_id: str | None
    active: bool
    appearance_signature: Annotated[str, Field(min_length=1, max_length=220)]

    @model_validator(mode="after")
    def validate_inactive_state(self) -> EntityState:
        if not self.active and any((self.anchor_id, self.support_id, self.container_id)):
            raise ValueError("非活动实体不能继续占用锚点、支撑或容器")
        return self


class VisibleEntity(StrictModel):
    """会被操作、承重、包含或跨镜头持续出现的唯一实体实例。"""

    id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    type: EntityType
    semantic_key: Annotated[
        str,
        Field(pattern=r"^[a-z][a-z0-9_]*:[a-z0-9][a-z0-9_-]{0,119}$"),
    ] | None = None
    initial_state: EntityState


class EntityTransition(StrictModel):
    """一个动作中某个实体的完整前后状态；无变化时不创建本对象。"""

    entity_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]
    before: EntityState
    after: EntityState
    reason: Annotated[str, Field(min_length=2, max_length=180)]

    @model_validator(mode="after")
    def require_real_change(self) -> EntityTransition:
        if self.before == self.after:
            raise ValueError("没有状态变化的动作不能创建空Transition")
        return self


class VisibleWorld(StrictModel):
    """一条Episode的初始世界；动作变化保存在ActionStage中。"""

    anchors: list[WorldAnchor] = Field(default_factory=list, max_length=12)
    entities: list[VisibleEntity] = Field(min_length=2, max_length=20)

    @model_validator(mode="after")
    def validate_unique_objects(self) -> VisibleWorld:
        anchor_ids = [item.id for item in self.anchors]
        entity_ids = [item.id for item in self.entities]
        entity_names = [item.name for item in self.entities]
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("VisibleWorld锚点ID不能重复")
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("VisibleWorld实体ID不能重复")
        if len(entity_names) != len(set(entity_names)):
            raise ValueError("VisibleWorld实体名称不能重复")
        if entity_ids.count("person") != 1 or entity_ids.count("cat") != 1:
            raise ValueError("VisibleWorld必须各包含一个person和cat实体")
        return self


@dataclass(frozen=True, slots=True)
class ReplayIssue:
    """状态重放发现的一个确定性矛盾。"""

    code: str
    message: str
    action_order: int | None = None


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """统一终态与矛盾列表，供规划、Prompt和查询复用。"""

    states: dict[str, EntityState]
    issues: tuple[ReplayIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.issues


def replay_world(
    world: VisibleWorld,
    actions: list[object] | tuple[object, ...],
) -> ReplayResult:
    """按动作顺序重放完整快照，不通过回填掩盖导演遗漏。

    ``actions``只要求暴露 ``order``、``actor_id`` 和 ``transitions``，避免领域模块
    之间形成循环导入。
    """

    anchors = {item.id: item for item in world.anchors}
    entities = {item.id: item for item in world.entities}
    states = {item.id: item.initial_state.model_copy(deep=True) for item in world.entities}
    issues: list[ReplayIssue] = []
    _validate_all_states(states, anchors, entities, issues, action_order=None)

    for action in sorted(actions, key=lambda item: int(item.order)):
        actor_id = str(action.actor_id)
        if actor_id != "environment" and actor_id not in entities:
            issues.append(
                ReplayIssue("unknown_actor", f"动作主体{actor_id}未在可见世界建账", action.order)
            )
        elif actor_id in states and not states[actor_id].active:
            issues.append(
                ReplayIssue("inactive_actor", f"动作主体{actor_id}已离场", action.order)
            )

        changed_ids: set[str] = set()
        for transition in action.transitions:
            entity_id = transition.entity_id
            if entity_id in changed_ids:
                issues.append(
                    ReplayIssue(
                        "duplicate_transition",
                        f"动作{action.order}重复修改实体{entity_id}",
                        action.order,
                    )
                )
                continue
            changed_ids.add(entity_id)
            if entity_id not in states:
                issues.append(
                    ReplayIssue(
                        "unknown_entity",
                        f"动作{action.order}引用未建账实体{entity_id}",
                        action.order,
                    )
                )
                continue
            current = states[entity_id]
            if transition.before != current:
                issues.append(
                    ReplayIssue(
                        "before_state_mismatch",
                        f"动作{action.order}的{entity_id}.before与前序终态不一致",
                        action.order,
                    )
                )
                continue
            states[entity_id] = transition.after.model_copy(deep=True)

        _validate_all_states(states, anchors, entities, issues, action_order=action.order)

    return ReplayResult(states=states, issues=tuple(issues))


def require_consistent_world(
    world: VisibleWorld,
    actions: list[object] | tuple[object, ...],
) -> dict[str, EntityState]:
    """返回合法终态；存在矛盾时以稳定业务异常拒绝规划。"""

    result = replay_world(world, actions)
    if result.issues:
        raise ValueError("；".join(item.message for item in result.issues))
    return result.states


def _validate_all_states(
    states: dict[str, EntityState],
    anchors: dict[str, WorldAnchor],
    entities: dict[str, VisibleEntity],
    issues: list[ReplayIssue],
    *,
    action_order: int | None,
) -> None:
    known_ids = set(anchors) | set(entities)
    for entity_id, state in states.items():
        if not state.active:
            continue
        if state.anchor_id is not None and state.anchor_id not in anchors:
            issues.append(
                ReplayIssue(
                    "unknown_anchor",
                    f"实体{entity_id}引用未知锚点{state.anchor_id}",
                    action_order,
                )
            )
        if state.support_id is not None and state.support_id not in known_ids:
            issues.append(
                ReplayIssue(
                    "unknown_support",
                    f"实体{entity_id}引用未知支撑{state.support_id}",
                    action_order,
                )
            )
        if state.container_id is not None and state.container_id not in entities:
            issues.append(
                ReplayIssue(
                    "unknown_container",
                    f"实体{entity_id}引用未知容器{state.container_id}",
                    action_order,
                )
            )
        if state.support_id == entity_id or state.container_id == entity_id:
            issues.append(
                ReplayIssue("self_reference", f"实体{entity_id}不能支撑或包含自身", action_order)
            )
        if state.support_id in states and not states[state.support_id].active:
            issues.append(
                ReplayIssue(
                    "inactive_support",
                    f"实体{entity_id}仍由已离场实体{state.support_id}支撑",
                    action_order,
                )
            )
        if state.container_id in states and not states[state.container_id].active:
            issues.append(
                ReplayIssue(
                    "inactive_container",
                    f"实体{entity_id}仍位于已离场容器{state.container_id}内",
                    action_order,
                )
            )
        if not any((state.anchor_id, state.support_id, state.container_id)):
            issues.append(
                ReplayIssue(
                    "unsupported_entity",
                    f"活动实体{entity_id}没有锚点、支撑或容器",
                    action_order,
                )
            )
