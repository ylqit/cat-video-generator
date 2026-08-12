"""HTTP request contracts for the V4 shot queue API."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..domain.contracts import ReferenceBinding, SceneDraft, ShotCardDraft, StoryProjectInput


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)


class CreateProjectRequest(ApiModel):
    project: StoryProjectInput
    content_date: date | None = Field(default=None, alias="contentDate")


class UpdateProjectRequest(ApiModel):
    title: Annotated[str, Field(min_length=1, max_length=160)]
    content_date: date = Field(alias="contentDate")


class OrderRequest(ApiModel):
    ids: list[UUID] = Field(min_length=1)


class SceneRequest(SceneDraft):
    pass


class ShotRequest(ShotCardDraft):
    pass


class SuggestShotsRequest(ApiModel):
    allow_paid_generation: bool = Field(alias="allowPaidGeneration")


class ReferencesRequest(ApiModel):
    references: list[ReferenceBinding]


class GenerateRequest(ApiModel):
    allow_paid_generation: bool = Field(alias="allowPaidGeneration")
    regenerate: bool = False
    reason: Annotated[str | None, Field(max_length=1000)] = None

    @property
    def retry_reason(self) -> str | None:
        if not self.regenerate:
            return None
        return self.reason or "用户在镜头节点显式重新生成"


class ReviewRequest(ApiModel):
    decision: Literal["approved", "rejected"]
    reason: Annotated[str | None, Field(max_length=2000)] = None
    select: bool = True


class RangeEditRequest(ApiModel):
    source_asset_id: UUID = Field(alias="sourceAssetId")
    start_ms: int = Field(alias="startMs", ge=0)
    end_ms: int = Field(alias="endMs", gt=0)
    instruction: Annotated[str, Field(min_length=1, max_length=4000)]
    allow_paid_generation: bool = Field(alias="allowPaidGeneration")


class ReconcileRequest(ApiModel):
    provider_task_id: Annotated[str, Field(alias="providerTaskId", min_length=3, max_length=200)]


class SelectSequenceRequest(ApiModel):
    approve: bool = True
