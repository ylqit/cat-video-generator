"""HTTP boundary for the typed AIGC canvas.

V2 is additive: existing V1 endpoints remain available while projects are
enabled progressively.  The application service owns workflow decisions;
this module owns public validation, status codes and SSE framing.
"""

from __future__ import annotations

import inspect
import uuid
from typing import Any, Literal, Protocol

from fastapi import APIRouter, FastAPI, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import Field, model_validator

from ..domain.aigc_canvas import (
    CanvasConnection,
    CanvasNodeType,
    NodeGenerationConfigDraft,
    StoryBrief,
    SubjectCompletionField,
    SubjectDraft,
)
from ..domain.contract_base import StrictModel
from ..domain.universal_canvas import (
    CanvasTemplateKey,
    VideoEditAnnotation,
    VideoEditRecipeDraft,
)
from .http_headers import parse_version_header
from .jobs import JobConflictError, JobRegistry
from .sse import parse_event_cursor, stream_events


class CanvasV2Service(Protocol):
    def save_brief(self, project_id: uuid.UUID, payload: StoryBrief) -> dict[str, Any]: ...

    def create_subject(
        self, project_id: uuid.UUID, payload: SubjectDraft
    ) -> dict[str, Any]: ...

    def create_subject_revision(
        self, subject_id: uuid.UUID, payload: SubjectDraft
    ) -> dict[str, Any]: ...

    def create_subject_completion_run(
        self, project_id: uuid.UUID, payload: SubjectAssistantRunRequest
    ) -> dict[str, Any]: ...

    def get_subject_completion_run(self, run_id: uuid.UUID) -> dict[str, Any]: ...

    def apply_subject_completion(
        self, run_id: uuid.UUID, payload: SubjectAssistantApplyRequest
    ) -> dict[str, Any]: ...

    def list_project_assets(
        self, project_id: uuid.UUID, *, media_kind: str | None = None
    ) -> list[dict[str, Any]]: ...

    def create_video_filmstrip_run(
        self, asset_id: uuid.UUID, *, frame_count: int
    ) -> dict[str, Any]: ...

    def get_video_filmstrip(
        self, asset_id: uuid.UUID, *, frame_count: int
    ) -> dict[str, Any]: ...

    def save_node_generation_config(
        self,
        node_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: NodeGenerationConfigDraft,
    ) -> dict[str, Any]: ...

    def list_provider_capabilities(
        self, *, media_kind: str | None = None
    ) -> list[dict[str, Any]]: ...

    def run_story_strategies(
        self, project_id: uuid.UUID, payload: StoryStrategyRunRequest
    ) -> dict[str, Any]: ...

    def approve_story_revision(self, revision_id: uuid.UUID) -> dict[str, Any]: ...

    def create_storyboard(
        self,
        project_id: uuid.UUID,
        *,
        idempotency_key: str | None = None,
        creation_mode: str = "from_story",
        reference_asset_ids: tuple[uuid.UUID, ...] = (),
        instruction: str | None = None,
    ) -> dict[str, Any]: ...

    def update_shot_beat(
        self,
        beat_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: ShotBeatPatch,
    ) -> dict[str, Any]: ...

    def save_manual_storyboard(
        self,
        project_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: ManualStoryboardDraftRequest,
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


class StoryboardRunRequest(StrictModel):
    creation_mode: Literal["from_story", "from_characters"] = Field(
        alias="creationMode",
        default="from_story",
    )
    reference_asset_ids: list[uuid.UUID] = Field(
        alias="referenceAssetIds",
        default_factory=list,
        max_length=6,
    )
    instruction: str | None = Field(default=None, max_length=4_000)
    idempotency_key: str | None = Field(
        alias="idempotencyKey",
        default=None,
        min_length=8,
        max_length=96,
    )
    rewrite_instruction: str | None = Field(
        alias="rewriteInstruction", default=None, max_length=4_000
    )


class SubjectAssistantRunRequest(StrictModel):
    subject_id: uuid.UUID = Field(alias="subjectId")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)
    instruction: str = Field(default="", max_length=4_000)


