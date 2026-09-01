from __future__ import annotations

import uuid
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cat_video_generator.interfaces.creator_api import install_creator_routes


class _CreatorService:
    def __init__(self) -> None:
        self.project_id = uuid.uuid4()
        self.shot_id = uuid.uuid4()
        self.snapshot_id = uuid.uuid4()
        self.saved_state_version: int | None = None
        self.submitted = False

    def create_project(self, payload: object) -> dict[str, object]:
        return {
            "projectId": str(self.project_id),
            "title": payload.title,  # type: ignore[attr-defined]
            "version": 1,
            "providerCallCount": 0,
        }

    def canon_options(self) -> dict[str, object]:
        return {
            "ready": True,
            "references": [
                {
                    "assetId": str(uuid.uuid4()),
                    "role": "child_identity",
                    "providerEligible": True,
                    "title": "固定儿童",
                    "instruction": "固定身份",
                }
            ],
        }

    def asset_library(self, project_id: uuid.UUID) -> list[dict[str, object]]:
        assert project_id == self.project_id
        return [
            {
                "id": str(uuid.uuid4()),
                "projectId": None,
                "mediaType": "image",
                "role": "identity_reference",
                "status": "approved",
                "semanticKey": "person:headshot",
                "sha256": "c" * 64,
                "metadata": {"providerEligible": True},
                "contentUrl": "/api/v2/assets/example/content",
            }
        ]

    def get_state(self, project_id: uuid.UUID) -> dict[str, object]:
        assert project_id == self.project_id
        return {
            "projectId": str(project_id),
            "version": 1,
            "briefBody": "窗边纸星星",
            "storyCandidates": [],
            "currentStory": {},
            "targetDurationSeconds": 8,
            "aspectRatio": "9:16",
            "qualityTier": "quick",
            "referenceBindings": [],
        }

    def update_state(
        self, project_id: uuid.UUID, *, expected_version: int, payload: object
    ) -> dict[str, object]:
        assert project_id == self.project_id
        self.saved_state_version = expected_version
        return {**self.get_state(project_id), "version": expected_version + 1}

    def create_story_candidates(self, project_id: uuid.UUID, payload: object) -> dict[str, object]:
        assert project_id == self.project_id
        return {
            "snapshotId": str(self.snapshot_id),
            "kind": "story_text",
            "promptText": "请生成故事候选",
            "orderedReferences": [],
            "providerConfig": {"provider": "ark", "model": "director"},
            "inputHash": "a" * 64,
            "estimatedCostMicros": 100,
            "confirmedAt": None,
        }

    def save_story(
        self, project_id: uuid.UUID, *, expected_version: int, payload: object
    ) -> dict[str, object]:
        return self.update_state(project_id, expected_version=expected_version, payload=payload)

    def list_shots(self, project_id: uuid.UUID) -> list[dict[str, object]]:
        assert project_id == self.project_id
        return []

    def replace_shots(
        self, project_id: uuid.UUID, *, expected_version: int, payload: object
    ) -> dict[str, object]:
        assert project_id == self.project_id
        return {
            "projectVersion": expected_version + 1,
            "shots": [
                {
                    "id": str(self.shot_id),
                    "sortOrder": 1,
                    "version": 1,
                    "title": "镜头一",
                    "direction": "孩子与猫咪完成纸星星动作。",
                    "durationSeconds": 8,
                    "referenceBindings": [],
                }
            ],
        }

    def update_shot(
        self, shot_id: uuid.UUID, *, expected_version: int, payload: object
    ) -> dict[str, object]:
        assert shot_id == self.shot_id
        return {"id": str(shot_id), "version": expected_version + 1}

    def create_snapshot(self, shot_id: uuid.UUID, payload: object) -> dict[str, object]:
        assert shot_id == self.shot_id
        return {
            "id": str(self.snapshot_id),
            "projectId": str(self.project_id),
            "creatorShotId": str(shot_id),
            "kind": "video",
            "promptText": payload.prompt_text,  # type: ignore[attr-defined]
            "orderedReferences": [],
            "providerConfig": payload.provider_config,  # type: ignore[attr-defined]
            "inputHash": "b" * 64,
            "estimatedCostMicros": 250,
            "confirmedAt": None,
        }

    def submit_snapshot(
        self,
        snapshot_id: uuid.UUID,
        *,
        idempotency_key: str,
        input_hash: str,
        accepted_estimated_cost_micros: int,
    ) -> dict[str, object]:
        assert snapshot_id == self.snapshot_id
        assert idempotency_key == "creator-submit-0001"
        assert input_hash == "b" * 64
        assert accepted_estimated_cost_micros == 250
        self.submitted = True
        return {"taskId": str(uuid.uuid4()), "status": "local_queued"}

    def decide_asset(self, asset_id: uuid.UUID, payload: object) -> dict[str, object]:
        return {"assetId": str(asset_id), "decision": payload.decision}  # type: ignore[attr-defined]

    def select_video(
        self, shot_id: uuid.UUID, *, expected_version: int, payload: object
    ) -> dict[str, object]:
        return {
            "id": str(shot_id),
            "version": expected_version + 1,
            "selectedVideoAssetId": str(payload.asset_id),  # type: ignore[attr-defined]
        }

    def diagnostics(self, project_id: uuid.UUID) -> dict[str, object]:
        return {"projectId": str(project_id), "items": []}


