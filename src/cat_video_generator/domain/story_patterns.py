"""剧情模式契约：蘑菇秃秃式多样化剧情事件的结构骨架。

模式是数据不是模板：一天三个时段抽签三个不同模式，导演按当天主题改编
节拍骨架而非照抄示例。新增剧情类型只改 content/events/patterns.yaml，
不改任何代码。
"""

from __future__ import annotations

from pydantic import Field

from .contract_base import StrictModel
from .contracts import Slot


class StoryPattern(StrictModel):
    """一种剧情类型的节拍骨架与可选幽默机制。"""

    pattern_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")
    name: str = Field(min_length=2, max_length=40)
    structure: tuple[str, ...] = Field(min_length=3, max_length=5)
    example_themes: tuple[str, ...] = Field(min_length=2, max_length=3)
    humor_mechanisms: tuple[str, ...] = Field(default_factory=tuple, max_length=4)
    prop_hints: tuple[str, ...] = Field(default_factory=tuple, max_length=6)
    suitable_slots: tuple[Slot, ...] = Field(default_factory=tuple, max_length=3)
    mood: str = Field(min_length=2, max_length=20)
