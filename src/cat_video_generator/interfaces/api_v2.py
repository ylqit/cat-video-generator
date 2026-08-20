"""HTTP boundary for the typed AIGC canvas.

V2 is additive: existing V1 endpoints remain available while projects are
enabled progressively.  The application service owns workflow decisions;
this module owns public validation, status codes and SSE framing.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from typing import Any, Protocol

from fastapi import APIRouter, FastAPI, Header, Response, status
from fastapi.responses import StreamingResponse
from pydantic import Field

from ..domain.aigc_canvas import CanvasConnection, CanvasNodeType, StoryBrief, SubjectDraft
from ..domain.contract_base import StrictModel
from ..domain.universal_canvas import (
    CanvasTemplateKey,
    VideoEditAnnotation,
    VideoEditRecipeDraft,
)


class CanvasV2Service(Protocol):
    def save_brief(self, project_id: uuid.UUID, payload: StoryBrief) -> dict[str, Any]: ...

    def create_subject(
        self, project_id: uuid.UUID, payload: SubjectDraft
    ) -> dict[str, Any]: ...

    def create_subject_revision(
        self, subject_id: uuid.UUID, payload: SubjectDraft
    ) -> dict[str, Any]: ...

    def run_story_strategies(
        self, project_id: uuid.UUID, payload: StoryStrategyRunRequest
    ) -> dict[str, Any]: ...

    def approve_story_revision(self, revision_id: uuid.UUID) -> dict[str, Any]: ...

    def create_storyboard(self, project_id: uuid.UUID) -> dict[str, Any]: ...

    def update_shot_beat(
        self,
        beat_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: ShotBeatPatch,
    ) -> dict[str, Any]: ...

    def create_generation_attempt(
        self, payload: GenerationAttemptRequest
    ) -> dict[str, Any]: ...

    def retry_generation_attempt(
        self, attempt_id: uuid.UUID, payload: RetryGenerationRequest
    ) -> dict[str, Any]: ...

    def review_asset(
        self, asset_id: uuid.UUID, payload: AssetReviewRequest
    ) -> dict[str, Any]: ...

    def get_prompt_run(self, prompt_id: uuid.UUID) -> dict[str, Any]: ...

    def get_canvas(self, project_id: uuid.UUID) -> dict[str, Any]: ...

    def save_canvas_layout(
        self,
        project_id: uuid.UUID,
        *,
        expected_version: int,
        payload: CanvasLayoutPatch,
    ) -> dict[str, Any]: ...

    def list_canvas_templates(self) -> list[dict[str, Any]]: ...

    def instantiate_template(
        self, project_id: uuid.UUID, payload: TemplateInstanceRequest
    ) -> dict[str, Any]: ...

    def create_canvas_node(
        self, project_id: uuid.UUID, payload: CanvasNodeCreateRequest
    ) -> dict[str, Any]: ...

    def create_canvas_edge(
        self, project_id: uuid.UUID, payload: CanvasConnection
    ) -> dict[str, Any]: ...

    def delete_canvas_edge(self, edge_id: uuid.UUID) -> dict[str, Any]: ...

    def create_generation_batch(
        self, payload: GenerationBatchRequest
    ) -> dict[str, Any]: ...

    def create_video_edit_recipe(
        self, payload: VideoEditRecipeDraft
    ) -> dict[str, Any]: ...

    def update_video_edit_recipe(
        self,
        recipe_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: VideoEditRecipePatch,
    ) -> dict[str, Any]: ...

    def replace_video_edit_annotations(
        self,
        recipe_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: VideoEditAnnotationsRequest,
    ) -> dict[str, Any]: ...

    def compile_video_edit_recipe(self, recipe_id: uuid.UUID) -> dict[str, Any]: ...

    def submit_video_edit_recipe(
        self, recipe_id: uuid.UUID, payload: SubmitVideoEditRequest
    ) -> dict[str, Any]: ...


class StoryStrategyRunRequest(StrictModel):
    idempotency_key: str | None = Field(
        alias="idempotencyKey", default=None, min_length=8, max_length=96
    )
    rewrite_instruction: str | None = Field(
        alias="rewriteInstruction", default=None, max_length=4_000
    )


class ShotBeatPatch(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    action: str | None = Field(default=None, min_length=1, max_length=6_000)
    camera: str | None = Field(default=None, max_length=2_000)
    dialogue: str | None = Field(default=None, max_length=4_000)
    duration_seconds: int | None = Field(alias="durationSeconds", default=None, ge=1, le=60)
    subject_states: list[dict[str, Any]] | None = Field(
        alias="subjectStates", default=None, max_length=20
    )


class GenerationAttemptRequest(StrictModel):
    project_id: uuid.UUID = Field(alias="projectId")
    business_object_type: str = Field(alias="businessObjectType", min_length=1, max_length=80)
    business_object_id: uuid.UUID = Field(alias="businessObjectId")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)
    request: dict[str, Any]


class RetryGenerationRequest(StrictModel):
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)
    reason: str = Field(min_length=1, max_length=2_000)


class AssetReviewRequest(StrictModel):
    decision: str = Field(pattern="^(approve|reject)$")
    reason: str | None = Field(default=None, max_length=2_000)


class CanvasLayoutPatch(StrictModel):
    nodes: list[dict[str, Any]] = Field(max_length=2_000)
    edges: list[CanvasConnection] = Field(max_length=4_000)
    viewport: dict[str, Any]
    operations: list[dict[str, Any]] = Field(default_factory=list, max_length=2_000)


class TemplateInstanceRequest(StrictModel):
    template_key: CanvasTemplateKey = Field(alias="templateKey")


class CanvasNodeCreateRequest(StrictModel):
    node_type: CanvasNodeType = Field(alias="nodeType")
    object_type: str = Field(alias="objectType", min_length=1, max_length=80)
    object_id: uuid.UUID | None = Field(alias="objectId", default=None)
    data: dict[str, Any] = Field(default_factory=dict)


class GenerationBatchRequest(StrictModel):
    project_id: uuid.UUID = Field(alias="projectId")
    canvas_node_id: uuid.UUID = Field(alias="canvasNodeId")
    media_kind: str = Field(alias="mediaKind", pattern="^(image|video)$")
    candidate_count: int = Field(alias="candidateCount", default=4, ge=1, le=8)
    provider: str | None = Field(default=None, min_length=1, max_length=80)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)
    input: dict[str, Any]


class VideoEditRecipePatch(StrictModel):
    start_ms: int | None = Field(alias="startMs", default=None, ge=0)
    end_ms: int | None = Field(alias="endMs", default=None, gt=0)
    instruction: str | None = Field(default=None, min_length=1, max_length=4_000)
    reference_asset_ids: list[uuid.UUID] | None = Field(
        alias="referenceAssetIds", default=None, max_length=6
    )


class VideoEditAnnotationsRequest(StrictModel):
    annotations: list[VideoEditAnnotation] = Field(max_length=8)


class SubmitVideoEditRequest(StrictModel):
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)
    accept_estimated_cost_micros: int = Field(
        alias="acceptEstimatedCostMicros", ge=0
    )


def install_canvas_v2_routes(app: FastAPI, service: CanvasV2Service) -> None:
    router = APIRouter(prefix="/api/v2")

    @router.get("/canvas-templates")
    def list_canvas_templates() -> list[dict[str, Any]]:
        return service.list_canvas_templates()

    @router.post(
        "/projects/{project_id}/template-instances",
        status_code=status.HTTP_201_CREATED,
    )
    def instantiate_template(
        project_id: uuid.UUID, payload: TemplateInstanceRequest
    ) -> dict[str, Any]:
        return service.instantiate_template(project_id, payload)

    @router.post(
        "/projects/{project_id}/canvas/nodes", status_code=status.HTTP_201_CREATED
    )
    def create_canvas_node(
        project_id: uuid.UUID, payload: CanvasNodeCreateRequest
    ) -> dict[str, Any]:
        return service.create_canvas_node(project_id, payload)

    @router.post(
        "/projects/{project_id}/canvas/edges", status_code=status.HTTP_201_CREATED
    )
    def create_canvas_edge(
        project_id: uuid.UUID, payload: CanvasConnection
    ) -> dict[str, Any]:
        return service.create_canvas_edge(project_id, payload)

    @router.delete("/canvas/edges/{edge_id}")
    def delete_canvas_edge(edge_id: uuid.UUID) -> dict[str, Any]:
        return service.delete_canvas_edge(edge_id)

    @router.put("/projects/{project_id}/brief")
    def save_brief(project_id: uuid.UUID, payload: StoryBrief) -> dict[str, Any]:
        return service.save_brief(project_id, payload)

    @router.post("/projects/{project_id}/subjects", status_code=status.HTTP_201_CREATED)
    def create_subject(project_id: uuid.UUID, payload: SubjectDraft) -> dict[str, Any]:
        return service.create_subject(project_id, payload)

    @router.post("/subjects/{subject_id}/revisions", status_code=status.HTTP_201_CREATED)
    def create_subject_revision(
        subject_id: uuid.UUID,
        payload: SubjectDraft,
    ) -> dict[str, Any]:
        return service.create_subject_revision(subject_id, payload)

    @router.post(
        "/projects/{project_id}/story-strategy-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def run_story_strategies(
        project_id: uuid.UUID,
        payload: StoryStrategyRunRequest,
    ) -> dict[str, Any]:
        return service.run_story_strategies(project_id, payload)

    @router.post("/story-revisions/{revision_id}/approve")
    def approve_story_revision(
        revision_id: uuid.UUID,
        _payload: dict[str, Any],
    ) -> dict[str, Any]:
        return service.approve_story_revision(revision_id)

    @router.post(
        "/projects/{project_id}/storyboard-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_storyboard(
        project_id: uuid.UUID,
        _payload: dict[str, Any],
    ) -> dict[str, Any]:
        return service.create_storyboard(project_id)

    @router.patch("/shot-beats/{beat_id}")
    def update_shot_beat(
        beat_id: uuid.UUID,
        payload: ShotBeatPatch,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.update_shot_beat(
            beat_id,
            expected_revision=_version_header(if_match),
            payload=payload,
        )

    @router.post("/generation-attempts", status_code=status.HTTP_202_ACCEPTED)
    def create_generation_attempt(payload: GenerationAttemptRequest) -> dict[str, Any]:
        return service.create_generation_attempt(payload)

    @router.post("/generation-batches", status_code=status.HTTP_202_ACCEPTED)
    def create_generation_batch(payload: GenerationBatchRequest) -> dict[str, Any]:
        return service.create_generation_batch(payload)

    @router.post("/video-edit-recipes", status_code=status.HTTP_201_CREATED)
    def create_video_edit_recipe(payload: VideoEditRecipeDraft) -> dict[str, Any]:
        return service.create_video_edit_recipe(payload)

    @router.patch("/video-edit-recipes/{recipe_id}", status_code=status.HTTP_201_CREATED)
    def update_video_edit_recipe(
        recipe_id: uuid.UUID,
        payload: VideoEditRecipePatch,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.update_video_edit_recipe(
            recipe_id,
            expected_revision=_version_header(if_match),
            payload=payload,
        )

    @router.put("/video-edit-recipes/{recipe_id}/annotations")
    def replace_video_edit_annotations(
        recipe_id: uuid.UUID,
        payload: VideoEditAnnotationsRequest,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.replace_video_edit_annotations(
            recipe_id,
            expected_revision=_version_header(if_match),
            payload=payload,
        )

    @router.post("/video-edit-recipes/{recipe_id}/compile")
    def compile_video_edit_recipe(
        recipe_id: uuid.UUID, _payload: dict[str, Any]
    ) -> dict[str, Any]:
        return service.compile_video_edit_recipe(recipe_id)

    @router.post(
        "/video-edit-recipes/{recipe_id}/submit",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def submit_video_edit_recipe(
        recipe_id: uuid.UUID, payload: SubmitVideoEditRequest
    ) -> dict[str, Any]:
        return service.submit_video_edit_recipe(recipe_id, payload)

    @router.post(
        "/generation-attempts/{attempt_id}/retry",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def retry_generation_attempt(
        attempt_id: uuid.UUID,
        payload: RetryGenerationRequest,
    ) -> dict[str, Any]:
        return service.retry_generation_attempt(attempt_id, payload)

    @router.post("/assets/{asset_id}/review")
    def review_asset(asset_id: uuid.UUID, payload: AssetReviewRequest) -> dict[str, Any]:
        return service.review_asset(asset_id, payload)

    @router.get("/prompt-runs/{prompt_id}")
    def get_prompt_run(prompt_id: uuid.UUID) -> dict[str, Any]:
        return service.get_prompt_run(prompt_id)

    @router.get("/projects/{project_id}/canvas")
    def get_canvas(project_id: uuid.UUID) -> dict[str, Any]:
        return service.get_canvas(project_id)

    @router.patch("/projects/{project_id}/canvas/layout")
    def save_canvas_layout(
        project_id: uuid.UUID,
        payload: CanvasLayoutPatch,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.save_canvas_layout(
            project_id,
            expected_version=_version_header(if_match),
            payload=payload,
        )

    @router.get("/projects/{project_id}/events")
    def project_events(
        project_id: uuid.UUID,
        last_event_id: str | None = Header(alias="Last-Event-ID", default=None),
    ) -> StreamingResponse:
        event_loader = getattr(service, "events", None)
        events: Iterable[dict[str, Any]] = (
            event_loader(project_id, last_event_id=last_event_id)
            if callable(event_loader)
            else ({"type": "canvas_snapshot", "data": service.get_canvas(project_id)},)
        )

        def encode() -> Iterable[str]:
            for event in events:
                event_id = event.get("id")
                if event_id is not None:
                    yield f"id: {event_id}\n"
                yield f"event: {event.get('type', 'message')}\n"
                yield f"data: {json.dumps(event.get('data', {}), ensure_ascii=False)}\n\n"

        return StreamingResponse(
            encode(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.options("/{path:path}", include_in_schema=False)
    def v2_options(_path: str) -> Response:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    app.include_router(router)


def _version_header(value: str) -> int:
    normalized = value.strip().strip('"')
    try:
        version = int(normalized)
    except ValueError as exc:
        raise ValueError("If-Match 必须是画布或对象的整数版本") from exc
    if version < 0:
        raise ValueError("If-Match 版本不能为负数")
    return version
