"""供应商结构化输出的确定性形状归一化。

本模块只修正可由既有字段唯一推导的层级和冗余标志，不删除实体、不改变动作，
也不绕过Pydantic与可见世界语义校验。
"""

from __future__ import annotations

from typing import Any


def normalize_episode_draft_payload(value: Any) -> Any:
    """归位镜头边界，并由动作—镜头映射推导连续镜头标志。"""

    if not isinstance(value, dict):
        return value
    migrated = dict(value)
    world = migrated.get("visible_world")
    if not isinstance(world, dict):
        return migrated
    world = dict(world)
    root_boundaries = migrated.pop("shot_boundary_states", None)
    if root_boundaries is not None and not world.get("shot_boundary_states"):
        world["shot_boundary_states"] = root_boundaries

    action_shots: dict[int, set[int]] = {}
    for shot in migrated.get("shots", []):
        if not isinstance(shot, dict):
            continue
        shot_order = shot.get("order")
        for action_order in shot.get("action_orders", []):
            if isinstance(action_order, int) and isinstance(shot_order, int):
                action_shots.setdefault(action_order, set()).add(shot_order)

    transitions: list[Any] = []
    anchors = world.get("scene_anchors", [])
    entities = world.get("tracked_entities", [])
    known_support_ids = _known_support_ids(anchors, entities)
    for item in world.get("action_transitions", []):
        if not isinstance(item, dict):
            transitions.append(item)
            continue
        transition = dict(item)
        action_order = transition.get("action_order")
        # 动作只属于一个镜头时，连续性由结构唯一确定，不采信模型的冗余布尔值。
        if isinstance(action_order, int) and len(action_shots.get(action_order, ())) == 1:
            transition["continuous_shot"] = True
        for field in ("support_before", "support_after"):
            support = transition.get(field)
            if isinstance(support, str) and support not in known_support_ids:
                resolved = _resolve_support_description(
                    support,
                    anchors=anchors,
                    entities=entities,
                )
                if resolved is not None:
                    transition[field] = resolved
        transitions.append(transition)
    if transitions:
        world["action_transitions"] = transitions
    migrated["visible_world"] = world
    return migrated


def _known_support_ids(anchors: object, entities: object) -> set[str]:
    values: set[str] = set()
    for collection, field in ((anchors, "anchor_id"), (entities, "entity_id")):
        if isinstance(collection, list):
            values.update(
                str(item[field])
                for item in collection
                if isinstance(item, dict) and item.get(field)
            )
    return values


def _resolve_support_description(
    description: str,
    *,
    anchors: object,
    entities: object,
) -> str | None:
    """仅解析唯一可确定的支撑ID；存在多个候选时交回契约硬门拒绝。"""

    collections = (
        (anchors if isinstance(anchors, list) else [], "anchor_id"),
        (entities if isinstance(entities, list) else [], "entity_id"),
    )
    named_matches = {
        str(item[id_field])
        for collection, id_field in collections
        for item in collection
        if isinstance(item, dict)
        and item.get(id_field)
        and item.get("display_name")
        and str(item["display_name"]) in description
    }
    if len(named_matches) == 1:
        return next(iter(named_matches))
    type_keywords = {
        "ground": ("地面", "地板"),
        "table": ("桌面", "桌"),
        "seat": ("座面", "座椅", "椅", "凳"),
        "shelf": ("窗台", "架", "搁板"),
        "counter": ("台面", "操作台", "柜台"),
    }
    matching_types = {
        anchor_type
        for anchor_type, keywords in type_keywords.items()
        if any(keyword in description for keyword in keywords)
    }
    type_matches = [
        str(item["anchor_id"])
        for item in collections[0][0]
        if isinstance(item, dict)
        and item.get("anchor_id")
        and item.get("anchor_type") in matching_types
    ]
    return type_matches[0] if len(type_matches) == 1 else None
