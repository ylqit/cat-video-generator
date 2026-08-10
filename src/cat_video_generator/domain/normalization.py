"""导演输出的最小无损归一化。

该模块只修复供应商常见的等价结构差异，不再推断剧情、物理状态或视觉资产。
任何会改变导演意图的内容都交由契约明确拒绝并由创作台重新规划。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_ACTOR_ALIASES = {
    "child": "person",
    "kid": "person",
    "human": "person",
    "character": "person",
    "main_character": "person",
    "人物": "person",
    "孩子": "person",
    "儿童": "person",
    "小孩": "person",
    "cat": "cat",
    "kitten": "cat",
    "gray_white_cat": "cat",
    "grey_white_cat": "cat",
    "猫": "cat",
    "猫咪": "cat",
    "灰白猫": "cat",
    "environment": "environment",
    "scene": "environment",
    "环境": "environment",
}


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
    actions = normalized.get("actions")
    if isinstance(actions, list):
        for index, action in enumerate(actions, 1):
            if not isinstance(action, dict):
                continue
            actor_id = action.get("actor_id")
            if not isinstance(actor_id, str):
                continue
            canonical = _ACTOR_ALIASES.get(actor_id.strip().lower())
            if canonical is not None and canonical != actor_id:
                action["actor_id"] = canonical
                warnings.append(f"actions[{index}].actor_id由{actor_id}归一化为{canonical}")
    return normalized, tuple(warnings)
