"""WorkflowStep三类输入快照的严格契约。

快照只保存幂等恢复所需事实，不承载任意业务JSON。Prompt正文另存于prompt_records，
模型和operation_key使用关系列，避免同一字段重复。
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, TypeAdapter

from .contract_base import StrictModel
from .rendering import VideoInputPlan


class DirectorInputSnapshot(StrictModel):
    type: Literal["director"] = "director"
    phase: Literal["day", "episode"]
    slot: Literal["morning", "noon", "evening"] | None = None
    prompt_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    output_contract: Literal["DayBrief", "EpisodeScript"]
    repair_of_step_id: UUID | None = None
    response_id: str | None = None
    request_hash: str | None = None
    output: dict[str, object] | None = None


class ImageInputSnapshot(StrictModel):
    type: Literal["image"] = "image"
    target: Literal["storyboard"]
    expected_panel_count: Annotated[int, Field(ge=3, le=4)]
    prompt_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    reference_asset_ids: tuple[UUID, ...]
    reference_sha256: tuple[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")], ...]
    retry_of_step_id: UUID | None = None
    retry_reason: str | None = None
    provider_task_status: str | None = None


class VideoInputSnapshot(StrictModel):
    type: Literal["video"] = "video"
    prompt_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    input_plan: VideoInputPlan
    input_asset_ids: tuple[UUID, ...]
    retry_of_step_id: UUID | None = None
    retry_reason: str | None = None
    provider_task_status: str | None = None


StepInputSnapshot = Annotated[
    DirectorInputSnapshot | ImageInputSnapshot | VideoInputSnapshot,
    Field(discriminator="type"),
]
_ADAPTER = TypeAdapter(StepInputSnapshot)


def validate_input_snapshot(value: object) -> StepInputSnapshot:
    """在Repository写入前拒绝任意键堆积。"""

    return _ADAPTER.validate_python(value)
