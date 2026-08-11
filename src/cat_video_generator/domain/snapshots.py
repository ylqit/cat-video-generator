"""WorkflowStep三类输入快照的严格契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, TypeAdapter

from .contract_base import StrictModel
from .rendering import VideoInputPlan


class DirectorInputSnapshot(StrictModel):
    type: Literal["director"] = "director"
    phase: Literal["project_outline", "episode", "connection"]
    slot: Literal["morning", "noon", "evening"] | None = None
    prompt_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    output_contract: Literal["ProjectOutlineV3", "EpisodeScript", "ConnectionSuggestion"]
    repair_of_step_id: UUID | None = None
    response_id: str | None = None
    request_hash: str | None = None
    provider_output: dict[str, object] | None = None
    normalized_output: dict[str, object] | None = None
    normalization_warnings: tuple[str, ...] = ()

    @property
    def effective_output(self) -> dict[str, object] | None:
        """业务层始终读取归一化结果；没有改写时直接使用供应商原始对象。"""

        return self.normalized_output or self.provider_output


class ImageInputSnapshot(StrictModel):
    type: Literal["image"] = "image"
    target: Literal["look", "opening_anchor"]
    prompt_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    reference_asset_ids: tuple[UUID, ...]
    reference_sha256: tuple[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")], ...]
    retry_of_step_id: UUID | None = None
    retry_reason: str | None = None
    provider_task_status: str | None = None
    request_timeout_seconds: float | None = None
    duplicate_billing_risk_accepted: bool = False


class VideoInputSnapshot(StrictModel):
    type: Literal["video"] = "video"
    prompt_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    input_plan: VideoInputPlan
    input_asset_ids: tuple[UUID, ...]
    render_section_order: Annotated[int, Field(ge=0, le=3)]
    sequence_id: UUID | None = None
    selection_start_ms: int | None = None
    selection_end_ms: int | None = None
    source_start_ms: int | None = None
    source_end_ms: int | None = None
    edit_instruction: str | None = None
    retry_of_step_id: UUID | None = None
    retry_reason: str | None = None
    api_request_timeout_seconds: float | None = None
    task_timeout_seconds: float | None = None
    poll_interval_seconds: float | None = None
    provider_task_status: str | None = None
    polling_window_ended_at: datetime | None = None
    reconciliation_candidates: tuple[dict[str, object | None], ...] = ()
    reconciliation_queried_at: datetime | None = None
    reconciled_provider_task_id: str | None = None
    reconciled_at: datetime | None = None
    local_recovery_started_at: datetime | None = None
    media_qc: dict[str, object] | None = None


StepInputSnapshot = Annotated[
    DirectorInputSnapshot | ImageInputSnapshot | VideoInputSnapshot,
    Field(discriminator="type"),
]
_ADAPTER = TypeAdapter(StepInputSnapshot)


def validate_input_snapshot(value: object) -> StepInputSnapshot:
    return _ADAPTER.validate_python(value)
