"""关键可见实体的轻量起终态连续性契约。

本模块只记录人物、猫咪和真正影响剧情结果的关键道具。停步、转头、蹲下、
嗅闻等瞬时姿态属于导演动作，不进入状态模型；普通植物、远山、光影等背景也
不建账。这里不读取数据库、不解析剧情文本，也不审核实际媒体。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .contract_base import StrictModel

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


class PlacementKind(StrEnum):
    """关键实体起点或终点的最小位置关系。"""

    ANCHOR = "anchor"
    HELD_BY = "held_by"
    INSIDE = "inside"
    OFFSCREEN = "offscreen"


class EntityKind(StrEnum):
    """连续性账本中的实体类别。"""

    PERSON = "person"
    CAT = "cat"
    PROP = "prop"


class EntityLifecycle(StrEnum):
    """实体从开场到结尾的可见生命周期。"""

    PERSIST = "persist"
    ENTER = "enter"
    EXIT = "exit"
    CONSUME = "consume"
    TRANSFORM = "transform"


class SceneAnchor(StrictModel):
    """确实参与坐靠、承重或放置的稳定场景位置。"""

    id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    type: AnchorType


class Placement(StrictModel):
    """实体位置；目标的具体类型由 ``SceneContinuity`` 统一校验。

    ``inside`` 既可以表示容器实体内部，也可以表示长椅缝隙、柜格等场景锚点
    内部。这里仅校验有没有目标，避免底层字段模型擅自猜测场景语义。
    """

    kind: PlacementKind
    target_id: (
        Annotated[
            str,
            Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$"),
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def validate_target(self) -> Placement:
        if self.kind is PlacementKind.OFFSCREEN and self.target_id is not None:
            raise ValueError("offscreen位置不能携带targetId")
        if self.kind is not PlacementKind.OFFSCREEN and self.target_id is None:
            raise ValueError(f"{self.kind.value}位置必须提供targetId")
        return self


class EntityState(StrictModel):
    """实体在Episode开场或结尾的可见位置。"""

    present: bool
    placement: Placement

    @model_validator(mode="after")
    def validate_presence(self) -> EntityState:
        if self.present == (self.placement.kind is PlacementKind.OFFSCREEN):
            raise ValueError("present实体必须有可见位置，非present实体必须offscreen")
        return self


_POSE_TOKENS = {
    "crouch",
    "crouching",
    "sniff",
    "sniffing",
    "walk",
    "walking",
    "run",
    "running",
    "sit",
    "sitting",
    "stand",
    "standing",
    "turn",
    "turning",
    "look",
    "looking",
}


class TrackedEntity(StrictModel):
    """需要从开场保持到结尾的关键角色或道具。

    ``form_key``只表达稳定类别或外观，不表达蹲下、走动等姿态。起终位置允许
    相同；这正是绝大多数生活观察片中的正常持续状态。
    """

    id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    kind: EntityKind
    entity_key: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")]
    start_state: EntityState
    end_state: EntityState
    lifecycle: EntityLifecycle = EntityLifecycle.PERSIST
    form_key: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")]
    final_form_key: (
        Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")] | None
    ) = None
    change_reason: Annotated[str, Field(min_length=2, max_length=180)] | None = None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> TrackedEntity:
        if self.kind is EntityKind.PERSON and self.id != "person":
            raise ValueError("person实体ID必须为person")
        if self.kind is EntityKind.CAT and self.id != "cat":
            raise ValueError("cat实体ID必须为cat")
        if self.kind is EntityKind.PROP and self.id in {"person", "cat"}:
            raise ValueError("prop实体不能使用person或cat ID")
        if self.kind in {EntityKind.PERSON, EntityKind.CAT} and (
            self.lifecycle is not EntityLifecycle.PERSIST
        ):
            raise ValueError("人物和猫咪生命周期必须为persist")

        pose_tokens = set(re.split(r"[_-]+", self.form_key.casefold()))
        if pose_tokens & _POSE_TOKENS:
            raise ValueError("formKey只能表达稳定类别或外观，不能包含瞬时姿态")

        start_present = self.start_state.present
        end_present = self.end_state.present
        if self.lifecycle is EntityLifecycle.PERSIST:
            if not start_present or not end_present:
                raise ValueError("persist实体必须在开场和结尾都存在")
            if self.final_form_key is not None:
                raise ValueError("persist实体不能声明finalFormKey")
        elif self.lifecycle is EntityLifecycle.ENTER:
            if start_present or not end_present:
                raise ValueError("enter实体必须从离屏进入可见画面")
        elif self.lifecycle in {EntityLifecycle.EXIT, EntityLifecycle.CONSUME}:
            if not start_present or end_present:
                raise ValueError(f"{self.lifecycle.value}实体必须从可见变为离屏")
        elif self.lifecycle is EntityLifecycle.TRANSFORM:
            if not start_present or not end_present:
                raise ValueError("transform实体在开场和结尾都必须存在")
            if not self.final_form_key or self.final_form_key == self.form_key:
                raise ValueError("transform实体必须提供不同的finalFormKey")

        if self.lifecycle is not EntityLifecycle.TRANSFORM and self.final_form_key is not None:
            raise ValueError("只有transform实体可以声明finalFormKey")
        if self.lifecycle is not EntityLifecycle.PERSIST and not self.change_reason:
            raise ValueError(f"{self.lifecycle.value}实体必须说明变化原因")
        return self


class SceneContinuity(StrictModel):
    """一个Episode的关键实体起终态与真实交互锚点。"""

    anchors: list[SceneAnchor] = Field(default_factory=list, max_length=10)
    entities: list[TrackedEntity] = Field(min_length=2, max_length=14)

    @model_validator(mode="after")
    def validate_unique_objects(self) -> SceneContinuity:
        anchor_ids = [item.id for item in self.anchors]
        entity_ids = [item.id for item in self.entities]
        entity_names = [item.name for item in self.entities]
        entity_keys = [item.entity_key for item in self.entities]
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("SceneContinuity锚点ID不能重复")
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("SceneContinuity实体ID不能重复")
        if len(entity_names) != len(set(entity_names)):
            raise ValueError("SceneContinuity实体名称不能重复")
        if len(entity_keys) != len(set(entity_keys)):
            raise ValueError("SceneContinuity实体entityKey不能重复")
        if sum(item.kind is EntityKind.PERSON for item in self.entities) != 1:
            raise ValueError("SceneContinuity必须包含一个person实体")
        if sum(item.kind is EntityKind.CAT for item in self.entities) != 1:
            raise ValueError("SceneContinuity必须包含一个cat实体")
        anchor_set = set(anchor_ids)
        entity_set = set(entity_ids)
        for entity in self.entities:
            states = (("startState", entity.start_state), ("endState", entity.end_state))
            for label, state in states:
                target_id = state.placement.target_id
                if state.placement.kind is PlacementKind.ANCHOR and target_id not in anchor_set:
                    raise ValueError(f"实体{entity.id}的{label}引用未知锚点{target_id}")
                if state.placement.kind is PlacementKind.HELD_BY:
                    if target_id not in entity_set:
                        raise ValueError(f"实体{entity.id}的{label}引用未知实体{target_id}")
                    if target_id == entity.id:
                        raise ValueError(f"实体{entity.id}不能持有自身")
                if state.placement.kind is PlacementKind.INSIDE:
                    if target_id not in entity_set | anchor_set:
                        raise ValueError(
                            f"实体{entity.id}的{label}引用未知容器或锚点{target_id}"
                        )
                    if target_id == entity.id:
                        raise ValueError(f"实体{entity.id}不能包含自身")
        return self


@dataclass(frozen=True, slots=True)
class ContinuityIssue:
    """轻量连续性检查发现的确定性矛盾。"""

    code: str
    message: str
    entity_id: str | None = None


@dataclass(frozen=True, slots=True)
class ContinuityResult:
    """关键实体合法结尾状态与矛盾列表。"""

    final_states: dict[str, EntityState]
    issues: tuple[ContinuityIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.issues


def validate_continuity(continuity: SceneContinuity) -> ContinuityResult:
    """检查起终位置引用，不模拟中间姿态或物理运动。"""

    anchors = {item.id: item for item in continuity.anchors}
    entities = {item.id: item for item in continuity.entities}
    issues: list[ContinuityIssue] = []
    for phase in ("start", "end"):
        states = {
            item.id: item.start_state if phase == "start" else item.end_state
            for item in continuity.entities
        }
        for entity_id, state in states.items():
            _validate_state_reference(
                entity_id,
                state,
                phase=phase,
                states=states,
                anchors=anchors,
                entities=entities,
                issues=issues,
            )
    return ContinuityResult(
        final_states={item.id: item.end_state for item in continuity.entities},
        issues=tuple(issues),
    )


def require_consistent_scene(continuity: SceneContinuity) -> dict[str, EntityState]:
    """返回合法结尾状态；存在矛盾时以稳定业务异常拒绝规划。"""

    result = validate_continuity(continuity)
    if result.issues:
        raise ValueError("；".join(item.message for item in result.issues))
    return result.final_states


def _validate_state_reference(
    entity_id: str,
    state: EntityState,
    *,
    phase: str,
    states: dict[str, EntityState],
    anchors: dict[str, SceneAnchor],
    entities: dict[str, TrackedEntity],
    issues: list[ContinuityIssue],
) -> None:
    if not state.present:
        return
    target_id = state.placement.target_id
    if state.placement.kind is PlacementKind.ANCHOR and target_id not in anchors:
        issues.append(
            ContinuityIssue(
                "unknown_anchor",
                f"实体{entity_id}的{phase}State引用未知锚点{target_id}",
                entity_id,
            )
        )
        return
    if state.placement.kind is PlacementKind.INSIDE and target_id in anchors:
        return
    if state.placement.kind not in {PlacementKind.HELD_BY, PlacementKind.INSIDE}:
        return
    if target_id not in entities:
        issues.append(
            ContinuityIssue(
                "unknown_placement_target",
                f"实体{entity_id}的{phase}State引用未知容器、持有者或锚点{target_id}",
                entity_id,
            )
        )
    elif target_id == entity_id:
        issues.append(
            ContinuityIssue(
                "self_reference",
                f"实体{entity_id}不能包含或持有自身",
                entity_id,
            )
        )
    elif not states[target_id].present:
        issues.append(
            ContinuityIssue(
                "inactive_placement_target",
                f"实体{entity_id}的{phase}State依附于离屏实体{target_id}",
                entity_id,
            )
        )
