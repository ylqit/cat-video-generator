"""轻量事件种子目录读取与确定性筛选。

事件种子只提供生活方向，不包含完整剧情。目录缺失或没有匹配项时返回空集合，
导演仍可原创，因此本模块不会成为规划单点故障。
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..domain.contracts import Slot


class EventSeed(BaseModel):
    """可按时段与环境筛选的单个生活灵感。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    seed_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")
    direction: str = Field(min_length=4, max_length=200)
    slots: list[Slot] = Field(min_length=1, max_length=3)
    weather_tags: list[str] = Field(default_factory=list, max_length=8)
    context_tags: list[str] = Field(default_factory=list, max_length=8)


class EventSeedCatalog:
    """读取版本化YAML并按业务键稳定选出最多三个候选。"""

    def __init__(self, root: Path) -> None:
        self._root = root

    def select(
        self,
        *,
        series_profile_hash: str,
        content_date: date,
        planning_revision: int,
        planning_context: str,
        limit: int = 3,
    ) -> tuple[EventSeed, ...]:
        if not 1 <= limit <= 3:
            raise ValueError("事件种子最多选择3项")
        context = planning_context.casefold()
        eligible = [
            seed
            for seed in self._load()
            if not seed.weather_tags and not seed.context_tags
            or any(tag.casefold() in context for tag in (*seed.weather_tags, *seed.context_tags))
        ]
        key = (
            f"{series_profile_hash}|{content_date.isoformat()}|{planning_revision}"
        )
        ranked = sorted(
            eligible,
            key=lambda item: hashlib.sha256(
                f"{key}|{item.seed_id}".encode("utf-8")
            ).hexdigest(),
        )
        # 先覆盖尽可能多的不同时段，再用稳定哈希顺序补足。这样slots真正参与
        # 选择，而不是只作为写进YAML却从未生效的装饰字段。
        selected: list[EventSeed] = []
        covered: set[Slot] = set()
        for seed in ranked:
            if len(selected) >= limit:
                break
            if not set(seed.slots).issubset(covered):
                selected.append(seed)
                covered.update(seed.slots)
        for seed in ranked:
            if len(selected) >= limit:
                break
            if seed not in selected:
                selected.append(seed)
        return tuple(selected)

    def _load(self) -> tuple[EventSeed, ...]:
        if not self._root.is_dir():
            return ()
        result: list[EventSeed] = []
        for path in sorted(self._root.glob("*.yaml")):
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            if payload is None:
                continue
            items = payload if isinstance(payload, list) else payload.get("events", [])
            result.extend(EventSeed.model_validate(item) for item in items)
        ids = [item.seed_id for item in result]
        if len(ids) != len(set(ids)):
            raise ValueError("content/events中的seed_id不能重复")
        return tuple(result)