def _client(service: _CreatorService) -> TestClient:
    class Files:
        def asset_path(self, _asset_id: uuid.UUID):
            raise AssertionError("media route was not requested")

    class Cancellation:
        def cancellation_for(self, _task_id: uuid.UUID):
            raise AssertionError("cancellation route was not requested")

        def cancel(self, _task_id: uuid.UUID, **_values: object):
            raise AssertionError("cancellation route was not requested")

    app = FastAPI()
    install_creator_routes(app, service, Files(), Cancellation())
    return TestClient(app)


def test_creator_project_and_content_endpoints_use_explicit_versions() -> None:
    service = _CreatorService()
    client = _client(service)
    child_asset = uuid.uuid4()
    cat_asset = uuid.uuid4()
    style_asset = uuid.uuid4()

    created = client.post(
        "/api/v2/creator-projects",
        json={
            "title": "纸星星",
            "contentDate": date.today().isoformat(),
            "brief": {
                "body": "清晨窗边的一人一猫故事",
                "durationSeconds": 8,
                "aspectRatio": "9:16",
                "qualityTier": "quick",
            },
            "references": [
                {
                    "assetId": str(child_asset),
                    "role": "child_identity",
                    "providerEligible": True,
                    "title": "儿童",
                },
                {
                    "assetId": str(cat_asset),
                    "role": "cat_identity",
                    "providerEligible": True,
                    "title": "猫咪",
                },
                {
                    "assetId": str(style_asset),
                    "role": "style_board",
                    "providerEligible": True,
                    "title": "画风板",
                },
            ],
        },
    )
    assert created.status_code == 201
    assert created.json()["providerCallCount"] == 0

    state = client.get(f"/api/v2/projects/{service.project_id}/creator-state")
    assert state.status_code == 200
    assert state.json()["briefBody"] == "窗边纸星星"

    updated = client.patch(
        f"/api/v2/projects/{service.project_id}/creator-state",
        headers={"If-Match": '"1"'},
        json={"briefBody": "新的创作要求"},
    )
    assert updated.status_code == 200
    assert service.saved_state_version == 1


def test_snapshot_preview_creates_no_task_and_submit_is_a_separate_boundary() -> None:
    service = _CreatorService()
    client = _client(service)

    preview = client.post(
        f"/api/v2/creator-shots/{service.shot_id}/generation-snapshots",
        json={
            "kind": "video",
            "promptText": "生成专业的一人一猫短片",
            "orderedReferences": [],
            "providerConfig": {"provider": "ark", "model": "video", "durationSeconds": 8},
        },
    )
    assert preview.status_code == 201
    assert preview.json()["confirmedAt"] is None
    assert service.submitted is False

    submitted = client.post(
        f"/api/v2/generation-snapshots/{service.snapshot_id}/submit",
        headers={"Idempotency-Key": "creator-submit-0001"},
        json={"inputHash": "b" * 64, "acceptedEstimatedCostMicros": 250},
    )
    assert submitted.status_code == 202
    assert submitted.json()["status"] == "local_queued"
    assert service.submitted is True


def test_creator_canon_and_asset_library_are_read_only_creator_boundaries() -> None:
    service = _CreatorService()
    client = _client(service)

    canon = client.get("/api/v2/creator-canon")
    assets = client.get(f"/api/v2/projects/{service.project_id}/creator-assets")

    assert canon.status_code == 200
    assert canon.json()["ready"] is True
    assert canon.json()["references"][0]["role"] == "child_identity"
    assert assets.status_code == 200
    assert assets.json()[0]["semanticKey"] == "person:headshot"
