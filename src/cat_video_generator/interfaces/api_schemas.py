"""HTTP request contracts for the V5 video-clip workflow API."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..domain.contracts import (
    ReferenceBinding,
    SceneDraft,
    SceneLookDraft,
    SceneLookPlan,
    ShotAssistPatch,
    ShotCardDraft,
    ShotSuggestion,
    StoryDiagnosisOutput,
    StoryProjectInput,
    StoryRewriteOutput,
    StoryRewriteStrategy,
    VisualProfileDraft,
)


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


class DiagnoseStoryRequest(ApiModel):
    allow_paid_generation: bool = Field(alias="allowPaidGeneration")


class AcceptStoryDiagnosisRequest(ApiModel):
    diagnosis: StoryDiagnosisOutput
    selected_strategy: StoryRewriteStrategy | None = Field(
        default=None,
        alias="selectedStrategy",
    )
    additional_instructions: Annotated[
        str,
        Field(alias="additionalInstructions", max_length=4_000),
    ] = ""
    preserve_original: bool = Field(default=False, alias="preserveOriginal")


class RewriteStoryRequest(ApiModel):
    diagnosis_step_id: UUID = Field(alias="diagnosisStepId")
    allow_paid_generation: bool = Field(alias="allowPaidGeneration")


class AcceptStoryRewriteRequest(ApiModel):
    rewrite: StoryRewriteOutput


class AssistShotRequest(ApiModel):
    source_draft_revision: int = Field(alias="sourceDraftRevision", ge=1)
    candidate_asset_ids: list[UUID] = Field(
        default_factory=list,
        alias="candidateAssetIds",
        max_length=32,
    )
    allow_paid_generation: bool = Field(alias="allowPaidGeneration")


class AcceptShotAssistanceRequest(ApiModel):
    source_draft_revision: int = Field(alias="sourceDraftRevision", ge=1)
    patch: ShotAssistPatch


class AcceptSuggestionsRequest(ApiModel):
    look_plan: SceneLookPlan | None = Field(alias="lookPlan")
    shots: list[ShotSuggestion] = Field(min_length=1, max_length=6)


class ReferencesRequest(ApiModel):
    references: list[ReferenceBinding]


class VisualProfileRequest(VisualProfileDraft):
    pass


class SelectSceneLookRequest(ApiModel):
    asset_id: UUID | None = Field(alias="assetId")


class SaveSceneLookDraftRequest(ApiModel):
    expected_revision: int = Field(alias="expectedRevision", ge=0)
    draft: SceneLookDraft


class GenerateRequest(ApiModel):
    allow_paid_generation: bool = Field(alias="allowPaidGeneration")
    regenerate: bool = False
    reason: Annotated[str | None, Field(max_length=1000)] = None

    @property
    def retry_reason(self) -> str | None:
        if not self.regenerate:
            return None
        return self.reason or "用户在镜头节点显式重新生成"


class GenerateSceneLookRequest(GenerateRequest):
    draft_revision: int = Field(alias="draftRevision", ge=1)


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
