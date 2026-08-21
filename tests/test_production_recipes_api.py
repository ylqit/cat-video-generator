from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from cat_video_generator.interfaces.api import create_app
from cat_video_generator.interfaces.jobs import JobRegistry


class _RecipeService:
    def __init__(self) -> None:
        self.instance_id = uuid.uuid4()
        self.project_id = uuid.uuid4()
        self.group_id = uuid.uuid4()
        self.last_payload: object | None = None
        self.last_expected_revision: int | None = None

    def list_recipes(self) -> list[dict[str, object]]:
        return [{"key": "healing_child_cat_v1", "title": "一人一猫治愈短片"}]

    def create_instance(self, project_id: uuid.UUID, payload: object) -> dict[str, object]:
        self.project_id = project_id
        self.last_payload = payload
        return {
            "id": str(self.instance_id),
            "projectId": str(project_id),
            "recipeKey": "healing_child_cat_v1",
            "revision": 1,
            "targetDurationSeconds": payload.target_duration_seconds,  # type: ignore[attr-defined]
            "qualityTier": payload.quality_tier,  # type: ignore[attr-defined]
            "stage": "concept",
        }

    def get_instance(self, instance_id: uuid.UUID) -> dict[str, object]:
        assert instance_id == self.instance_id
        return {
            "id": str(instance_id),
            "projectId": str(self.project_id),
            "revision": 1,
            "stage": "concept",
        }

    def update_instance(
        self,
        instance_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: object,
    ) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.last_expected_revision = expected_revision
        self.last_payload = payload
        return {"id": str(instance_id), "revision": expected_revision + 1}

    def record_review(
        self,
        instance_id: uuid.UUID,
        payload: object,
        *,
        episode_rules: object | None = None,
    ) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.last_payload = payload
        return {
            "id": str(uuid.uuid4()),
            "recipeInstanceId": str(instance_id),
            "decision": payload.decision,  # type: ignore[attr-defined]
            "targetRevision": payload.target_revision,  # type: ignore[attr-defined]
        }

    def run_story(self, instance_id: uuid.UUID, payload: object) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.last_payload = payload
        return {"id": "story-run", "status": "succeeded"}

    def run_storyboard(self, instance_id: uuid.UUID, payload: object) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.last_payload = payload
        return {"id": "storyboard-run", "status": "succeeded"}

    def run_anchor(
        self, instance_id: uuid.UUID, shot_id: uuid.UUID, payload: object
    ) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.last_payload = payload
        return {"shotId": str(shot_id), "status": "queued"}

    def run_video(
        self, instance_id: uuid.UUID, shot_id: uuid.UUID, payload: object
    ) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.last_payload = payload
        return {"shotId": str(shot_id), "status": "queued"}

    def run_sequence(self, instance_id: uuid.UUID, payload: object) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.last_payload = payload
        return {"id": "sequence-run", "status": "content_review"}

    def enqueue_recipe_task(
        self,
        instance_id: uuid.UUID,
        *,
        operation_key: str,
        payload: object,
        shot_id: uuid.UUID | None = None,
        group_id: uuid.UUID | None = None,
        creation_mode: str | None = None,
    ) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.last_payload = payload
        return {
            "jobId": str(uuid.uuid4()),
            "kind": "director",
            "status": "pending",
            "projectId": str(self.project_id),
            "shotId": None if shot_id is None else str(shot_id),
            "canvasGroupId": None if group_id is None else str(group_id),
            "recipeInstanceId": str(instance_id),
            "creationMode": creation_mode,
            "operationKey": operation_key,
            "workflowStage": operation_key.removeprefix("recipe:"),
            "phase": operation_key.removeprefix("recipe:"),
        }

    def enqueue_group_task(self, group_id: uuid.UUID, payload: object) -> dict[str, object]:
        assert group_id == self.group_id
        self.last_payload = payload
        return {
            "jobId": str(uuid.uuid4()),
            "kind": "director",
            "status": "pending",
            "projectId": str(self.project_id),
            "canvasGroupId": str(group_id),
            "recipeInstanceId": str(self.instance_id),
            "operationKey": "canvas-group:run",
            "workflowStage": "creative",
            "phase": "creative",
        }

    def compile_group(self, group_id: uuid.UUID) -> dict[str, object]:
        assert group_id == self.group_id
        return {
            "groupId": str(group_id),
            "projectId": str(self.project_id),
            "recipeInstanceId": str(self.instance_id),
            "phase": "creative",
            "primaryAction": "补全创意输入",
            "blocker": "创意简报尚未人工批准",
            "estimatedCostMicros": 0,
        }

    def run_group(self, group_id: uuid.UUID, payload: object) -> dict[str, object]:
        assert group_id == self.group_id
        self.last_payload = payload
        return {"groupId": str(group_id), "status": "awaiting_review"}

    def save_group_template(self, group_id: uuid.UUID) -> dict[str, object]:
        assert group_id == self.group_id
        return {"id": str(uuid.uuid4()), "templateKey": "six-stage-v1"}

    def ungroup(
        self,
        group_id: uuid.UUID,
        *,
        expected_revision: int,
    ) -> dict[str, object]:
        assert group_id == self.group_id
        self.last_expected_revision = expected_revision
        return {"id": str(group_id), "status": "detached", "archived": True}

    def convert_group_to_shots(self, group_id: uuid.UUID) -> dict[str, object]:
        assert group_id == self.group_id
        return {"parentGroupId": str(group_id), "groups": []}

    def group_download_manifest(self, group_id: uuid.UUID) -> dict[str, object]:
        assert group_id == self.group_id
        return {"groupId": str(group_id), "assets": []}

    def build_group_download(self, group_id: uuid.UUID) -> tuple[bytes, str]:
        assert group_id == self.group_id
        return b"zip-content", "one-child-one-cat-assets.zip"