class SubjectAssistantApplyRequest(StrictModel):
    accepted_fields: list[SubjectCompletionField] = Field(
        alias="acceptedFields", min_length=1, max_length=5
    )
    final_draft: SubjectDraft = Field(alias="finalDraft")


class ShotBeatPatch(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    action: str | None = Field(default=None, min_length=1, max_length=6_000)
    camera: str | None = Field(default=None, max_length=2_000)
    dialogue: str | None = Field(default=None, max_length=4_000)
    duration_seconds: int | None = Field(alias="durationSeconds", default=None, ge=1, le=60)
    subject_states: list[dict[str, Any]] | None = Field(
        alias="subjectStates", default=None, max_length=20
    )


class ManualStoryboardShot(StrictModel):
    id: uuid.UUID | None = None
    revision: int | None = Field(default=None, ge=1)
    order: int = Field(ge=1, le=200)
    duration_seconds: int = Field(alias="durationSeconds", ge=1, le=60)
    title: str = Field(min_length=1, max_length=160)
    action: str = Field(min_length=1, max_length=6_000)
    shot_size: str = Field(alias="shotSize", default="中景", max_length=200)
    lighting: str = Field(default="", max_length=500)
    dialogue: str = Field(default="", max_length=4_000)
    sound_effect: str = Field(alias="soundEffect", default="", max_length=1_000)
    camera: str = Field(default="", max_length=2_000)
    prompt: str = Field(default="", max_length=8_000)


class ManualStoryboardDraftRequest(StrictModel):
    shots: list[ManualStoryboardShot] = Field(min_length=1, max_length=200)
    healing_recipe: bool = Field(alias="healingRecipe", default=False)

    @model_validator(mode="after")
    def validate_shots(self) -> ManualStoryboardDraftRequest:
        orders = [shot.order for shot in self.shots]
        if sorted(orders) != list(range(1, len(self.shots) + 1)):
            raise ValueError("镜头顺序必须从1开始且连续")
        if self.healing_recipe:
            if any(not 8 <= shot.duration_seconds <= 15 for shot in self.shots):
                raise ValueError("治愈组合包每镜必须为8至15秒")
            if any(shot.dialogue.strip() for shot in self.shots):
                raise ValueError("治愈组合包禁止对白")
        return self


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
    legacy_edges: list[dict[str, Any]] | None = Field(
        alias="edges",
        default=None,
        exclude=True,
        max_length=4_000,
    )
    viewport: dict[str, Any]
    operations: list[dict[str, Any]] = Field(default_factory=list, max_length=2_000)


class TemplateInstanceRequest(StrictModel):
    template_key: CanvasTemplateKey = Field(alias="templateKey")


class CanvasNodeCreateRequest(StrictModel):
    node_type: CanvasNodeType = Field(alias="nodeType")
    object_type: str = Field(alias="objectType", min_length=1, max_length=80)
    object_id: uuid.UUID | None = Field(alias="objectId", default=None)
    data: dict[str, Any] = Field(default_factory=dict)


class CanvasNodeAssetBinding(StrictModel):
    asset_id: uuid.UUID = Field(alias="assetId")
    semantic_role: str = Field(alias="semanticRole", min_length=1, max_length=80)


class CanvasNodeAssetBindingsRequest(StrictModel):
    bindings: list[CanvasNodeAssetBinding] = Field(max_length=30)
    allow_move: bool = Field(alias="allowMove", default=False)


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


def install_canvas_v2_routes(
    app: FastAPI,
    service: CanvasV2Service,
    jobs: JobRegistry,
) -> None:
    router = APIRouter(prefix="/api/v2")

    @router.get("/canvas-templates")
    def list_canvas_templates() -> list[dict[str, Any]]:
        return service.list_canvas_templates()

    @router.get("/provider-capabilities")
    def list_provider_capabilities(
        media_kind: str | None = Query(
            alias="mediaKind", default=None, pattern="^(image|video|audio|video_edit)$"
        ),
    ) -> list[dict[str, Any]]:
        return service.list_provider_capabilities(media_kind=media_kind)

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

    @router.put(
        "/canvas/nodes/{node_id}/generation-config",
        status_code=status.HTTP_201_CREATED,
    )
    def save_node_generation_config(
        node_id: uuid.UUID,
        payload: NodeGenerationConfigDraft,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.save_node_generation_config(
            node_id,
            expected_revision=parse_version_header(if_match),
            payload=payload,
        )

    @router.put("/projects/{project_id}/brief")
    def save_brief(project_id: uuid.UUID, payload: StoryBrief) -> dict[str, Any]:
        return service.save_brief(project_id, payload)

    @router.post("/projects/{project_id}/subjects", status_code=status.HTTP_201_CREATED)
    def create_subject(project_id: uuid.UUID, payload: SubjectDraft) -> dict[str, Any]:
        return service.create_subject(project_id, payload)

    @router.get("/projects/{project_id}/subjects")
    def list_subjects(project_id: uuid.UUID) -> list[dict[str, Any]]:
        return service.list_subjects(project_id)

    @router.post("/subjects/{subject_id}/revisions", status_code=status.HTTP_201_CREATED)
    def create_subject_revision(
        subject_id: uuid.UUID,
        payload: SubjectDraft,
    ) -> dict[str, Any]:
        return service.create_subject_revision(subject_id, payload)

    @router.post(
        "/projects/{project_id}/subject-assistant-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_subject_completion_run(
        project_id: uuid.UUID,
        payload: SubjectAssistantRunRequest,
    ) -> dict[str, Any]:
        return service.create_subject_completion_run(project_id, payload)

    @router.get("/subject-assistant-runs/{run_id}")
    def get_subject_completion_run(run_id: uuid.UUID) -> dict[str, Any]:
        return service.get_subject_completion_run(run_id)

    @router.post(
        "/subject-assistant-runs/{run_id}/apply",
        status_code=status.HTTP_201_CREATED,
    )
    def apply_subject_completion(
        run_id: uuid.UUID,
        payload: SubjectAssistantApplyRequest,
    ) -> dict[str, Any]:
        return service.apply_subject_completion(run_id, payload)

    @router.get("/projects/{project_id}/assets")
    def list_project_assets(
        project_id: uuid.UUID,
        kind: str | None = Query(default=None, pattern="^(image|video|audio)$"),
    ) -> list[dict[str, Any]]:
        return service.list_project_assets(project_id, media_kind=kind)

    @router.post(
        "/assets/{asset_id}/filmstrip-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_video_filmstrip_run(
        asset_id: uuid.UUID,
        frame_count: int = Query(default=12, alias="frameCount", ge=4, le=12),
    ) -> dict[str, Any]:
        return service.create_video_filmstrip_run(asset_id, frame_count=frame_count)

    @router.get("/assets/{asset_id}/filmstrip")
    def get_video_filmstrip(
        asset_id: uuid.UUID,
        frame_count: int = Query(default=12, alias="frameCount", ge=4, le=12),
    ) -> dict[str, Any]:
        return service.get_video_filmstrip(asset_id, frame_count=frame_count)

    @router.put("/canvas/nodes/{node_id}/asset-bindings")
    def bind_canvas_node_assets(
        node_id: uuid.UUID,
        payload: CanvasNodeAssetBindingsRequest,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.bind_canvas_node_assets(
            node_id,
            expected_revision=parse_version_header(if_match),
            payload=payload,
        )

    @router.post(
        "/projects/{project_id}/story-strategy-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def run_story_strategies(
        project_id: uuid.UUID,
        payload: StoryStrategyRunRequest,
    ) -> dict[str, Any]:
        try:
            record = jobs.submit(
                kind="story_strategy",
                dedup_key=f"story_strategy:{project_id}:{payload.idempotency_key or 'default'}",
                fn=lambda: service.run_story_strategies(project_id, payload),
                context={
                    "projectId": project_id,
                    "canvasNodeId": uuid.uuid5(project_id, "story-planner"),
                    "operationKey": "canvas:story_strategy",
                    "workflowStage": "story",
                },
            )
        except JobConflictError as exc:
            return jobs.get(exc.job_id).to_dict()
        return record.to_dict()

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
        payload: StoryboardRunRequest,
    ) -> dict[str, Any]:
        if payload.creation_mode == "from_characters" and not payload.reference_asset_ids:
            raise ValueError("角色生成分镜至少需要一个角色素材")
        create_parameters = inspect.signature(service.create_storyboard).parameters
        if "idempotency_key" not in create_parameters:
            # Compatibility boundary for older CanvasV2 service implementations:
            # validation still happens before a 202 response. The production service
            # exposes the extended signature and always runs inside JobRegistry.
            result = service.create_storyboard(project_id)

            def run() -> dict[str, Any]:
                return result
        else:
            def run() -> dict[str, Any]:
                return service.create_storyboard(
                    project_id,
                    idempotency_key=payload.idempotency_key,
                    creation_mode=payload.creation_mode,
                    reference_asset_ids=tuple(payload.reference_asset_ids),
                    instruction=payload.instruction,
                )
        try:
            record = jobs.submit(
                kind="storyboard",
                dedup_key=f"storyboard:{project_id}:{payload.idempotency_key or 'default'}",
                fn=run,
                context={
                    "projectId": project_id,
                    "canvasNodeId": uuid.uuid5(project_id, "storyboard-director"),
                    "creationMode": payload.creation_mode,
                    "operationKey": "canvas:storyboard",
                    "workflowStage": "storyboard",
                },
            )
        except JobConflictError as exc:
            return jobs.get(exc.job_id).to_dict()
        return record.to_dict()

    @router.patch("/shot-beats/{beat_id}")
    def update_shot_beat(
        beat_id: uuid.UUID,
        payload: ShotBeatPatch,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.update_shot_beat(
            beat_id,
            expected_revision=parse_version_header(if_match),
            payload=payload,
        )

    @router.put("/projects/{project_id}/storyboard-drafts")
    def save_manual_storyboard(
        project_id: uuid.UUID,
        payload: ManualStoryboardDraftRequest,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.save_manual_storyboard(
            project_id,
            expected_revision=parse_version_header(if_match),
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
            expected_revision=parse_version_header(if_match),
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
            expected_revision=parse_version_header(if_match),
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
            expected_version=parse_version_header(if_match),
            payload=payload,
        )

    @router.get("/projects/{project_id}/events")
    async def project_events(
        project_id: uuid.UUID,
        request: Request,
        last_event_id: str | None = Header(alias="Last-Event-ID", default=None),
        after_event_id: int | None = Query(alias="afterEventId", default=None, ge=0),
    ) -> StreamingResponse:
        event_loader = getattr(service, "events", None)
        cursor = parse_event_cursor(last_event_id, after_event_id)

        def load(after_sequence: int) -> tuple[dict[str, Any], ...]:
            if callable(event_loader):
                return event_loader(project_id, after_sequence=after_sequence)
            if after_sequence > 0:
                return ()
            return (
                {
                    "sequence": 1,
                    "type": "canvas_snapshot",
                    "data": service.get_canvas(project_id),
                },
            )

        return StreamingResponse(
            stream_events(request, loader=load, after_sequence=cursor),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.options("/{path:path}", include_in_schema=False)
    def v2_options(_path: str) -> Response:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    app.include_router(router)
