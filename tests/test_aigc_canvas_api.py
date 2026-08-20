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
        self.approved_revision: uuid.UUID | None = None

    def save_brief(self, project_id: uuid.UUID, payload: object) -> dict[str, object]:
        self.brief = payload.model_dump(by_alias=True, mode="json")  # type: ignore[attr-defined]
        return {"id": str(uuid.uuid4()), "projectId": str(project_id), **self.brief}

    def create_subject(self, project_id: uuid.UUID, payload: object) -> dict[str, object]:
        self.subject = payload.model_dump(by_alias=True, mode="json")  # type: ignore[attr-defined]
        return {"id": str(uuid.uuid4()), "projectId": str(project_id), **self.subject}

    def approve_story_revision(self, revision_id: uuid.UUID) -> dict[str, object]:
        self.approved_revision = revision_id
        return {"id": str(revision_id), "status": "approved"}

    def create_storyboard(self, project_id: uuid.UUID) -> dict[str, object]:
        if self.approved_revision is None:
            raise ValueError("故事尚未人工批准，不能生成分镜")
        return {"projectId": str(project_id), "status": "ready", "beats": []}

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


def test_v2_storyboard_is_blocked_until_human_story_approval(tmp_path: Path) -> None:
    service = _CanvasService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()
    revision_id = uuid.uuid4()

    blocked = client.post(f"/api/v2/projects/{project_id}/storyboard-runs", json={})
    approved = client.post(f"/api/v2/story-revisions/{revision_id}/approve", json={})
    ready = client.post(f"/api/v2/projects/{project_id}/storyboard-runs", json={})

    assert blocked.status_code == 422
    assert "人工批准" in blocked.json()["detail"]
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert ready.status_code == 202


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