def _client(tmp_path: Path, service: _RecipeService) -> TestClient:
    container = SimpleNamespace(
        repository=object(),
        editing=object(),
        production_recipes=service,
        runtime_settings=SimpleNamespace(work_root=tmp_path, asset_root=tmp_path),
    )
    return TestClient(
        create_app(container, job_registry=JobRegistry(inline=True))  # type: ignore[arg-type]
    )


def test_recipe_catalog_create_get_and_if_match_patch(tmp_path: Path) -> None:
    service = _RecipeService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()

    catalog = client.get("/api/v2/production-recipes")
    created = client.post(
        f"/api/v2/projects/{project_id}/recipe-instances",
        json={
            "recipeKey": "healing_child_cat_v1",
            "theme": "孩子与猫在雨后收集落叶",
            "targetDurationSeconds": 31,
            "qualityTier": "balanced",
        },
    )
    loaded = client.get(f"/api/v2/recipe-instances/{service.instance_id}")
    patched = client.patch(
        f"/api/v2/recipe-instances/{service.instance_id}",
        headers={"If-Match": '"1"'},
        json={"qualityTier": "premium"},
    )

    assert catalog.status_code == 200
    assert catalog.json()[0]["key"] == "healing_child_cat_v1"
    assert created.status_code == 201
    assert created.json()["targetDurationSeconds"] == 31
    assert loaded.status_code == 200
    assert patched.status_code == 200
    assert service.last_expected_revision == 1


def test_review_endpoint_requires_pinned_target_and_override_reason(tmp_path: Path) -> None:
    service = _RecipeService()
    client = _client(tmp_path, service)
    target_id = uuid.uuid4()

    unpinned = client.post(
        "/api/v2/review-decisions",
        json={
            "recipeInstanceId": str(service.instance_id),
            "targetType": "anchor_asset",
            "targetId": str(target_id),
            "decision": "approve",
        },
    )
    invalid_override = client.post(
        "/api/v2/review-decisions",
        json={
            "recipeInstanceId": str(service.instance_id),
            "targetType": "anchor_asset",
            "targetId": str(target_id),
            "targetHash": "a" * 64,
            "decision": "override",
            "blockingDiagnosticPresent": True,
        },
    )
    approved = client.post(
        "/api/v2/review-decisions",
        json={
            "recipeInstanceId": str(service.instance_id),
            "targetType": "anchor_asset",
            "targetId": str(target_id),
            "targetHash": "a" * 64,
            "decision": "override",
            "blockingDiagnosticPresent": True,
            "reason": "人工逐帧确认该提示为误报",
        },
    )

    assert unpinned.status_code == 422
    assert invalid_override.status_code == 422
    assert approved.status_code == 201
    assert approved.json()["decision"] == "override"


