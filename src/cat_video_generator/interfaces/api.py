"""FastAPI surface for the V5 scene and video-clip creation studio."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..application.shot_queue import GatewayUnavailableError, RevisionConflictError
from ..domain.contracts import (
    CURRENT_CONTRACT_VERSION,
    ReferenceRole,
    ReferenceTarget,
    ReferenceUsage,
)
from ..domain.rendering import SequenceStatus
from ..infrastructure.db.repositories import WorkflowConflictError
from ..infrastructure.db.session import ALEMBIC_HEAD
from .api_schemas import (
    AcceptShotAssistanceRequest,
    AcceptStoryDiagnosisRequest,
    AcceptStoryRewriteRequest,
    AcceptSuggestionsRequest,
    AssistShotRequest,
    BuildSequenceRequest,
    CreateProjectRequest,
    DiagnoseStoryRequest,
    GenerateRequest,
    GenerateSceneLookRequest,
    OrderRequest,
    RangeEditRequest,
    ReconcileRequest,
    ReferencesRequest,
    ReviewRequest,
    RewriteStoryRequest,
    SaveSceneLookDraftRequest,
    SceneRequest,
    SelectSceneLookRequest,
    SelectSequenceRequest,
    ShotRequest,
    SuggestShotsRequest,
    UpdateProjectRequest,
    VisualProfileRequest,
)
from .jobs import JobConflictError, JobRegistry

if TYPE_CHECKING:
    from ..bootstrap import RuntimeContainer


class _SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            normalized = path.replace("\\", "/").lstrip("/")
            if (
                exc.status_code != 404
                or scope["method"] not in {"GET", "HEAD"}
                or normalized.startswith("api/")
                or Path(normalized).suffix
            ):
                raise
            return await super().get_response("index.html", scope)


def create_app(
    container: RuntimeContainer,
    *,
    job_registry: JobRegistry,
    static_dir: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="Cat Video Shot Queue", version="5.0.0", redoc_url=None)
    repository = container.repository
    roots = tuple(
        item.expanduser().resolve()
        for item in (
            container.runtime_settings.work_root,
            container.runtime_settings.asset_root,
        )
    )

    @app.exception_handler(LookupError)
    async def not_found(_request: Request, exc: LookupError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def invalid_request(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(WorkflowConflictError)
    async def workflow_conflict(_request: Request, exc: WorkflowConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(RevisionConflictError)
    async def revision_conflict(_request: Request, exc: RevisionConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(GatewayUnavailableError)
    async def gateway_unavailable(_request: Request, exc: GatewayUnavailableError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        database_ready = container.alembic_revision == ALEMBIC_HEAD
        return {
            "ready": database_ready,
            "databaseReady": database_ready,
            "contractVersion": CURRENT_CONTRACT_VERSION,
            "alembicRevision": container.alembic_revision,
            "expectedAlembicRevision": ALEMBIC_HEAD,
            **container.runtime_settings.preflight_report(),
        }

    @app.get("/api/v1/projects")
    def list_projects() -> list[dict[str, Any]]:
        return [
            {
                "id": str(item.id),
                "title": item.title,
                "contentDate": item.content_date.isoformat(),
                "status": item.status.value,
            }
            for item in repository.list_projects()
        ]

    @app.post("/api/v1/projects")
    def create_project(payload: CreateProjectRequest) -> dict[str, Any]:
        return container.editing.create_project(
            payload.project,
            content_date=payload.content_date,
        )

    @app.get("/api/v1/projects/{project_id}")
    def project_graph(project_id: uuid.UUID) -> dict[str, Any]:
        return repository.project_graph(project_id)

    @app.get("/api/v1/projects/{project_id}/production-board")
    def production_board(project_id: uuid.UUID) -> dict[str, Any]:
        graph = repository.project_graph(project_id)
        assets_by_id = {item["id"]: item for item in graph["assets"]}
        for graph_scene in graph["scenes"]:
            for graph_shot in graph_scene["shots"]:
                assets_by_id.update({item["id"]: item for item in graph_shot["assets"]})
        active_statuses = {"pending", "submitting", "queued", "running"}
        scene_summaries: list[dict[str, Any]] = []
        for scene in graph["scenes"]:
            scene_look_versions = [
                item
                for item in graph["assets"]
                if item.get("sceneId") == scene["id"] and item.get("role") == "scene_look"
            ]
            shot_summaries: list[dict[str, Any]] = []
            for shot in scene["shots"]:
                active_attempts = [
                    item for item in shot["attempts"] if item["status"] in active_statuses
                ]
                active_anchor = next(
                    (
                        item
                        for item in active_attempts
                        if item["operationKey"] == "image:anchor"
                    ),
                    None,
                )
                active_video = next(
                    (
                        item
                        for item in active_attempts
                        if item["operationKey"] in {"video:shot", "video:range-edit"}
                    ),
                    None,
                )
                anchor_assets = [
                    item for item in shot["assets"] if item["role"] == "shot_anchor"
                ]
                video_assets = [
                    item
                    for item in shot["assets"]
                    if item["role"] in {"shot_video", "shot_video_edit"}
                ]
                selected_anchor = assets_by_id.get(shot.get("selectedAnchorAssetId"))
                selected_video = assets_by_id.get(shot.get("selectedVideoAssetId"))
                candidate_video = next(
                    (item for item in reversed(video_assets) if item["status"] == "candidate"),
                    None,
                )
                selected_video_step = next(
                    (
                        item
                        for item in shot["attempts"]
                        if selected_video
                        and item["id"] == selected_video.get("producingStepId")
                    ),
                    None,
                )

                ordered_source_ids: list[str] = []
                if selected_anchor:
                    ordered_source_ids.append(selected_anchor["id"])
                ordered_source_ids.extend(
                    binding["assetId"]
                    for binding in shot["referenceBindings"]
                    if binding["usage"] != "approved_anchor"
                    and binding["applyTo"] in {"video", "both"}
                )
                include_scene_look = (
                    scene.get("selectedLookAssetId") is not None
                    and shot["sceneLookUsage"] in {"appearance_only", "full_reference"}
                )
                if include_scene_look:
                    ordered_source_ids.append(scene["selectedLookAssetId"])
                if shot["inheritProjectReferences"]:
                    ordered_source_ids.extend(
                        binding["assetId"]
                        for binding in graph["project"]["defaultReferenceBindings"]
                        if binding["applyTo"] in {"video", "both"}
                    )

                source_ids: list[str] = []
                seen_asset_ids: set[str] = set()
                seen_sha256: set[str] = set()
                for asset_id in ordered_source_ids:
                    asset = assets_by_id.get(asset_id)
                    if asset_id in seen_asset_ids:
                        continue
                    sha256 = str(asset.get("sha256") or "") if asset else ""
                    if sha256 and sha256 in seen_sha256:
                        continue
                    source_ids.append(asset_id)
                    seen_asset_ids.add(asset_id)
                    if sha256:
                        seen_sha256.add(sha256)

                current_revision = shot["draftRevision"]
                selected_snapshot = (
                    selected_video_step.get("inputSnapshot", {})
                    if selected_video_step
                    else {}
                )
                generated_revision = selected_snapshot.get("shotDraftRevision")
                generated_source_ids = selected_snapshot.get("sourceAssetIds")
                stale = bool(
                    selected_video
                    and (
                        (
                            generated_revision is not None
                            and generated_revision != current_revision
                        )
                        or (
                            isinstance(generated_source_ids, list)
                            and generated_source_ids != source_ids
                        )
                    )
                )
                uses_scene_look = (
                    shot["sceneLookUsage"] != "off"
                    and scene.get("selectedLookAssetId") is not None
                )
                if active_anchor:
                    state, next_action = "generating_anchor", "open_task"
                    state_label, action_label = "开场图生成中", "查看生成任务"
                elif active_video:
                    state, next_action = "generating_video", "open_task"
                    state_label, action_label = "视频生成中", "查看生成任务"
                elif selected_video and stale:
                    state, next_action = "stale", "open_versions"
                    state_label, action_label = "基于旧输入", "查看并决定是否重做"
                elif selected_video:
                    state, next_action = "approved", "open_versions"
                    state_label, action_label = "已批准", "查看视频版本"
                elif candidate_video:
                    state, next_action = "awaiting_review", "review_media"
                    state_label, action_label = "等待审核", "审核视频版本"
                elif shot["anchorMode"] in {"generate", "existing"} and not selected_anchor:
                    state, next_action = "needs_opening", "generate_anchor"
                    state_label = "待设计开场"
                    action_label = (
                        "生成片段开场图"
                        if shot["anchorMode"] == "generate"
                        else "选择已有开场图"
                    )
                elif any(
                    assets_by_id.get(asset_id) is None
                    or not assets_by_id[asset_id].get("contentReady", False)
                    for asset_id in source_ids
                ):
                    state, next_action = "blocked", "fix_inputs"
                    state_label, action_label = "生成条件未完成", "检查缺失素材"
                else:
                    state, next_action = "ready_video", "generate_video"
                    state_label, action_label = "可以生成视频", "生成视频片段"
                if state == "needs_opening":
                    blockers = [
                        "请先生成并批准片段开场图"
                        if shot["anchorMode"] == "generate"
                        else "请先上传或选择已有开场图"
                    ]
                elif state == "blocked":
                    blockers = ["实际参考图中存在不可读取的文件，请打开片段生成台检查"]
                else:
                    blockers = []

                selected_anchor_id = shot.get("selectedAnchorAssetId")
                person_reference_count = 0
                cat_reference_count = 0
                style_reference_count = 0
                prop_reference_count = 0
                for asset_id in source_ids:
                    asset = assets_by_id.get(asset_id, {})
                    semantic_key = str(asset.get("semanticKey") or "")
                    if semantic_key.startswith("person:"):
                        person_reference_count += 1
                    elif semantic_key.startswith("cat:"):
                        cat_reference_count += 1
                    elif semantic_key.startswith("style:"):
                        style_reference_count += 1
                    elif (
                        asset_id != selected_anchor_id
                        and asset_id != scene.get("selectedLookAssetId")
                    ):
                        prop_reference_count += 1
                reference_counts = {
                    "custom": sum(
                        1
                        for item in shot["referenceBindings"]
                        if item["usage"] != "approved_anchor"
                        and item["applyTo"] in {"video", "both"}
                        and item["assetId"] in source_ids
                    ),
                    "scene": int(
                        include_scene_look
                        and scene.get("selectedLookAssetId") in source_ids
                    ),
                    "project": sum(
                        1
                        for item in graph["project"]["defaultReferenceBindings"]
                        if item["assetId"] in source_ids
                    ),
                    "opening": int(
                        selected_anchor_id is not None and selected_anchor_id in source_ids
                    ),
                    "person": person_reference_count,
                    "cat": cat_reference_count,
                    "style": style_reference_count,
                    "prop": prop_reference_count,
                    "total": len(source_ids),
                }
                preview_asset = (
                    selected_video
                    or selected_anchor
                    or candidate_video
                    or (anchor_assets[-1] if anchor_assets else None)
                    or assets_by_id.get(scene.get("selectedLookAssetId"))
                )
                shot_summaries.append(
                    {
                        "shotId": shot["id"],
                        "sceneId": scene["id"],
                        "state": state,
                        "stateLabel": state_label,
                        "nextAction": next_action,
                        "primaryActionLabel": action_label,
                        "blockers": blockers,
                        "referenceCounts": reference_counts,
                        "anchorVersionCount": len(anchor_assets),
                        "videoVersionCount": len(video_assets),
                        "activeTaskCount": len(active_attempts),
                        "previewAssetId": preview_asset["id"] if preview_asset else None,
                        "previewMediaType": (
                            preview_asset["mediaType"] if preview_asset else None
                        ),
                        "usesSceneLook": uses_scene_look,
                        "inputHash": f"draft:{current_revision}:" + ",".join(source_ids),
                    }
                )
            scene_summaries.append(
                {
                    "sceneId": scene["id"],
                    "selectedLookAssetId": scene.get("selectedLookAssetId"),
                    "lookVersionCount": len(scene_look_versions),
                    "lookStatus": (
                        assets_by_id.get(scene.get("selectedLookAssetId"), {}).get("status")
                        if scene.get("selectedLookAssetId")
                        else "missing"
                    ),
                    "shots": shot_summaries,
                }
            )
        return {"projectId": str(project_id), "scenes": scene_summaries}

    @app.patch("/api/v1/projects/{project_id}")
    def update_project(
        project_id: uuid.UUID,
        payload: UpdateProjectRequest,
    ) -> dict[str, Any]:
        project = repository.update_project(
            project_id,
            title=payload.title,
            content_date=payload.content_date,
        )
        return {
            "id": str(project.id),
            "title": project.title,
            "contentDate": project.content_date.isoformat(),
            "status": project.status.value,
        }

    @app.put("/api/v1/projects/{project_id}/default-references")
    def update_project_default_references(
        project_id: uuid.UUID,
        payload: ReferencesRequest,
    ) -> dict[str, Any]:
        project = repository.update_project_default_references(project_id, payload.references)
        return {
            "projectId": str(project.id),
            "defaultReferenceBindings": [
                item.model_dump(mode="json", by_alias=True)
                for item in project.default_reference_bindings
            ],
        }

    @app.get("/api/v1/projects/{project_id}/visual-profile")
    def get_visual_profile(project_id: uuid.UUID) -> dict[str, Any]:
        return {
            **_visual_profile_json(repository.get_visual_profile(project_id)),
            "canonDefaults": repository.get_default_visual_profile(project_id).model_dump(
                mode="json",
                by_alias=True,
            ),
        }

    @app.put("/api/v1/projects/{project_id}/visual-profile")
    def update_visual_profile(
        project_id: uuid.UUID,
        payload: VisualProfileRequest,
    ) -> dict[str, Any]:
        return _visual_profile_json(repository.save_visual_profile(project_id, payload))

    @app.post("/api/v1/projects/{project_id}/restore-canon-references")
    def restore_project_canon_references(project_id: uuid.UUID) -> dict[str, Any]:
        return container.editing.restore_project_canon_references(project_id)

    @app.post("/api/v1/projects/{project_id}/scenes")
    def add_scene(project_id: uuid.UUID, payload: SceneRequest) -> dict[str, Any]:
        return _scene_json(repository.add_scene(project_id, payload))

    @app.patch("/api/v1/scenes/{scene_id}")
    def update_scene(scene_id: uuid.UUID, payload: SceneRequest) -> dict[str, Any]:
        return _scene_json(repository.update_scene(scene_id, payload))

    @app.delete("/api/v1/scenes/{scene_id}", status_code=204)
    def delete_scene(scene_id: uuid.UUID) -> None:
        repository.delete_scene(scene_id)

    @app.put("/api/v1/projects/{project_id}/scene-order")
    def reorder_scenes(project_id: uuid.UUID, payload: OrderRequest) -> dict[str, bool]:
        repository.reorder_scenes(project_id, tuple(payload.ids))
        return {"saved": True}

    @app.get("/api/v1/scenes/{scene_id}/creative-workflow")
    def creative_workflow(scene_id: uuid.UUID) -> dict[str, Any]:
        return container.editing.creative_workflow(scene_id)

    @app.post("/api/v1/scenes/{scene_id}/story-diagnoses")
    def diagnose_story(
        scene_id: uuid.UUID,
        payload: DiagnoseStoryRequest,
    ) -> dict[str, Any]:
        return _submit(
            job_registry,
            kind="story_diagnosis",
            key=f"scene:{scene_id}:story-diagnosis",
            fn=lambda: _story_diagnosis_json(
                container.editing.diagnose_story(
                    scene_id,
                    allow_paid_generation=payload.allow_paid_generation,
                )
            ),
            context={
                "sceneId": scene_id,
                "operationKey": "director:story-diagnosis",
            },
        )

    @app.post("/api/v1/steps/{step_id}/accept-story-diagnosis")
    def accept_story_diagnosis(
        step_id: uuid.UUID,
        payload: AcceptStoryDiagnosisRequest,
    ) -> dict[str, Any]:
        step = container.editing.accept_story_diagnosis(
            step_id,
            diagnosis=payload.diagnosis,
            selected_strategy=payload.selected_strategy,
            additional_instructions=payload.additional_instructions,
            preserve_original=payload.preserve_original,
        )
        return _creative_step_json(step)

    @app.post("/api/v1/scenes/{scene_id}/story-rewrites")
    def rewrite_story(
        scene_id: uuid.UUID,
        payload: RewriteStoryRequest,
    ) -> dict[str, Any]:
        return _submit(
            job_registry,
            kind="story_rewrite",
            key=f"scene:{scene_id}:story-rewrite:{payload.diagnosis_step_id}",
            fn=lambda: _story_rewrite_json(
                container.editing.rewrite_story(
                    scene_id,
                    diagnosis_step_id=payload.diagnosis_step_id,
                    allow_paid_generation=payload.allow_paid_generation,
                )
            ),
            context={
                "sceneId": scene_id,
                "stepId": payload.diagnosis_step_id,
                "operationKey": "director:story-rewrite",
            },
        )

    @app.post("/api/v1/steps/{step_id}/accept-story-rewrite")
    def accept_story_rewrite(
        step_id: uuid.UUID,
        payload: AcceptStoryRewriteRequest,
    ) -> dict[str, Any]:
        return _scene_json(
            container.editing.accept_story_rewrite(
                step_id,
                rewrite=payload.rewrite,
            )
        )

    @app.post("/api/v1/scenes/{scene_id}/shot-suggestions")
    def suggest_shots(scene_id: uuid.UUID, payload: SuggestShotsRequest) -> dict[str, Any]:
        return _submit(
            job_registry,
            kind="shot_suggestions",
            key=f"scene:{scene_id}:suggestions",
            fn=lambda: _suggestion_json(
                container.editing.suggest_shots(
                    scene_id,
                    allow_paid_generation=payload.allow_paid_generation,
                )
            ),
            context={"sceneId": scene_id, "operationKey": "director:shot-suggestions"},
        )

    @app.post("/api/v1/steps/{step_id}/accept-suggestions")
    def accept_suggestions(
        step_id: uuid.UUID,
        payload: AcceptSuggestionsRequest,
    ) -> list[dict[str, Any]]:
        return [
            _shot_json(item)
            for item in container.editing.accept_suggestions(
                step_id,
                look_plan=payload.look_plan,
                shots=tuple(payload.shots),
                apply_mode=payload.apply_mode,
                source_shot_revisions=payload.source_shot_revisions,
            )
        ]

    @app.post("/api/v1/scenes/{scene_id}/shots")
    def add_shot(scene_id: uuid.UUID, payload: ShotRequest) -> dict[str, Any]:
        return _shot_json(repository.add_shot(scene_id, payload))

    @app.patch("/api/v1/shots/{shot_id}")
    def update_shot(shot_id: uuid.UUID, payload: ShotRequest) -> dict[str, Any]:
        return _shot_json(repository.update_shot(shot_id, payload))

    @app.get("/api/v1/shots/{shot_id}/assist-context")
    def shot_assist_context(shot_id: uuid.UUID) -> dict[str, Any]:
        return container.editing.shot_assist_context(shot_id)

    @app.post("/api/v1/shots/{shot_id}/assist")
    def assist_shot(shot_id: uuid.UUID, payload: AssistShotRequest) -> dict[str, Any]:
        return _submit(
            job_registry,
            kind="shot_assistance",
            key=f"shot:{shot_id}:assist:{payload.source_draft_revision}",
            fn=lambda: _shot_assistance_json(
                container.editing.assist_shot(
                    shot_id,
                    source_draft_revision=payload.source_draft_revision,
                    candidate_asset_ids=tuple(payload.candidate_asset_ids),
                    allow_paid_generation=payload.allow_paid_generation,
                )
            ),
            context={
                "shotId": shot_id,
                "operationKey": "director:shot-assistance",
                "sourceDraftRevision": payload.source_draft_revision,
            },
        )

    @app.get("/api/v1/shots/{shot_id}/assist-analyses")
    def shot_assist_analyses(shot_id: uuid.UUID) -> list[dict[str, Any]]:
        return container.editing.list_shot_assistance(shot_id)

    @app.get("/api/v1/shots/{shot_id}/previous-tail")
    def previous_tail(shot_id: uuid.UUID) -> dict[str, Any]:
        return container.production.tail_frame_status(shot_id)

    @app.post("/api/v1/shots/{shot_id}/adopt-previous-tail-anchor")
    def adopt_previous_tail_anchor(shot_id: uuid.UUID) -> dict[str, Any]:
        shot = container.production.adopt_previous_tail_anchor(shot_id)
        return {
            **_shot_json(shot),
            "previousTail": container.production.tail_frame_status(shot_id),
        }

    @app.post("/api/v1/steps/{step_id}/accept-shot-assistance")
    def accept_shot_assistance(
        step_id: uuid.UUID,
        payload: AcceptShotAssistanceRequest,
    ) -> dict[str, Any]:
        return _shot_json(
            container.editing.accept_shot_assistance(
                step_id,
                source_draft_revision=payload.source_draft_revision,
                patch=payload.patch,
            )
        )

    @app.delete("/api/v1/shots/{shot_id}", status_code=204)
    def delete_shot(shot_id: uuid.UUID) -> None:
        repository.delete_shot(shot_id)

    @app.put("/api/v1/scenes/{scene_id}/shot-order")
    def reorder_shots(scene_id: uuid.UUID, payload: OrderRequest) -> dict[str, bool]:
        repository.reorder_shots(scene_id, tuple(payload.ids))
        return {"saved": True}

    @app.put("/api/v1/scenes/{scene_id}/look-asset")
    def select_scene_look_asset(
        scene_id: uuid.UUID,
        payload: SelectSceneLookRequest,
    ) -> dict[str, Any]:
        return _scene_json(repository.select_scene_look_asset(scene_id, payload.asset_id))

    @app.get("/api/v1/scenes/{scene_id}/look-draft")
    def get_scene_look_draft(scene_id: uuid.UUID) -> dict[str, Any]:
        return container.production.get_scene_look_draft(scene_id)

    @app.put("/api/v1/scenes/{scene_id}/look-draft")
    def save_scene_look_draft(
        scene_id: uuid.UUID,
        payload: SaveSceneLookDraftRequest,
    ) -> dict[str, Any]:
        return container.production.save_scene_look_draft(
            scene_id,
            expected_revision=payload.expected_revision,
            draft=payload.draft,
        )

    @app.post("/api/v1/scenes/{scene_id}/look-prompt-preview")
    def preview_scene_look_prompt(scene_id: uuid.UUID) -> dict[str, Any]:
        return container.production.preview_scene_look_prompt(scene_id)

    @app.get("/api/v1/scenes/{scene_id}/look-versions")
    def scene_look_versions(scene_id: uuid.UUID) -> list[dict[str, Any]]:
        scene = repository.get_scene(scene_id)
        versions: list[dict[str, Any]] = []
        for asset in repository.list_assets(project_id=scene.project_id):
            if asset.scene_id != scene_id or asset.role != "scene_look":
                continue
            item = {
                **_asset_json(asset),
                "selected": asset.id == scene.selected_look_asset_id,
                "attempt": None,
                "prompt": None,
                "inputSnapshot": {},
            }
            if asset.step_id is not None:
                step = repository.get_step(asset.step_id)
                prompt = repository.get_prompt(step.id)
                item["attempt"] = step.attempt
                item["inputSnapshot"] = step.input_snapshot
                item["prompt"] = (
                    None
                    if prompt is None
                    else {
                        "id": str(prompt.id),
                        "purpose": prompt.purpose.value,
                        "model": prompt.model,
                        "text": prompt.text,
                        "sha256": prompt.sha256,
                    }
                )
            versions.append(item)
        return sorted(versions, key=lambda item: int(item["attempt"] or 0), reverse=True)

    @app.get("/api/v1/shots/{shot_id}")
    def shot_trace(shot_id: uuid.UUID) -> dict[str, Any]:
        return repository.shot_trace(shot_id)

    @app.get("/api/v1/shots/{shot_id}/generation-workspace")
    def shot_generation_workspace(shot_id: uuid.UUID) -> dict[str, Any]:
        shot = repository.shot_trace(shot_id)
        scene = repository.get_scene(uuid.UUID(shot["sceneId"]))
        anchor_preview = container.production.preview_shot_prompt(
            shot_id,
            target=ReferenceTarget.ANCHOR,
        )
        video_preview = container.production.preview_shot_prompt(
            shot_id,
            target=ReferenceTarget.VIDEO,
        )
        assets = {
            item["id"]: item
            for item in repository.project_graph(scene.project_id)["assets"]
        }

        def reference_slots(preview: dict[str, Any], target: str) -> list[dict[str, Any]]:
            grouped: dict[str, list[dict[str, Any]]] = {
                "person": [],
                "cat": [],
                "style": [],
                "scene": [],
                "prop": [],
                "opening": [],
                "custom": [],
            }
            for reference in preview["references"]:
                asset = assets.get(reference["assetId"], {})
                semantic_key = str(asset.get("semanticKey") or "")
                if reference["assetId"] == shot.get("selectedAnchorAssetId"):
                    key = "opening"
                elif reference["sourceLayer"] == "scene_look":
                    key = "scene"
                elif semantic_key.startswith("person:"):
                    key = "person"
                elif semantic_key.startswith("cat:"):
                    key = "cat"
                elif semantic_key.startswith("style:"):
                    key = "style"
                elif reference["sourceLayer"] == "shot":
                    key = "custom"
                else:
                    key = "prop"
                grouped[key].append({**reference, "asset": asset})
            labels = {
                "person": "人物身份",
                "cat": "猫咪身份",
                "style": "系列画风",
                "scene": "场景视觉基准",
                "prop": "道具与构图",
                "opening": "批准开场图",
                "custom": "片段专用素材",
            }
            return [
                {"key": key, "label": labels[key], "target": target, "items": items}
                for key, items in grouped.items()
                if items
            ]

        active_statuses = {"pending", "submitting", "queued", "running"}
        active_tasks = [
            item for item in shot["attempts"] if item["status"] in active_statuses
        ]
        return {
            "shot": shot,
            "scene": {
                "id": str(scene.id),
                "title": scene.draft.title,
                "selectedLookAssetId": (
                    str(scene.selected_look_asset_id)
                    if scene.selected_look_asset_id
                    else None
                ),
            },
            "anchorPreview": anchor_preview,
            "videoPreview": video_preview,
            "referenceSlots": {
                "anchor": reference_slots(anchor_preview, "anchor"),
                "video": reference_slots(video_preview, "video"),
            },
            "previousTail": container.production.tail_frame_status(shot_id),
            "activeTasks": active_tasks,
        }

    @app.get("/api/v1/shots/{shot_id}/prompt-preview")
    def shot_prompt_preview(
        shot_id: uuid.UUID,
        target: ReferenceTarget = ReferenceTarget.VIDEO,
        regeneration_instruction: str | None = None,
    ) -> dict[str, Any]:
        return container.production.preview_shot_prompt(
            shot_id,
            target=target,
            regeneration_instruction=regeneration_instruction,
        )

    @app.put("/api/v1/shots/{shot_id}/references")
    def update_references(shot_id: uuid.UUID, payload: ReferencesRequest) -> dict[str, Any]:
        shot = repository.get_shot(shot_id)
        draft = shot.draft.model_copy(update={"reference_bindings": payload.references})
        return _shot_json(repository.update_shot(shot_id, draft))

    @app.post("/api/v1/projects/{project_id}/references")
    async def upload_reference(
        project_id: uuid.UUID,
        usage: ReferenceUsage = Form(...),
        role: ReferenceRole = Form(...),
        display_name: str | None = Form(default=None, alias="displayName"),
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        upload_root = container.runtime_settings.work_root / "uploads"
        upload_root.mkdir(parents=True, exist_ok=True)
        suffix = Path(file.filename or "reference.png").suffix or ".png"
        temporary = upload_root / f"{uuid.uuid4().hex}{suffix}"
        try:
            payload = await file.read(32 * 1024 * 1024 + 1)
            if len(payload) > 32 * 1024 * 1024:
                raise ValueError("reference image cannot exceed 32 MiB")
            temporary.write_bytes(payload)
            asset = container.production.import_reference(
                project_id=project_id,
                path=temporary,
                usage=usage.value,
                role=role.value,
                display_name=display_name,
            )
            return _asset_json(asset)
        finally:
            temporary.unlink(missing_ok=True)

    @app.post("/api/v1/shots/{shot_id}/anchors")
    def generate_anchor(shot_id: uuid.UUID, payload: GenerateRequest) -> dict[str, Any]:
        container.production.validate_anchor_request(
            shot_id,
            allow_paid_generation=payload.allow_paid_generation,
        )
        return _submit(
            job_registry,
            kind="generate_anchor",
            key=f"shot:{shot_id}:anchor",
            fn=lambda: container.production.generate_anchor(
                shot_id,
                allow_paid_generation=payload.allow_paid_generation,
                regenerate=payload.regenerate,
                reason=payload.retry_reason,
            ),
            context={"shotId": shot_id, "operationKey": "image:anchor"},
        )

    @app.post("/api/v1/scenes/{scene_id}/look-images")
    def generate_scene_look(
        scene_id: uuid.UUID,
        payload: GenerateSceneLookRequest,
    ) -> dict[str, Any]:
        container.production.validate_scene_look_request(
            scene_id,
            payload.draft_revision,
        )
        return _submit(
            job_registry,
            kind="generate_scene_look",
            key=f"scene:{scene_id}:look",
            fn=lambda: container.production.generate_scene_look(
                scene_id,
                allow_paid_generation=payload.allow_paid_generation,
                draft_revision=payload.draft_revision,
                regenerate=payload.regenerate,
                reason=payload.retry_reason,
            ),
            context={"sceneId": scene_id, "operationKey": "image:scene-look"},
        )

    @app.post("/api/v1/shots/{shot_id}/videos")
    def generate_video(shot_id: uuid.UUID, payload: GenerateRequest) -> dict[str, Any]:
        container.production.validate_video_request(
            shot_id,
            allow_paid_generation=payload.allow_paid_generation,
        )
        return _submit(
            job_registry,
            kind="generate_video",
            key=f"shot:{shot_id}:video",
            fn=lambda: container.production.generate_video(
                shot_id,
                allow_paid_generation=payload.allow_paid_generation,
                regenerate=payload.regenerate,
                reason=payload.retry_reason,
            ),
            context={"shotId": shot_id, "operationKey": "video:shot"},
        )

    @app.get("/api/v1/shots/{shot_id}/versions")
    def shot_versions(shot_id: uuid.UUID) -> list[dict[str, Any]]:
        return [
            _asset_json(item)
            for item in repository.list_assets(shot_id=shot_id)
            if item.media_type == "video"
        ]

    @app.post("/api/v1/assets/{asset_id}/review")
    def review_asset(asset_id: uuid.UUID, payload: ReviewRequest) -> dict[str, Any]:
        return container.production.decide_asset(
            asset_id,
            decision=payload.decision,
            reason=payload.reason,
            select=payload.select,
        )

    @app.post("/api/v1/shots/{shot_id}/versions/{asset_id}/select")
    def select_version(shot_id: uuid.UUID, asset_id: uuid.UUID) -> dict[str, Any]:
        asset = repository.get_asset(asset_id)
        if asset.shot_card_id != shot_id:
            raise ValueError("media version does not belong to the requested shot")
        if asset.media_type == "video" and asset.role in {"shot_video", "shot_video_edit"}:
            kind = "video"
        elif asset.media_type == "image" and asset.role == "shot_anchor":
            kind = "anchor"
        else:
            raise ValueError("asset is not a selectable shot anchor or video version")
        return _shot_json(repository.select_shot_asset(shot_id, kind=kind, asset_id=asset_id))

    @app.post("/api/v1/shots/{shot_id}/range-edits")
    def range_edit(shot_id: uuid.UUID, payload: RangeEditRequest) -> dict[str, Any]:
        return _submit(
            job_registry,
            kind="range_edit",
            key=f"shot:{shot_id}:range:{payload.source_asset_id}:{payload.start_ms}:{payload.end_ms}",
            fn=lambda: container.production.range_edit(
                shot_id,
                source_asset_id=payload.source_asset_id,
                start_ms=payload.start_ms,
                end_ms=payload.end_ms,
                instruction=payload.instruction,
                allow_paid_generation=payload.allow_paid_generation,
            ),
            context={"shotId": shot_id, "operationKey": "video:range-edit"},
        )

    @app.post("/api/v1/projects/{project_id}/sequences")
    def build_sequence(
        project_id: uuid.UUID,
        payload: BuildSequenceRequest | None = None,
    ) -> dict[str, Any]:
        transitions = {
            item.after_shot_id: item.transition
            for item in (payload.transitions if payload is not None else [])
        }
        return _submit(
            job_registry,
            kind="build_sequence",
            key=f"project:{project_id}:sequence",
            fn=lambda: _sequence_json(
                container.sequences.build_project_sequence(
                    project_id,
                    transitions=transitions,
                )
            ),
            context={"projectId": project_id, "operationKey": "sequence:build"},
        )

    @app.get("/api/v1/projects/{project_id}/sequences")
    def list_sequences(project_id: uuid.UUID) -> list[dict[str, Any]]:
        return [_sequence_json(item) for item in repository.list_sequences(project_id)]

    @app.post("/api/v1/projects/{project_id}/sequences/{sequence_id}/select")
    def select_sequence(
        project_id: uuid.UUID,
        sequence_id: uuid.UUID,
        payload: SelectSequenceRequest,
    ) -> dict[str, Any]:
        decided = repository.decide_sequence(sequence_id, approved=payload.approve)
        if not payload.approve:
            return _sequence_json(decided)
        return _sequence_json(repository.select_sequence(project_id, sequence_id))

    @app.post("/api/v1/steps/{step_id}/resume")
    def resume_step(step_id: uuid.UUID) -> dict[str, Any]:
        step = repository.get_step(step_id)
        return _submit(
            job_registry,
            kind="resume_step",
            key=f"step:{step_id}:resume",
            fn=lambda: container.production.resume_step(step_id, wait=False),
            context={
                "projectId": step.project_id,
                "sceneId": step.scene_id,
                "shotId": step.shot_card_id,
                "operationKey": "resume",
                "stepId": step_id,
            },
        )

    @app.get("/api/v1/steps/{step_id}/reconciliation-candidates")
    def reconciliation_candidates(step_id: uuid.UUID) -> tuple[dict[str, Any], ...]:
        return container.production.reconcile_candidates(step_id)

    @app.post("/api/v1/steps/{step_id}/reconcile")
    def reconcile_step(step_id: uuid.UUID, payload: ReconcileRequest) -> dict[str, Any]:
        return container.production.reconcile(step_id, task_id=payload.provider_task_id)

    @app.get("/api/v1/assets/{asset_id}/content")
    def asset_content(asset_id: uuid.UUID) -> FileResponse:
        asset = repository.get_asset(asset_id)
        if asset.path is None:
            raise HTTPException(status_code=404, detail="asset content requires repair")
        resolved = asset.path.expanduser().resolve()
        if not any(resolved.is_relative_to(root) for root in roots):
            raise HTTPException(status_code=403, detail="asset is outside configured media roots")
        if not resolved.is_file():
            raise HTTPException(status_code=404, detail="asset file does not exist")
        return FileResponse(resolved)

    @app.get("/api/v1/canon")
    def list_canon() -> list[dict[str, Any]]:
        return [
            _asset_json(item)
            for item in repository.list_assets()
            if item.scope == "canon" and item.status == "approved"
        ]

    @app.get("/api/v1/projects/{project_id}/tasks")
    def project_tasks(project_id: uuid.UUID) -> list[dict[str, Any]]:
        repository.get_project(project_id)
        return [
            _task_json(item)
            for item in reversed(repository.list_steps(project_id=project_id))
        ][:100]

    @app.get("/api/v1/jobs")
    def list_jobs() -> list[dict[str, Any]]:
        return [item.to_dict() for item in job_registry.list()]

    @app.get("/api/v1/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        return job_registry.get(job_id).to_dict()

    if static_dir is not None:
        if not (static_dir / "index.html").is_file():
            raise ValueError(f"static directory has no index.html: {static_dir}")
        app.mount("/", _SPAStaticFiles(directory=static_dir, html=True), name="web")
    return app


def _submit(
    jobs: JobRegistry,
    *,
    kind: str,
    key: str,
    fn: Callable[[], Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    try:
        record = jobs.submit(kind=kind, dedup_key=key, fn=fn, context=context)
    except JobConflictError as exc:
        return {"jobId": exc.job_id, "status": "running", "reused": True}
    return {"jobId": record.job_id, "status": record.status, "reused": False}


def _scene_json(item: Any) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "projectId": str(item.project_id),
        "order": item.order,
        **item.draft.model_dump(mode="json", by_alias=True),
        "status": item.status.value,
        "selectedLookAssetId": (
            None
            if item.selected_look_asset_id is None
            else str(item.selected_look_asset_id)
        ),
        "lookDraftRevision": item.look_draft_revision,
    }


def _shot_json(item: Any) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "sceneId": str(item.scene_id),
        "order": item.order,
        **item.draft.model_dump(mode="json", by_alias=True),
        "draftRevision": item.draft_revision,
        "useSceneLook": item.draft.use_scene_look,
        "status": item.status.value,
        "selectedAnchorAssetId": None
        if item.selected_anchor_asset_id is None
        else str(item.selected_anchor_asset_id),
        "selectedVideoAssetId": None
        if item.selected_video_asset_id is None
        else str(item.selected_video_asset_id),
    }


def _asset_json(item: Any) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "role": item.role,
        "mediaType": item.media_type,
        "scope": item.scope,
        "status": item.status,
        "projectId": None if item.project_id is None else str(item.project_id),
        "sceneId": None if item.scene_id is None else str(item.scene_id),
        "shotId": None if item.shot_card_id is None else str(item.shot_card_id),
        "producingStepId": None if item.step_id is None else str(item.step_id),
        "sha256": item.sha256,
        "semanticKey": item.semantic_key,
        "metadata": item.metadata,
        "contentReady": item.content_ready,
        "displayName": item.display_name,
        "referencePurpose": item.reference_purpose,
        "visualProfileRevisionId": item.metadata.get("visualProfileRevisionId"),
        "lookDraftRevision": item.metadata.get("lookDraftRevision"),
        "createdAt": None if item.created_at is None else item.created_at.isoformat(),
    }


def _shot_assistance_json(item: Any) -> dict[str, Any]:
    return {
        "stepId": str(item.step_id),
        "analysis": item.analysis.model_dump(mode="json", by_alias=True),
    }


def _story_diagnosis_json(item: Any) -> dict[str, Any]:
    return {
        "stepId": str(item.step_id),
        "diagnosis": item.output.model_dump(mode="json", by_alias=True),
    }


def _story_rewrite_json(item: Any) -> dict[str, Any]:
    return {
        "stepId": str(item.step_id),
        "rewrite": item.output.model_dump(mode="json", by_alias=True),
    }


def _creative_step_json(item: Any) -> dict[str, Any]:
    return {
        "stepId": str(item.id),
        "operationKey": item.operation_key,
        "status": item.status.value,
        "attempt": item.attempt,
        "model": item.model,
        "sourceHash": item.input_snapshot.get("sourceHash"),
        "providerOutput": item.input_snapshot.get("providerOutput"),
        "acceptedOutput": item.input_snapshot.get("acceptedOutput"),
        "acceptedAt": item.input_snapshot.get("acceptedAt"),
        "error": item.error,
        "createdAt": None if item.created_at is None else item.created_at.isoformat(),
    }


def _task_json(item: Any) -> dict[str, Any]:
    return {
        "stepId": str(item.id),
        "projectId": str(item.project_id),
        "sceneId": None if item.scene_id is None else str(item.scene_id),
        "shotId": None if item.shot_card_id is None else str(item.shot_card_id),
        "kind": item.kind.value,
        "status": item.status.value,
        "attempt": item.attempt,
        "operationKey": item.operation_key,
        "provider": item.provider,
        "providerTaskId": item.provider_task_id,
        "model": item.model,
        "inputSnapshot": item.input_snapshot,
        "error": item.error,
        "createdAt": None if item.created_at is None else item.created_at.isoformat(),
    }


def _visual_profile_json(item: Any) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "projectId": str(item.project_id),
        "revision": item.revision,
        "profileHash": item.profile_hash,
        "sourceProfileId": item.source_profile_id,
        **item.draft.model_dump(mode="json", by_alias=True),
        "createdAt": None if item.created_at is None else item.created_at.isoformat(),
    }


def _suggestion_json(item: Any) -> dict[str, Any]:
    return {
        "stepId": str(item.step_id),
        "output": item.output.model_dump(mode="json", by_alias=True),
    }


def _sequence_json(item: Any) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "projectId": str(item.project_id),
        "revision": item.revision,
        "parentSequenceId": None
        if item.parent_sequence_id is None
        else str(item.parent_sequence_id),
        "renderedAssetId": None if item.rendered_asset_id is None else str(item.rendered_asset_id),
        "status": item.status.value if isinstance(item.status, SequenceStatus) else item.status,
        "plan": item.plan.model_dump(mode="json"),
    }
