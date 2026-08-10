"""导演输出的最小无损归一化。

该模块只修复供应商常见的等价结构差异，不再推断剧情、物理状态或视觉资产。
任何会改变导演意图的内容都交由契约明确拒绝并由创作台重新规划。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def normalize_day_brief_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """去除完全相同的时段重复项，但不合并相互冲突的导演意图。

    Ark偶尔会在结构化数组中重复输出同一个对象。只有去重后严格恢复为
    morning/noon/evening三个时段时才接受；缺失时段、乱序或同一时段内容
    不一致仍由DayBrief契约拒绝，避免归一化悄悄改写剧情。
    """

    slot_briefs = payload.get("slot_briefs")
    if not isinstance(slot_briefs, list):
        return deepcopy(payload), ()

    seen: dict[str, dict[str, Any]] = {}
    deduplicated: list[Any] = []
    removed_slots: list[str] = []
    for item in slot_briefs:
        if not isinstance(item, dict) or not isinstance(item.get("slot"), str):
            deduplicated.append(item)
            continue
        slot = item["slot"]
        existing = seen.get(slot)
        if existing is None:
            seen[slot] = item
            deduplicated.append(item)
        elif item == existing:
            removed_slots.append(slot)
        else:
            # 冲突的重复项必须留给契约报错，不能擅自选择一个版本。
            deduplicated.append(item)

    deduplicated_slots = [
        item.get("slot") for item in deduplicated if isinstance(item, dict)
    ]
    if not removed_slots or deduplicated_slots != [
        "morning",
        "noon",
        "evening",
    ]:
        return deepcopy(payload), ()

    normalized = deepcopy(payload)
    normalized["slot_briefs"] = deepcopy(deduplicated)
    slots = "、".join(removed_slots)
    return normalized, (f"slot_briefs去除完全相同的重复时段：{slots}",)


def normalize_episode_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """返回可由新契约解析的副本以及发生过的机械归一化说明。"""

    normalized = deepcopy(payload)
    warnings: list[str] = []
    sound = normalized.get("sound_design")
    if isinstance(sound, list):
        normalized["sound_design"] = "；".join(
            text for item in sound if (text := str(item).strip())
        )
        warnings.append("sound_design由列表合并为字符串")
    return normalized, tuple(warnings)
