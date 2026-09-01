"""HTTP boundary for the Creator-only product."""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import Any, Literal, Protocol

from fastapi import APIRouter, FastAPI, Header, status
from fastapi.responses import FileResponse
from pydantic import Field

from ..domain.contract_base import StrictModel
from ..domain.creator_core import (
    CreativeTextCandidate,
    CreatorReference,
    CreatorShotDraft,
    GenerationSnapshotDraft,
)
from .http_headers import parse_version_header


class CreatorService(Protocol):
    def list_projects(self) -> list[dict[str, Any]]: ...
    def canon_options(self) -> dict[str, Any]: ...
    def create_project(self, payload: Any) -> dict[str, Any]: ...
    def get_state(self, project_id: uuid.UUID) -> dict[str, Any]: ...
    def asset_library(self, project_id: uuid.UUID) -> list[dict[str, Any]]: ...
    def update_state(
        self, project_id: uuid.UUID, *, expected_version: int, payload: Any
    ) -> dict[str, Any]: ...
    def create_story_candidates(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]: ...
    def save_story(
        self, project_id: uuid.UUID, *, expected_version: int, payload: Any
    ) -> dict[str, Any]: ...
    def list_shots(self, project_id: uuid.UUID) -> list[dict[str, Any]]: ...
    def replace_shots(
        self, project_id: uuid.UUID, *, expected_version: int, payload: Any
    ) -> dict[str, Any]: ...
    def update_shot(
        self, shot_id: uuid.UUID, *, expected_version: int, payload: Any
    ) -> dict[str, Any]: ...
    def create_snapshot(self, shot_id: uuid.UUID, payload: Any) -> dict[str, Any]: ...
    def submit_snapshot(
        self,
        snapshot_id: uuid.UUID,
        *,
        idempotency_key: str,
        input_hash: str,
        accepted_estimated_cost_micros: int,
    ) -> dict[str, Any]: ...
    def decide_asset(self, asset_id: uuid.UUID, payload: Any) -> dict[str, Any]: ...
    def select_video(
        self, shot_id: uuid.UUID, *, expected_version: int, payload: Any
    ) -> dict[str, Any]: ...
    def diagnostics(self, project_id: uuid.UUID) -> dict[str, Any]: ...
    def list_tasks(self, project_id: uuid.UUID | None = None) -> list[dict[str, Any]]: ...
    def timeline(self, project_id: uuid.UUID) -> dict[str, Any]: ...
    def save_timeline(
        self, project_id: uuid.UUID, *, expected_version: int, clips: list[dict[str, Any]]
    ) -> dict[str, Any]: ...


class CreatorFiles(Protocol):
    def asset_path(self, asset_id: uuid.UUID) -> Path: ...


class CreatorCancellation(Protocol):
    def cancellation_for(self, task_id: uuid.UUID) -> dict[str, Any]: ...
    def cancel(
        self,
        task_id: uuid.UUID,
        *,
        expected_status: str,
        expected_provider_task_id: str | None,
        reason: str | None,
    ) -> dict[str, Any]: ...


class CreatorBriefRequest(StrictModel):
    body: str = Field(min_length=1, max_length=40_000)
    duration_seconds: int = Field(alias="durationSeconds", ge=1, le=360)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = Field(alias="aspectRatio")
    quality_tier: Literal["quick", "standard", "quality"] = Field(alias="qualityTier")


