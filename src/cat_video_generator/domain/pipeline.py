"""生产工作台五阶段的自动/人工推进设置。"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field

from .contract_base import StrictModel


class StageMode(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


class PlanningMode(StrEnum):
    """全天脚本的推进方式；顺序模式是Web创作台的默认质量路径。"""

    GUIDED_SEQUENTIAL = "guided_sequential"
    AUTO_DAY = "auto_day"


PIPELINE_STAGES = ("projectOutline", "script", "visual", "video", "review")


class PipelineSettings(StrictModel):
    planning_mode: Annotated[
        PlanningMode,
        Field(alias="planningMode"),
    ] = PlanningMode.GUIDED_SEQUENTIAL
    allow_paid_generation: Annotated[bool, Field(alias="allowPaidGeneration")] = False
    project_outline: Annotated[StageMode, Field(alias="projectOutline")] = StageMode.AUTO
    script: StageMode = StageMode.AUTO
    visual: StageMode = StageMode.AUTO
    video: StageMode = StageMode.AUTO
    review: StageMode = StageMode.MANUAL

    def stage(self, name: str) -> StageMode:
        aliases = {"projectOutline": "project_outline"}
        attribute = aliases.get(name, name)
        if attribute not in {"project_outline", "script", "visual", "video", "review"}:
            raise ValueError(f"未知流水线阶段：{name!r}")
        return getattr(self, attribute)
