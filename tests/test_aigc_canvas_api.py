from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from cat_video_generator.interfaces.api import create_app
from cat_video_generator.interfaces.jobs import JobRegistry


class _CanvasService:
    def __init__(self) -> None:
        self.brief: dict[str, object] | None = None
        self.subject: dict[str, object] | None = None
        self.saved_layout: dict[str, object] | None = None
        self.asset_bindings: dict[str, object] | None = None
        self.approved_revision: uuid.UUID | None = None
        self.storyboard_mode: str | None = None
        self.storyboard_references: tuple[uuid.UUID, ...] = ()
        self.manual_storyboard: dict[str, object] | None = None
        self.subject_completion_run_id = uuid.uuid4()

    def save_brief(self, project_id: uuid.UUID, payload: object) -> dict[str, object]:
        self.brief = payload.model_dump(by_alias=True, mode="json")  # type: ignore[attr-defined]
        return {"id": str(uuid.uuid4()), "projectId": str(project_id), **self.brief}

    def create_subject(self, project_id: uuid.UUID, payload: object) -> dict[str, object]:
        self.subject = payload.model_dump(by_alias=True, mode="json")  # type: ignore[attr-defined]
        return {"id": str(uuid.uuid4()), "projectId": str(project_id), **self.subject}

    def list_subjects(self, project_id: uuid.UUID) -> list[dict[str, object]]:
        return [{
            "id": str(uuid.uuid4()),
            "projectId": str(project_id),
            "revisionId": str(uuid.uuid4()),
            "revision": 1,
            "status": "approved",
            "name": "蓝色汽水罐",
            "kind": "product",
            "role": "hero_product",
            "identityAnchors": ["蓝色罐身"],
            "immutableTraits": ["标签文字不变"],
            "references": [],
        }]

    def bind_canvas_node_assets(
        self,
        node_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: object,
    ) -> dict[str, object]:
        assert expected_revision == 1
        self.asset_bindings = payload.model_dump(by_alias=True, mode="json")  # type: ignore[attr-defined]
        return {
            "id": str(node_id),
            "type": "ReferenceAssetNode",
            "revision": 2,
            "status": "ready",
            "data": {"assets": self.asset_bindings["bindings"]},
        }

    def create_subject_completion_run(
        self, project_id: uuid.UUID, payload: object
    ) -> dict[str, object]:
        return {
            "id": str(self.subject_completion_run_id),
            "projectId": str(project_id),
            "subjectId": str(payload.subject_id),  # type: ignore[attr-defined]
            "status": "pending",
            "missingFields": ["immutableTraits", "dramaticFunction"],
        }

    def get_subject_completion_run(self, run_id: uuid.UUID) -> dict[str, object]:
        assert run_id == self.subject_completion_run_id
        return {
            "id": str(run_id),
            "status": "awaiting_review",
            "proposal": {"identityAnchors": ["灰白虎斑猫"]},
            "promptId": str(uuid.uuid4()),
        }

    def apply_subject_completion(
        self, run_id: uuid.UUID, payload: object
    ) -> dict[str, object]:
        assert run_id == self.subject_completion_run_id
        return {
            "runId": str(run_id),
            "status": "applied",
            "revision": 2,
            "acceptedFields": payload.accepted_fields,  # type: ignore[attr-defined]
        }

    def list_project_assets(
        self, project_id: uuid.UUID, *, media_kind: str | None = None
    ) -> list[dict[str, object]]:
        return [
            {
                "id": str(uuid.uuid4()),
                "projectId": str(project_id),
                "mediaType": media_kind or "image",
                "status": "ready",
            }
        ]

    def create_video_filmstrip_run(
        self, asset_id: uuid.UUID, *, frame_count: int
    ) -> dict[str, object]:
        return {
            "assetId": str(asset_id),
            "frameCount": frame_count,
            "status": "pending",
            "stepId": str(uuid.uuid4()),
            "frames": [],
        }

    def get_video_filmstrip(
        self, asset_id: uuid.UUID, *, frame_count: int
    ) -> dict[str, object]:
        return {
            "assetId": str(asset_id),
            "frameCount": frame_count,
            "status": "ready",
            "frames": [
                {
                    "assetId": str(uuid.uuid4()),
                    "timestampMs": index * 1_000,
                    "contentUrl": f"/api/v1/assets/frame-{index}/content",
                }
                for index in range(frame_count)
            ],
        }

    def save_node_generation_config(
        self,
        node_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: object,
    ) -> dict[str, object]:
        assert expected_revision == 2
        return {
            "id": str(uuid.uuid4()),
            "canvasNodeId": str(node_id),
            "revision": 3,
            **payload.model_dump(mode="json", by_alias=True),  # type: ignore[attr-defined]
        }

    def list_provider_capabilities(
        self, *, media_kind: str | None = None
    ) -> list[dict[str, object]]:
        return [
            {
                "provider": "ark",
                "model": "seedance-2",
                "mediaKind": media_kind or "video",
                "capabilities": {
                    "modes": ["text_to_video", "image_to_video"],
                    "aspectRatios": ["16:9", "9:16"],
                    "resolutions": ["720p"],
                    "durations": [5, 10],
                    "candidateCounts": [1],
                },
            }
        ]

    def approve_story_revision(self, revision_id: uuid.UUID) -> dict[str, object]:
        self.approved_revision = revision_id
        return {"id": str(revision_id), "status": "approved"}

    def create_storyboard(
        self,
        project_id: uuid.UUID,
        *,
        idempotency_key: str | None = None,
        creation_mode: str = "from_story",
        reference_asset_ids: tuple[uuid.UUID, ...] = (),
        instruction: str | None = None,
    ) -> dict[str, object]:
        if self.approved_revision is None:
            raise ValueError("故事尚未人工批准，不能生成分镜")
        self.storyboard_mode = creation_mode
        self.storyboard_references = reference_asset_ids
        return {
            "projectId": str(project_id),
            "status": "ready",
            "beats": [],
            "creationMode": creation_mode,
            "instruction": instruction,
            "idempotencyKey": idempotency_key,
        }

    def save_manual_storyboard(
        self,
        project_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: object,
    ) -> dict[str, object]:
        self.manual_storyboard = payload.model_dump(by_alias=True, mode="json")  # type: ignore[attr-defined]
        return {
            "projectId": str(project_id),
            "revision": expected_revision + 1,
            "status": "awaiting_review",
            "shotCount": len(self.manual_storyboard["shots"]),  # type: ignore[arg-type]
        }

    def get_prompt_run(self, prompt_id: uuid.UUID) -> dict[str, object]:
        return {
            "id": str(prompt_id),
            "finalPrompt": "精确发送给供应商的 Prompt",
            "providerInternalTransform": "not_observable",
            "retryChain": [],
        }

    def get_canvas(self, project_id: uuid.UUID) -> dict[str, object]:
        return {
            "projectId": str(project_id),
            "layoutVersion": 3,
            "nodes": [],
            "edges": [],
            "syncStatus": "saved",
        }

    def save_canvas_layout(
        self,
        project_id: uuid.UUID,
        *,
        expected_version: int,
        payload: object,
    ) -> dict[str, object]:
        assert expected_version == 3
        data = payload.model_dump(by_alias=True, mode="json")  # type: ignore[attr-defined]
        self.saved_layout = data
        return {
            "projectId": str(project_id),
            "layoutVersion": 4,
            "syncStatus": "saved",
            **data,
        }

    def list_canvas_templates(self) -> list[dict[str, object]]:
        return [
            {
                "key": "short_drama",
                "title": "AIGC 短剧",
                "defaultCandidateCount": 3,
                "nodeTypes": ["BriefNode", "StoryPlannerNode"],
            },
            {
                "key": "product_ad",
                "title": "产品广告",
                "defaultCandidateCount": 4,
                "nodeTypes": ["SubjectNode", "GenerationBatchNode"],
            },
            {
                "key": "blank",
                "title": "空白画布",
                "defaultCandidateCount": 4,
                "nodeTypes": [],
            },
        ]

    def instantiate_template(self, project_id: uuid.UUID, payload: object) -> dict[str, object]:
        return {
            "projectId": str(project_id),
            "templateKey": payload.template_key.value,  # type: ignore[attr-defined]
            "graphVersion": 1,
        }

    def create_video_edit_recipe(self, payload: object) -> dict[str, object]:
        return {
            "id": str(uuid.uuid4()),
            "revision": 1,
            **payload.model_dump(by_alias=True, mode="json"),  # type: ignore[attr-defined]
        }

    def compile_video_edit_recipe(self, recipe_id: uuid.UUID) -> dict[str, object]:
        return {
            "recipeId": str(recipe_id),
            "mode": "two_stage",
            "imageCallCount": 2,
            "videoCallCount": 1,
            "estimatedCostMicros": 11_000,
        }

    def submit_video_edit_recipe(self, recipe_id: uuid.UUID, payload: object) -> dict[str, object]:
        return {
            "recipeId": str(recipe_id),
            "jobId": str(uuid.uuid4()),
            "status": "queued",
            "idempotencyKey": payload.idempotency_key,  # type: ignore[attr-defined]
        }


