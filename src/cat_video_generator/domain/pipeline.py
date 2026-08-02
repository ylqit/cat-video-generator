"""主题创作台流水线的阶段开关契约。

每个Run持有一份 ``PipelineSettings``：四个阶段（日导演/剧本/首末帧/视频）
各自独立为 auto（自动推进）或 manual（停顿等待人工编辑/确认）。
``allow_paid_generation`` 在提交主题时一次收齐并持久化，是后续所有
自动续跑（含关键帧人工批准后的视频续跑钩子）的唯一付费依据；
历史Run没有设置列内容时按 ``legacy_default`` 读取——阶段全开但
付费授权绝不为真，保证旧Run永远不会被自动扣费。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field

from .contract_base import StrictModel


class StageMode(StrEnum):
    """单个流水线阶段的推进方式。"""

    AUTO = "auto"
    MANUAL = "manual"


PIPELINE_STAGES = ("dayBrief", "script", "keyframes", "video")


class PipelineSettings(StrictModel):
    """Run级流水线开关；默认全自动（一键直出）。"""

    allow_paid_generation: Annotated[bool, Field(alias="allowPaidGeneration")] = False
    day_brief: Annotated[StageMode, Field(alias="dayBrief")] = StageMode.AUTO
    script: StageMode = StageMode.AUTO
    keyframes: StageMode = StageMode.AUTO
    video: StageMode = StageMode.AUTO

    @classmethod
    def legacy_default(cls) -> PipelineSettings:
        """历史Run（settings列为NULL）的兼容读法：阶段全开、绝不自动扣费。"""

        return cls()

    def stage(self, name: str) -> StageMode:
        """按 PIPELINE_STAGES 名称取阶段模式，未知名称直接报错。"""

        aliases = {"dayBrief": "day_brief"}
        attribute = aliases.get(name, name)
        if attribute not in {"day_brief", "script", "keyframes", "video"}:
            raise ValueError(f"未知流水线阶段: {name!r}")
        return getattr(self, attribute)
