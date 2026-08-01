"""纯业务领域层。

该包只描述三时段内容、状态转换和 Prompt 规则，不依赖数据库、CLI、
FastAPI、Ark SDK 或本地文件系统。
"""

from .contracts import (
    ActionStage,
    AppearancePlan,
    DailyProductionPlan,
    EpisodePlan,
    EpisodeScript,
    SharedElement,
    Slot,
)
from .rendering import (
    MediaBinding,
    VideoInputMode,
    VideoInputPlan,
)

__all__ = [
    "ActionStage",
    "AppearancePlan",
    "DailyProductionPlan",
    "EpisodePlan",
    "EpisodeScript",
    "MediaBinding",
    "SharedElement",
    "Slot",
    "VideoInputMode",
    "VideoInputPlan",
]