def _client(tmp_path: Path, service: _CanvasService) -> TestClient:
    container = SimpleNamespace(
        repository=object(),
        editing=object(),
        canvas_v2=service,
        runtime_settings=SimpleNamespace(work_root=tmp_path, asset_root=tmp_path),
    )
    return TestClient(
        create_app(
            container,  # type: ignore[arg-type]
            job_registry=JobRegistry(inline=True),
        )
    )


def test_v2_brief_and_generic_subject_endpoints(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()

    brief_response = client.put(
        f"/api/v2/projects/{project_id}/brief",
        json={
            "theme": "小孩与猫在雨前收画",
            "audience": "亲子观众",
            "genre": "治愈短剧",
            "tone": "紧凑温暖",
            "aspectRatio": "9:16",
            "targetDurationSeconds": 60,
            "constraints": [],
        },
    )
    subject_response = client.post(
        f"/api/v2/projects/{project_id}/subjects",
        json={
            "name": "灰灰",
            "kind": "animal",
            "role": "co_protagonist",
            "identityAnchors": ["灰白虎斑猫"],
            "immutableTraits": ["尾巴纹路不变"],
        },
    )

    assert brief_response.status_code == 200
    assert brief_response.json()["targetDurationSeconds"] == 60
    assert subject_response.status_code == 201
    assert subject_response.json()["kind"] == "animal"


def test_v2_lists_subject_revisions_and_binds_assets_to_reference_nodes(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()
    node_id = uuid.uuid4()
    asset_id = uuid.uuid4()

    subjects = client.get(f"/api/v2/projects/{project_id}/subjects")
    bound = client.put(
        f"/api/v2/canvas/nodes/{node_id}/asset-bindings",
        headers={"If-Match": "1"},
        json={
            "bindings": [{"assetId": str(asset_id), "semanticRole": "packshot_front"}],
            "allowMove": False,
        },
    )

    assert subjects.status_code == 200
    assert subjects.json()[0]["revision"] == 1
    assert subjects.json()[0]["kind"] == "product"
    assert bound.status_code == 200
    assert bound.json()["revision"] == 2
    assert service.asset_bindings == {
        "bindings": [{"assetId": str(asset_id), "semanticRole": "packshot_front"}],
        "allowMove": False,
    }


def test_v2_storyboard_is_blocked_until_human_story_approval(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()
    revision_id = uuid.uuid4()

    blocked = client.post(
        f"/api/v2/projects/{project_id}/storyboard-runs",
        json={"idempotencyKey": "blocked-0001"},
    )
    approved = client.post(f"/api/v2/story-revisions/{revision_id}/approve", json={})
    ready = client.post(
        f"/api/v2/projects/{project_id}/storyboard-runs",
        json={"idempotencyKey": "ready-0001"},
    )

    assert blocked.status_code == 202
    assert blocked.json()["status"] == "failed"
    assert "人工批准" in blocked.json()["error"]["message"]
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert ready.status_code == 202
    assert ready.json()["kind"] == "storyboard"
    assert ready.json()["context"]["creationMode"] == "from_story"


def test_v2_character_storyboard_and_manual_draft_use_distinct_paths(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    child_asset_id = uuid.uuid4()
    cat_asset_id = uuid.uuid4()
    client.post(f"/api/v2/story-revisions/{revision_id}/approve", json={})

    generated = client.post(
        f"/api/v2/projects/{project_id}/storyboard-runs",
        json={
            "creationMode": "from_characters",
            "referenceAssetIds": [str(child_asset_id), str(cat_asset_id)],
            "instruction": "孩子与猫咪一起整理窗台",
            "idempotencyKey": "characters-0001",
        },
    )
    manual = client.put(
        f"/api/v2/projects/{project_id}/storyboard-drafts",
        headers={"If-Match": "1"},
        json={
            "healingRecipe": True,
            "shots": [{
                "order": 1,
                "durationSeconds": 15,
                "title": "亮叶",
                "action": "孩子蹲下看叶片，猫咪在旁边嗅闻水珠",
                "shotSize": "中景",
                "lighting": "雨后柔光",
                "dialogue": "",
                "soundEffect": "雨滴与猫咪脚步",
                "camera": "固定机位",
                "prompt": "固定儿童与猫咪，雨后水彩庭院",
            }],
        },
    )

    assert generated.status_code == 202
    assert generated.json()["context"]["creationMode"] == "from_characters"
    assert service.storyboard_mode == "from_characters"
    assert service.storyboard_references == (child_asset_id, cat_asset_id)
    assert manual.status_code == 200
    assert manual.json()["status"] == "awaiting_review"
    assert service.manual_storyboard is not None


def test_v2_prompt_and_optimistic_canvas_layout_endpoints(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()
    prompt_id = uuid.uuid4()

    prompt = client.get(f"/api/v2/prompt-runs/{prompt_id}")
    canvas = client.get(f"/api/v2/projects/{project_id}/canvas")
    saved = client.patch(
        f"/api/v2/projects/{project_id}/canvas/layout",
        headers={"If-Match": "3"},
        json={
            "nodes": [{"nodeId": str(uuid.uuid4()), "x": 120, "y": 80}],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
            "operations": [{"operationId": str(uuid.uuid4()), "type": "move_node"}],
        },
    )

    assert prompt.status_code == 200
    assert prompt.json()["providerInternalTransform"] == "not_observable"
    assert canvas.json()["layoutVersion"] == 3
    assert saved.status_code == 200
    assert saved.json()["layoutVersion"] == 4


def test_v2_layout_ignores_legacy_business_edge_snapshot(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()
    source_id = uuid.uuid4()
    target_id = uuid.uuid4()

    saved = client.patch(
        f"/api/v2/projects/{project_id}/canvas/layout",
        headers={"If-Match": "3"},
        json={
            "nodes": [],
            "edges": [
                {
                    "id": str(uuid.uuid4()),
                    "sourceNodeId": str(source_id),
                    "sourceNodeType": "ReferenceAssetNode",
                    "sourcePort": "media_reference[]",
                    "targetNodeId": str(target_id),
                    "targetNodeType": "GenerationBatchNode",
                    "targetPort": "media_reference[]",
                    "relationType": "media_reference[]->media_reference[]",
                    "revision": 1,
                }
            ],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
            "operations": [{"operationId": str(uuid.uuid4()), "type": "move_node"}],
        },
    )

    assert saved.status_code == 200
    assert service.saved_layout is not None
    assert "edges" not in service.saved_layout


def test_v2_template_library_and_product_default(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()

    templates = client.get("/api/v2/canvas-templates")
    instantiated = client.post(
        f"/api/v2/projects/{project_id}/template-instances",
        json={"templateKey": "product_ad"},
    )

    assert templates.status_code == 200
    product = next(item for item in templates.json() if item["key"] == "product_ad")
    assert product["defaultCandidateCount"] == 4
    assert instantiated.status_code == 201
    assert instantiated.json()["templateKey"] == "product_ad"


def test_v2_video_edit_recipe_compile_and_submit_contract(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    recipe = client.post(
        "/api/v2/video-edit-recipes",
        json={
            "projectId": str(uuid.uuid4()),
            "sourceAssetId": str(uuid.uuid4()),
            "startMs": 4_000,
            "endMs": 10_000,
            "instruction": "保持包装文字并修复手部",
            "referenceAssetIds": [str(uuid.uuid4())],
            "annotations": [
                {
                    "frameTimestampMs": 5_000,
                    "tool": "rectangle",
                    "points": [{"x": 0.2, "y": 0.2}, {"x": 0.6, "y": 0.7}],
                }
            ],
        },
    )
    recipe_id = recipe.json()["id"]
    compiled = client.post(f"/api/v2/video-edit-recipes/{recipe_id}/compile", json={})
    submitted = client.post(
        f"/api/v2/video-edit-recipes/{recipe_id}/submit",
        json={"idempotencyKey": "edit-test-0001", "acceptEstimatedCostMicros": 11_000},
    )

    assert recipe.status_code == 201
    assert compiled.json()["mode"] == "two_stage"
    assert submitted.status_code == 202
    assert submitted.json()["status"] == "queued"


def test_subject_assistant_is_explicit_async_and_requires_human_apply(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()
    subject_id = uuid.uuid4()

    queued = client.post(
        f"/api/v2/projects/{project_id}/subject-assistant-runs",
        json={
            "subjectId": str(subject_id),
            "idempotencyKey": "subject-assistant-0001",
            "instruction": "补齐跨镜头身份锚点",
        },
    )
    inspected = client.get(
        f"/api/v2/subject-assistant-runs/{service.subject_completion_run_id}"
    )
    applied = client.post(
        f"/api/v2/subject-assistant-runs/{service.subject_completion_run_id}/apply",
        json={
            "acceptedFields": ["immutableTraits"],
            "finalDraft": {
                "name": "灰灰",
                "kind": "animal",
                "role": "co_protagonist",
                "identityAnchors": ["灰白虎斑猫"],
                "immutableTraits": ["额头 M 纹不变"],
                "relationshipNotes": "",
                "dramaticFunction": "",
                "visualRisks": [],
                "references": [],
            },
        },
    )

    assert queued.status_code == 202
    assert queued.json()["status"] == "pending"
    assert inspected.json()["status"] == "awaiting_review"
    assert applied.status_code == 201
    assert applied.json()["acceptedFields"] == ["immutableTraits"]


def test_v2_canvas_asset_history_filters_by_media_kind(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()

    response = client.get(f"/api/v2/projects/{project_id}/assets?kind=video")

    assert response.status_code == 200
    assert response.json()[0]["mediaType"] == "video"


def test_video_filmstrip_queues_once_and_returns_distinct_cached_frames(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    asset_id = uuid.uuid4()

    queued = client.post(f"/api/v2/assets/{asset_id}/filmstrip-runs?frameCount=12")
    ready = client.get(f"/api/v2/assets/{asset_id}/filmstrip?frameCount=12")

    assert queued.status_code == 202
    assert queued.json()["status"] == "pending"
    assert ready.status_code == 200
    frames = ready.json()["frames"]
    assert len(frames) == 12
    assert len({frame["timestampMs"] for frame in frames}) == 12
    assert len({frame["contentUrl"] for frame in frames}) == 12


def test_generation_config_and_capabilities_are_server_driven(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    node_id = uuid.uuid4()
    reference_id = uuid.uuid4()

    capabilities = client.get("/api/v2/provider-capabilities?mediaKind=video")
    saved = client.put(
        f"/api/v2/canvas/nodes/{node_id}/generation-config",
        headers={"If-Match": "2"},
        json={
            "provider": "ark",
            "model": "seedance-2",
            "mode": "image_to_video",
            "aspectRatio": "9:16",
            "resolution": "720p",
            "durationSeconds": 5,
            "audioEnabled": True,
            "candidateCount": 1,
            "draftPrompt": "保持主体身份并缓慢推近",
            "autoValidate": True,
            "autoLink": True,
            "actualReferences": [
                {
                    "assetId": str(reference_id),
                    "semanticRole": "protagonist",
                    "providerIncluded": True,
                }
            ],
        },
    )

    assert capabilities.status_code == 200
    assert capabilities.json()[0]["capabilities"]["resolutions"] == ["720p"]
    assert saved.status_code == 201
    assert saved.json()["revision"] == 3
