"""纯业务领域层。

该包只描述三时段内容、状态转换和 Prompt 规则，不依赖数据库、CLI、
FastAPI、Ark SDK 或本地文件系统。
"""

from .contracts import (
    ActionStage,
    AppearancePlan,
    CriticalRelation,
    DailyProductionPlan,
    EpisodePlan,
    MediaBinding,
    SharedElement,
    Slot,
    VideoInputMode,
    VideoInputPlan,
)

__all__ = [
    "ActionStage",
    "AppearancePlan",
    "CriticalRelation",
    "DailyProductionPlan",
    "EpisodePlan",
    "MediaBinding",
    "SharedElement",
    "Slot",
    "VideoInputMode",
    "VideoInputPlan",
]