def test_recipe_stage_run_endpoints_require_idempotency_and_cost_acceptance(
    tmp_path: Path,
) -> None:
    service = _RecipeService()
    client = _client(tmp_path, service)
    shot_id = uuid.uuid4()
    payload = {
        "idempotencyKey": "stage-run-0001",
        "acceptEstimatedCostMicros": 0,
    }

    story = client.post(
        f"/api/v2/recipe-instances/{service.instance_id}/story-runs",
        json=payload,
    )
    storyboard = client.post(
        f"/api/v2/recipe-instances/{service.instance_id}/storyboard-runs",
        json=payload,
    )
    anchor = client.post(
        f"/api/v2/recipe-instances/{service.instance_id}/shots/{shot_id}/anchor-runs",
        json=payload,
    )
    video = client.post(
        f"/api/v2/recipe-instances/{service.instance_id}/shots/{shot_id}/video-runs",
        json=payload,
    )
    sequence = client.post(
        f"/api/v2/recipe-instances/{service.instance_id}/sequence-runs",
        json=payload,
    )
    invalid = client.post(
        f"/api/v2/recipe-instances/{service.instance_id}/story-runs",
        json={"acceptEstimatedCostMicros": 0},
    )

    assert [item.status_code for item in (story, storyboard, anchor, video, sequence)] == [
        202,
        202,
        202,
        202,
        202,
    ]
    assert invalid.status_code == 422
    assert storyboard.json()["kind"] == "director"
    assert storyboard.json()["projectId"] == str(service.project_id)
    assert storyboard.json()["recipeInstanceId"] == str(service.instance_id)
    assert storyboard.json()["creationMode"] == "from_story"
    assert storyboard.json()["operationKey"] == "recipe:storyboard"
    assert storyboard.json()["phase"] == "storyboard"


def test_canvas_group_actions_compile_run_and_preserve_if_match(tmp_path: Path) -> None:
    service = _RecipeService()
    client = _client(tmp_path, service)

    compiled = client.post(f"/api/v2/canvas-groups/{service.group_id}/compile-run")
    executed = client.post(
        f"/api/v2/canvas-groups/{service.group_id}/runs",
        json={
            "idempotencyKey": "group-run-0001",
            "acceptEstimatedCostMicros": 0,
        },
    )
    detached = client.post(
        f"/api/v2/canvas-groups/{service.group_id}/ungroup",
        headers={"If-Match": '"3"'},
    )

    assert compiled.status_code == 200
    assert compiled.json()["phase"] == "creative"
    assert executed.status_code == 202
    assert executed.json()["kind"] == "director"
    assert executed.json()["canvasGroupId"] == str(service.group_id)
    assert executed.json()["phase"] == "creative"
    assert detached.status_code == 200
    assert detached.json()["archived"] is True
    assert service.last_expected_revision == 3


def test_canvas_group_download_exposes_manifest_and_attachment(tmp_path: Path) -> None:
    service = _RecipeService()
    client = _client(tmp_path, service)

    manifest = client.get(f"/api/v2/canvas-groups/{service.group_id}/download-manifest")
    archive = client.get(f"/api/v2/canvas-groups/{service.group_id}/download")

    assert manifest.status_code == 200
    assert manifest.json() == {"groupId": str(service.group_id), "assets": []}
    assert archive.status_code == 200
    assert archive.content == b"zip-content"
    assert archive.headers["content-type"] == "application/zip"
    assert "one-child-one-cat-assets.zip" in archive.headers["content-disposition"]


def test_sequence_endpoint_accepts_recipe_bounded_transition_plan(tmp_path: Path) -> None:
    service = _RecipeService()
    client = _client(tmp_path, service)
    shot_id = uuid.uuid4()

    response = client.post(
        f"/api/v2/recipe-instances/{service.instance_id}/sequence-runs",
        json={
            "idempotencyKey": "sequence-run-0001",
            "acceptEstimatedCostMicros": 0,
            "transitions": [
                {
                    "afterShotId": str(shot_id),
                    "transition": {"type": "fade_black", "durationMs": 500},
                }
            ],
        },
    )

    assert response.status_code == 202
    assert service.last_payload.transitions[0].after_shot_id == shot_id  # type: ignore[attr-defined]
