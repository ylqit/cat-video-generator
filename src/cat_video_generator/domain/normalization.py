"""导演输出的最小无损归一化。

该模块只修复供应商常见的等价结构差异，不再推断剧情、物理状态或视觉资产。
任何会改变导演意图的内容都交由契约明确拒绝并由创作台重新规划。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


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
