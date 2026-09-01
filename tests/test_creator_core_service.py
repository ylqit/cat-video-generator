from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from cat_video_generator.application.creator_core import CreatorCoreService
from cat_video_generator.domain.creator_core import CreatorReference, GenerationSnapshotDraft


def _ref(role: str) -> CreatorReference:
    return CreatorReference(assetId=uuid.uuid4(), role=role, providerEligible=True, title=role)


class _Repository:
    def __init__(self) -> None:
        self.created = False
        self.snapshots: list[dict[str, object]] = []
        self.submit_calls = 0
        self.project_id = uuid.uuid4()
        self.shot_id = uuid.uuid4()

    def create_project(self, payload: object) -> dict[str, object]:
        self.created = True
        return {"projectId": str(self.project_id), "providerCallCount": 0}

    def create_snapshot(
        self,
        *,
        project_id: uuid.UUID,
        creator_shot_id: uuid.UUID | None,
        kind: str,
        prompt_text: str,
        ordered_references: list[dict[str, object]],
        provider_config: dict[str, object],
        input_hash: str,
        estimated_cost_micros: int | None,
    ) -> dict[str, object]:
        snapshot = {
            "id": str(uuid.uuid4()),
            "projectId": str(project_id),
            "creatorShotId": str(creator_shot_id) if creator_shot_id else None,
            "kind": kind,
            "promptText": prompt_text,
            "orderedReferences": ordered_references,
            "providerConfig": provider_config,
            "inputHash": input_hash,
            "estimatedCostMicros": estimated_cost_micros,
            "confirmedAt": None,
        }
        self.snapshots.append(snapshot)
        return snapshot

    def project_id_for_shot(self, shot_id: uuid.UUID) -> uuid.UUID:
        assert shot_id == self.shot_id
        return self.project_id

    def submit_snapshot(
        self,
        snapshot_id: uuid.UUID,
        *,
        idempotency_key: str,
        input_hash: str,
        accepted_estimated_cost_micros: int,
    ) -> dict[str, object]:
        self.submit_calls += 1
        return {"taskId": str(uuid.uuid4()), "status": "local_queued"}

    def __getattr__(self, name: str):
        def passthrough(*args, **kwargs):
            return {"name": name, "args": args, "kwargs": kwargs}

        return passthrough


def test_create_project_rejects_ambiguous_canon_before_persistence() -> None:
    repository = _Repository()
    service = CreatorCoreService(repository)
    payload = SimpleNamespace(
        references=[
            _ref("child_identity"),
            _ref("child_identity"),
            _ref("cat_identity"),
            _ref("style_board"),
        ]
    )

    with pytest.raises(ValueError, match="唯一"):
        service.create_project(payload)
    assert repository.created is False


def test_snapshot_preview_hashes_the_exact_visible_provider_input() -> None:
    repository = _Repository()
    service = CreatorCoreService(
        repository,
        provider_configs={
            "video": {
                "provider": "volcengine-ark-standard",
                "model": "server-video-model",
                "resolution": "720p",
            }
        },
        estimated_costs_micros={"video": 4800},
    )
    references = [_ref("child_identity"), _ref("cat_identity"), _ref("style_board")]
    payload = GenerationSnapshotDraft(
        kind="video",
        promptText="生成八秒竖屏一人一猫短片",
        orderedReferences=references,
        providerConfig={"provider": "ark", "model": "video", "durationSeconds": 8},
    )

    preview = service.create_snapshot(repository.shot_id, payload)

    assert preview["confirmedAt"] is None
    assert preview["inputHash"] == repository.snapshots[0]["inputHash"]
    assert len(str(preview["inputHash"])) == 64
    assert preview["providerConfig"]["model"] == "server-video-model"
    assert preview["estimatedCostMicros"] == 4800
    assert repository.submit_calls == 0


def test_unmetered_cost_is_owned_by_server_configuration() -> None:
    repository = _Repository()
    service = CreatorCoreService(repository)
    payload = GenerationSnapshotDraft(
        kind="video",
        promptText="生成八秒竖屏一人一猫短片",
        orderedReferences=[
            _ref("child_identity"),
            _ref("cat_identity"),
            _ref("style_board"),
        ],
        providerConfig={"provider": "ark", "model": "video"},
    )

    preview = service.create_snapshot(repository.shot_id, payload)

    assert preview["estimatedCostMicros"] is None


def test_story_candidate_request_creates_a_preview_not_a_task() -> None:
    repository = _Repository()
    service = CreatorCoreService(repository)
    payload = SimpleNamespace(
        brief_body="纸星星故事",
        requested_count=3,
    )

    preview = service.create_story_candidates(repository.project_id, payload)

    assert preview["kind"] == "story_text"
    assert "1至5个" in str(preview["promptText"])
    assert repository.submit_calls == 0