class CreateCreatorProjectRequest(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    content_date: date | None = Field(alias="contentDate", default=None)
    brief: CreatorBriefRequest
    references: list[CreatorReference] = Field(min_length=3, max_length=10)


class UpdateCreatorStateRequest(StrictModel):
    brief_body: str | None = Field(alias="briefBody", default=None, max_length=40_000)
    target_duration_seconds: int | None = Field(
        alias="targetDurationSeconds", default=None, ge=1, le=360
    )
    aspect_ratio: Literal["9:16", "16:9", "1:1"] | None = Field(alias="aspectRatio", default=None)
    quality_tier: Literal["quick", "standard", "quality"] | None = Field(
        alias="qualityTier", default=None
    )
    reference_bindings: list[CreatorReference] | None = Field(
        alias="referenceBindings", default=None, min_length=3, max_length=10
    )


class StoryCandidatesRequest(StrictModel):
    brief_body: str = Field(alias="briefBody", min_length=1, max_length=40_000)
    requested_count: int = Field(alias="requestedCount", default=3, ge=1, le=5)


class SaveStoryRequest(StrictModel):
    story: CreativeTextCandidate


class ReplaceShotsRequest(StrictModel):
    shots: list[CreatorShotDraft] = Field(min_length=1, max_length=6)


class PatchShotRequest(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    direction: str | None = Field(default=None, min_length=1, max_length=20_000)
    duration_seconds: int | None = Field(alias="durationSeconds", default=None, ge=1, le=60)
    scene_label: str | None = Field(alias="sceneLabel", default=None, max_length=160)
    reference_bindings: list[CreatorReference] | None = Field(
        alias="referenceBindings", default=None, max_length=12
    )
    prompt_draft: str | None = Field(alias="promptDraft", default=None, max_length=30_000)


class SubmitSnapshotRequest(StrictModel):
    input_hash: str = Field(alias="inputHash", min_length=64, max_length=64)
    accepted_estimated_cost_micros: int = Field(alias="acceptedEstimatedCostMicros", ge=0)


class AssetDecisionRequest(StrictModel):
    decision: Literal["adopt", "reject"]
    reason: str | None = Field(default=None, max_length=2_000)


class SelectVideoRequest(StrictModel):
    asset_id: uuid.UUID = Field(alias="assetId")


class TimelineClipRequest(StrictModel):
    creator_shot_id: uuid.UUID = Field(alias="creatorShotId")
    asset_id: uuid.UUID = Field(alias="assetId")
    transition: Literal["cut", "fade_black", "cross_dissolve"] = "cut"
    transition_duration_ms: int = Field(alias="transitionDurationMs", default=0, ge=0, le=2_000)


class SaveTimelineRequest(StrictModel):
    clips: list[TimelineClipRequest] = Field(max_length=6)


class CancelTaskRequest(StrictModel):
    expected_status: str = Field(alias="expectedStatus", min_length=1, max_length=32)
    expected_provider_task_id: str | None = Field(
        alias="expectedProviderTaskId", default=None, max_length=240
    )
    reason: str | None = Field(default=None, max_length=2_000)


def install_creator_routes(
    app: FastAPI,
    service: CreatorService,
    files: CreatorFiles,
    cancellation: CreatorCancellation,
) -> None:
    router = APIRouter(prefix="/api/v2", tags=["creator"])

    @router.get("/creator-projects")
    def list_projects() -> list[dict[str, Any]]:
        return service.list_projects()

    @router.get("/creator-canon")
    def canon_options() -> dict[str, Any]:
        return service.canon_options()

    @router.post("/creator-projects", status_code=status.HTTP_201_CREATED)
    def create_project(payload: CreateCreatorProjectRequest) -> dict[str, Any]:
        return service.create_project(payload)

    @router.get("/projects/{project_id}/creator-state")
    def get_state(project_id: uuid.UUID) -> dict[str, Any]:
        return service.get_state(project_id)

    @router.get("/projects/{project_id}/creator-assets")
    def asset_library(project_id: uuid.UUID) -> list[dict[str, Any]]:
        return service.asset_library(project_id)

    @router.patch("/projects/{project_id}/creator-state")
    def update_state(
        project_id: uuid.UUID,
        payload: UpdateCreatorStateRequest,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.update_state(
            project_id, expected_version=parse_version_header(if_match), payload=payload
        )

    @router.post("/projects/{project_id}/story-candidates", status_code=201)
    def create_story_candidates(
        project_id: uuid.UUID, payload: StoryCandidatesRequest
    ) -> dict[str, Any]:
        return service.create_story_candidates(project_id, payload)

    @router.put("/projects/{project_id}/story")
    def save_story(
        project_id: uuid.UUID,
        payload: SaveStoryRequest,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.save_story(
            project_id, expected_version=parse_version_header(if_match), payload=payload
        )

    @router.get("/projects/{project_id}/shots")
    def list_shots(project_id: uuid.UUID) -> list[dict[str, Any]]:
        return service.list_shots(project_id)

    @router.put("/projects/{project_id}/shots")
    def replace_shots(
        project_id: uuid.UUID,
        payload: ReplaceShotsRequest,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.replace_shots(
            project_id, expected_version=parse_version_header(if_match), payload=payload
        )

    @router.patch("/creator-shots/{shot_id}")
    def update_shot(
        shot_id: uuid.UUID,
        payload: PatchShotRequest,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.update_shot(
            shot_id, expected_version=parse_version_header(if_match), payload=payload
        )

    @router.post("/creator-shots/{shot_id}/generation-snapshots", status_code=201)
    def create_snapshot(shot_id: uuid.UUID, payload: GenerationSnapshotDraft) -> dict[str, Any]:
        return service.create_snapshot(shot_id, payload)

    @router.post("/generation-snapshots/{snapshot_id}/submit", status_code=202)
    def submit_snapshot(
        snapshot_id: uuid.UUID,
        payload: SubmitSnapshotRequest,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=64),
    ) -> dict[str, Any]:
        return service.submit_snapshot(
            snapshot_id,
            idempotency_key=idempotency_key,
            input_hash=payload.input_hash,
            accepted_estimated_cost_micros=payload.accepted_estimated_cost_micros,
        )

    @router.post("/assets/{asset_id}/decision")
    def decide_asset(asset_id: uuid.UUID, payload: AssetDecisionRequest) -> dict[str, Any]:
        return service.decide_asset(asset_id, payload)

    @router.put("/creator-shots/{shot_id}/selected-video")
    def select_video(
        shot_id: uuid.UUID,
        payload: SelectVideoRequest,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.select_video(
            shot_id, expected_version=parse_version_header(if_match), payload=payload
        )

    @router.get("/projects/{project_id}/diagnostics")
    def diagnostics(project_id: uuid.UUID) -> dict[str, Any]:
        return service.diagnostics(project_id)

    @router.get("/generation-tasks")
    def list_tasks(project_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
        return service.list_tasks(project_id)

    @router.get("/generation-tasks/{task_id}/cancellation")
    def cancellation_policy(task_id: uuid.UUID) -> dict[str, Any]:
        return cancellation.cancellation_for(task_id)

    @router.post("/generation-tasks/{task_id}/cancellation")
    def cancel_task(task_id: uuid.UUID, payload: CancelTaskRequest) -> dict[str, Any]:
        return cancellation.cancel(
            task_id,
            expected_status=payload.expected_status,
            expected_provider_task_id=payload.expected_provider_task_id,
            reason=payload.reason,
        )

    @router.get("/projects/{project_id}/timeline")
    def timeline(project_id: uuid.UUID) -> dict[str, Any]:
        return service.timeline(project_id)

    @router.put("/projects/{project_id}/timeline")
    def save_timeline(
        project_id: uuid.UUID,
        payload: SaveTimelineRequest,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        clips = [item.model_dump(by_alias=True, mode="json") for item in payload.clips]
        return service.save_timeline(
            project_id, expected_version=parse_version_header(if_match), clips=clips
        )

    @router.get("/media-assets/{asset_id}/content")
    def media_content(asset_id: uuid.UUID) -> FileResponse:
        path = files.asset_path(asset_id)
        return FileResponse(path, filename=path.name)

    app.include_router(router)
