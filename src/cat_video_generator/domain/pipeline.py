"""生产工作台五阶段的自动/人工推进设置。"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field

from .contract_base import StrictModel


class StageMode(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


PIPELINE_STAGES = ("dayBrief", "script", "visual", "video", "review")


class PipelineSettings(StrictModel):
    allow_paid_generation: Annotated[bool, Field(alias="allowPaidGeneration")] = False
    day_brief: Annotated[StageMode, Field(alias="dayBrief")] = StageMode.AUTO
    script: StageMode = StageMode.AUTO
    visual: StageMode = StageMode.AUTO
    video: StageMode = StageMode.AUTO
    review: StageMode = StageMode.MANUAL

    def stage(self, name: str) -> StageMode:
        aliases = {"dayBrief": "day_brief"}
        attribute = aliases.get(name, name)
        if attribute not in {"day_brief", "script", "visual", "video", "review"}:
            raise ValueError(f"未知流水线阶段：{name!r}")
        return getattr(self, attribute)
