from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cat_video_generator.application.planning import PlanningResult
from cat_video_generator.domain.contracts import DailyProductionPlan
from cat_video_generator.interfaces.api import create_app
from cat_video_generator.interfaces.api_write import create_write_router
from cat_video_generator.interfaces.jobs import JobRegistry


class FakePlanning:
    def __init__(self, plan: DailyProductionPlan) -> None:
        self.plan = plan
        self.calls: list[dict] = []
        self.resume_calls: list[dict] = []
        self.replan_calls: list[dict] = []
        self.block: threading.Event | None = None

    def plan_day(self, **kwargs):
        self.calls.append(kwargs)
        if self.block is not None:
            self.block.wait(timeout=10)
        return PlanningResult(
            run_id=uuid.uuid4(),
            selected_candidate=1,
            candidate_count=1,
            plan=self.plan,
        )

    def resume_planning(self, run_id, *, allow_paid_generation):
        self.resume_calls.append(
            {"runId": run_id, "allowPaidGeneration": allow_paid_generation}
        )
        return {"runId": str(run_id), "status": "planning_review"}

    def replan_episode(self, run_id, *, slot, reason, allow_paid_generation):
        self.replan_calls.append(
            {
                "runId": run_id,
                "slot": slot,
                "reason": reason,
                "allowPaidGeneration": allow_paid_generation,
            }
        )
        return {"runId": str(run_id), "slot": slot.value, "status": "planned"}


class FakeRetry:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def retry_step(
        self,
        step_id,
        *,
        reason,
        allow_paid_generation,
        allow_unverified_keyframes,
    ):
        self.calls.append(
            {
                "stepId": step_id,
                "reason": reason,
                "allowPaidGeneration": allow_paid_generation,
                "allowUnverifiedKeyframes": allow_unverified_keyframes,
            }
        )
        return {"stepId": str(step_id), "attempt": 2, "status": "succeeded"}


class FakeResolutionCompare:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def compare(
        self,
        run_id,
        *,
        resolution,
        allow_paid_generation,
        allow_multi_clip,
    ):
        self.calls.append(
            {
                "runId": run_id,
                "resolution": resolution,
                "allowPaidGeneration": allow_paid_generation,
                "allowMultiClip": allow_multi_clip,
            }
        )
        return {"runId": str(run_id), "resolution": resolution}


class FakeProduction:
    def __init__(self) -> None:
        self.run_day_calls: list[dict] = []
        self.keyframe_calls: list[dict] = []
        self.override_calls: list[dict] = []

    def run_day(
        self,
        run_id,
        *,
        slot,
        allow_paid_generation,
        allow_unverified_keyframes,
        allow_multi_clip,
    ):
        self.run_day_calls.append(
            {
                "runId": run_id,
                "slot": slot,
                "allowPaidGeneration": allow_paid_generation,
                "allowUnverifiedKeyframes": allow_unverified_keyframes,
                "allowMultiClip": allow_multi_clip,
            }
        )
        return {"runId": str(run_id), "episodes": []}

    def resume(self, run_id):
        return [{"stepId": str(uuid.uuid4()), "status": "succeeded"}]

    def prepare_keyframes_only(
        self,
        run_id,
        *,
        slot=None,
        prompt_overrides=None,
        allow_paid_generation,
        allow_unverified_keyframes=False,
    ):
        self.keyframe_calls.append(
            {
                "runId": run_id,
                "slot": slot,
                "promptOverrides": prompt_overrides,
                "allowPaidGeneration": allow_paid_generation,
                "allowUnverifiedKeyframes": allow_unverified_keyframes,
            }
        )
        return {"runId": str(run_id), "episodes": []}

    def save_prompt_overrides(self, episode_id, *, overrides):
        self.override_calls.append(
            {"episodeId": episode_id, "overrides": overrides}
        )


class FakeAssets:
    def __init__(self) -> None:
        self.review_calls: list[dict] = []
        self.import_calls: list[dict] = []
        self.reference_calls: list[dict] = []
        self.crop_calls: list[dict] = []

    def review_asset(self, asset_id, *, approve, reason):
        self.review_calls.append(
            {"assetId": asset_id, "approve": approve, "reason": reason}
        )
        return {
            "reviewId": str(uuid.uuid4()),
            "assetId": str(asset_id),
            "decision": "approved" if approve else "rejected",
        }

    def import_canon(self, *, role, path, semantic_key, view):
        assert path.is_file()
        self.import_calls.append(
            {
                "role": role,
                "path": path,
                "semanticKey": semantic_key,
                "view": view,
            }
        )
        return {
            "assetId": str(uuid.uuid4()),
            "role": role,
            "localPath": str(path),
            "sha256": "0" * 64,
        }

    def import_episode_reference(self, *, episode_id, role, path, semantic_key):
        assert path.is_file()
        self.reference_calls.append(
            {
                "episodeId": episode_id,
                "role": role,
                "semanticKey": semantic_key,
            }
        )
        return {"assetId": str(uuid.uuid4()), "role": role}

    def derive_canon_crop(
        self,
        *,
        source_asset_id,
        role,
        box,
        subject_free,
        semantic_key,
        view,
    ):
        self.crop_calls.append(
            {
                "sourceAssetId": source_asset_id,
                "role": role,
                "box": box,
                "subjectFree": subject_free,
                "semanticKey": semantic_key,
                "view": view,
            }
        )
        return {"assetId": str(uuid.uuid4()), "role": role}


class FakeDelivery:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready

    def deliver(self, run_id):
        if not self.ready:
            raise ValueError("只有ready Run可以构建交付包")
        return {
            "deliveryPackageId": str(uuid.uuid4()),
            "revision": 1,
            "localPath": "output/pkg",
            "manifestSha256": "1" * 64,
        }


class FakeQueries:
    def __init__(self, deliveries: list[dict] | None = None) -> None:
        self.deliveries = deliveries or []
        self.episodes: dict[str, dict] = {}

    def episode(self, episode_id):
        key = str(episode_id)
        if key in self.episodes:
            return self.episodes[key]
        raise LookupError(f"Episode {episode_id} 不存在")

    def list_canon(self):
        return [
            {
                "id": str(uuid.uuid4()),
                "role": "cat",
                "scope": "canon",
                "status": "approved",
                "sha256": "2" * 64,
                "metadata": {},
            }
        ]

    def list_deliveries(self, run_id):
        return self.deliveries

    def delivery_detail(self, package_id):
        for item in self.deliveries:
            if item["id"] == str(package_id):
                return item
        raise LookupError(f"DeliveryPackage {package_id} 不存在")


def _client(
    tmp_path: Path,
    *,
    planning=None,
    production=None,
    assets=None,
    delivery=None,
    queries=None,
    retry=None,
    resolution_compare=None,
    registry=None,
) -> TestClient:
    app = create_app(
        queries or FakeQueries(),
        allowed_media_roots=(tmp_path,),
    )
    app.include_router(
        create_write_router(
            planning=planning,
            production=production or FakeProduction(),
            assets=assets or FakeAssets(),
            delivery=delivery or FakeDelivery(),
            queries=queries or FakeQueries(),
            retry=retry or FakeRetry(),
            resolution_compare=resolution_compare or FakeResolutionCompare(),
            job_registry=registry or JobRegistry(inline=True),
            default_candidate_count=1,
            upload_dir=tmp_path / "uploads",
            delivery_root=tmp_path / "delivery",
        )
    )
    return TestClient(app)


def test_plan_requires_paid_confirmation(
    tmp_path: Path,
    daily_plan: DailyProductionPlan,
) -> None:
    planning = FakePlanning(daily_plan)
    client = _client(tmp_path, planning=planning)
    response = client.post("/api/v1/plans", json={"targetDate": "2026-08-01"})
    assert response.status_code == 422
    assert planning.calls == []


def test_plan_submits_background_job(
    tmp_path: Path,
    daily_plan: DailyProductionPlan,
) -> None:
    planning = FakePlanning(daily_plan)
    client = _client(tmp_path, planning=planning)
    response = client.post(
        "/api/v1/plans",
        json={"targetDate": "2026-08-01", "allowPaidGeneration": True},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["kind"] == "plan_day"
    assert body["dedupKey"] == "plan:2026-08-01"
    job = client.get(f"/api/v1/jobs/{body['jobId']}").json()
    assert job["status"] == "succeeded"
    assert job["result"]["candidateCount"] == 1
    assert job["result"]["plan"]["theme"] == daily_plan.theme
    assert planning.calls[0]["allow_paid_generation"] is True
    assert planning.calls[0]["candidate_count"] == 1


def test_plan_conflict_returns_409(
    tmp_path: Path,
    daily_plan: DailyProductionPlan,
) -> None:
    planning = FakePlanning(daily_plan)
    planning.block = threading.Event()
    with ThreadPoolExecutor(max_workers=2) as executor:
        client = _client(
            tmp_path,
            planning=planning,
            registry=JobRegistry(executor=executor),
        )
        first = client.post(
            "/api/v1/plans",
            json={"targetDate": "2026-08-01", "allowPaidGeneration": True},
        )
        assert first.status_code == 202
        second = client.post(
            "/api/v1/plans",
            json={"targetDate": "2026-08-01", "allowPaidGeneration": True},
        )
        assert second.status_code == 409
        assert second.json()["detail"]["jobId"] == first.json()["jobId"]
        planning.block.set()


def test_generate_requires_paid_confirmation(tmp_path: Path) -> None:
    production = FakeProduction()
    client = _client(tmp_path, production=production)
    response = client.post(f"/api/v1/runs/{uuid.uuid4()}/generate", json={})
    assert response.status_code == 422
    assert production.run_day_calls == []


def test_generate_with_slot_submits_job(tmp_path: Path) -> None:
    production = FakeProduction()
    client = _client(tmp_path, production=production)
    run_id = uuid.uuid4()
    response = client.post(
        f"/api/v1/runs/{run_id}/generate",
        json={"slot": "morning", "allowPaidGeneration": True},
    )
    assert response.status_code == 202
    assert response.json()["dedupKey"] == f"run:{run_id}:morning"
    call = production.run_day_calls[0]
    assert call["slot"].value == "morning"
    assert call["allowPaidGeneration"] is True


def test_resume_submits_job(tmp_path: Path) -> None:
    client = _client(tmp_path)
    run_id = uuid.uuid4()
    response = client.post(f"/api/v1/runs/{run_id}/resume")
    assert response.status_code == 202
    assert response.json()["dedupKey"] == f"resume:{run_id}"


def test_review_passes_decision_through(tmp_path: Path) -> None:
    assets = FakeAssets()
    client = _client(tmp_path, assets=assets)
    asset_id = uuid.uuid4()
    response = client.post(
        f"/api/v1/assets/{asset_id}/review",
        json={"approve": True, "reason": "动作自然"},
    )
    assert response.status_code == 200
    assert response.json()["decision"] == "approved"
    assert assets.review_calls[0]["reason"] == "动作自然"


def test_review_requires_reason(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post(
        f"/api/v1/assets/{uuid.uuid4()}/review",
        json={"approve": True, "reason": ""},
    )
    assert response.status_code == 422


def test_canon_upload_saves_and_imports(tmp_path: Path) -> None:
    assets = FakeAssets()
    client = _client(tmp_path, assets=assets)
    response = client.post(
        "/api/v1/canon",
        data={"role": "cat", "semantic_key": "cat:front", "view": "front"},
        files={"file": ("cat.png", b"png-bytes", "image/png")},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "cat"
    assert assets.import_calls[0]["role"] == "cat"
    assert not assets.import_calls[0]["path"].exists()
    assert list((tmp_path / "uploads").iterdir()) == []


def test_canon_rejects_bad_role_and_suffix(tmp_path: Path) -> None:
    client = _client(tmp_path)
    bad_role = client.post(
        "/api/v1/canon",
        data={"role": "dog", "semantic_key": "dog:front", "view": "front"},
        files={"file": ("dog.png", b"x", "image/png")},
    )
    assert bad_role.status_code == 422
    bad_suffix = client.post(
        "/api/v1/canon",
        data={"role": "cat", "semantic_key": "cat:front", "view": "front"},
        files={"file": ("cat.txt", b"x", "text/plain")},
    )
    assert bad_suffix.status_code == 422


def test_canon_list_adds_content_url(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/api/v1/canon")
    assert response.status_code == 200
    item = response.json()[0]
    assert item["contentUrl"] == f"/api/v1/assets/{item['id']}/content"


def test_deliver_maps_state_error_to_422(tmp_path: Path) -> None:
    client = _client(tmp_path, delivery=FakeDelivery(ready=False))
    response = client.post(f"/api/v1/runs/{uuid.uuid4()}/deliver")
    assert response.status_code == 422


def test_deliver_success(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post(f"/api/v1/runs/{uuid.uuid4()}/deliver")
    assert response.status_code == 200
    assert response.json()["revision"] == 1


def test_delivery_manifest_reads_file_inside_root(tmp_path: Path) -> None:
    package_dir = tmp_path / "delivery" / "pkg1"
    package_dir.mkdir(parents=True)
    manifest = {"runId": str(uuid.uuid4()), "items": []}
    (package_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    package_id = uuid.uuid4()
    queries = FakeQueries(
        deliveries=[
            {
                "id": str(package_id),
                "localPath": str(package_dir),
                "items": [],
            }
        ]
    )
    client = _client(tmp_path, queries=queries)
    response = client.get(f"/api/v1/deliveries/{package_id}/manifest")
    assert response.status_code == 200
    assert response.json() == manifest


def test_delivery_manifest_rejects_path_outside_root(tmp_path: Path) -> None:
    package_id = uuid.uuid4()
    queries = FakeQueries(
        deliveries=[{"id": str(package_id), "localPath": str(tmp_path), "items": []}]
    )
    client = _client(tmp_path, queries=queries)
    response = client.get(f"/api/v1/deliveries/{package_id}/manifest")
    assert response.status_code == 403


def test_unknown_job_returns_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/v1/jobs/missing").status_code == 404


def test_unknown_delivery_returns_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get(f"/api/v1/deliveries/{uuid.uuid4()}/manifest")
    assert response.status_code == 404


@pytest.mark.parametrize("limit", [1, 5])
def test_jobs_listing(tmp_path: Path, limit: int) -> None:
    client = _client(tmp_path)
    for _ in range(2):
        client.post(f"/api/v1/runs/{uuid.uuid4()}/resume")
    response = client.get(f"/api/v1/jobs?limit={limit}")
    assert response.status_code == 200
    assert len(response.json()) == min(limit, 2)


def test_retry_step_requires_paid_and_reason(tmp_path: Path) -> None:
    client = _client(tmp_path)
    step_id = uuid.uuid4()
    no_paid = client.post(
        f"/api/v1/steps/{step_id}/retry",
        json={"reason": "供应商超时"},
    )
    assert no_paid.status_code == 422
    short_reason = client.post(
        f"/api/v1/steps/{step_id}/retry",
        json={"reason": "短", "allowPaidGeneration": True},
    )
    assert short_reason.status_code == 422


def test_retry_step_submits_job(tmp_path: Path) -> None:
    retry = FakeRetry()
    client = _client(tmp_path, retry=retry)
    step_id = uuid.uuid4()
    response = client.post(
        f"/api/v1/steps/{step_id}/retry",
        json={
            "reason": "供应商超时重试",
            "allowPaidGeneration": True,
            "allowUnverifiedKeyframes": True,
        },
    )
    assert response.status_code == 202
    assert response.json()["kind"] == "retry_step"
    assert response.json()["dedupKey"] == f"retry:{step_id}"
    job = client.get(f"/api/v1/jobs/{response.json()['jobId']}").json()
    assert job["status"] == "succeeded"
    assert job["result"]["attempt"] == 2
    assert retry.calls[0]["allowPaidGeneration"] is True
    assert retry.calls[0]["allowUnverifiedKeyframes"] is True


def test_resume_planning_submits_job(
    tmp_path: Path,
    daily_plan: DailyProductionPlan,
) -> None:
    planning = FakePlanning(daily_plan)
    client = _client(tmp_path, planning=planning)
    run_id = uuid.uuid4()
    denied = client.post(f"/api/v1/runs/{run_id}/resume-planning", json={})
    assert denied.status_code == 422
    response = client.post(
        f"/api/v1/runs/{run_id}/resume-planning",
        json={"allowPaidGeneration": True},
    )
    assert response.status_code == 202
    assert response.json()["kind"] == "resume_planning"
    assert planning.resume_calls[0]["runId"] == run_id


def test_replan_episode_validates_slot_and_reason(
    tmp_path: Path,
    daily_plan: DailyProductionPlan,
) -> None:
    planning = FakePlanning(daily_plan)
    client = _client(tmp_path, planning=planning)
    run_id = uuid.uuid4()
    bad_slot = client.post(
        f"/api/v1/runs/{run_id}/episodes/midnight/replan",
        json={"reason": "剧情不合理", "allowPaidGeneration": True},
    )
    assert bad_slot.status_code == 422
    response = client.post(
        f"/api/v1/runs/{run_id}/episodes/noon/replan",
        json={"reason": "中午剧情与道具矛盾", "allowPaidGeneration": True},
    )
    assert response.status_code == 202
    assert response.json()["dedupKey"] == f"replan:{run_id}:noon"
    assert planning.replan_calls[0]["slot"].value == "noon"
    assert planning.replan_calls[0]["reason"] == "中午剧情与道具矛盾"


def test_episode_reference_upload(tmp_path: Path) -> None:
    assets = FakeAssets()
    client = _client(tmp_path, assets=assets)
    episode_id = uuid.uuid4()
    bad_role = client.post(
        f"/api/v1/episodes/{episode_id}/references",
        data={"role": "person", "semantic_key": "person:front"},
        files={"file": ("a.png", b"x", "image/png")},
    )
    assert bad_role.status_code == 422
    bad_suffix = client.post(
        f"/api/v1/episodes/{episode_id}/references",
        data={"role": "motion", "semantic_key": "motion:jump"},
        files={"file": ("a.png", b"x", "image/png")},
    )
    assert bad_suffix.status_code == 422
    response = client.post(
        f"/api/v1/episodes/{episode_id}/references",
        data={"role": "element", "semantic_key": "element:paper_crane"},
        files={"file": ("crane.png", b"png-bytes", "image/png")},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "element"
    assert assets.reference_calls[0]["episodeId"] == episode_id
    assert assets.reference_calls[0]["semanticKey"] == "element:paper_crane"
    assert list((tmp_path / "uploads").iterdir()) == []


def test_derive_crop_passes_payload(tmp_path: Path) -> None:
    assets = FakeAssets()
    client = _client(tmp_path, assets=assets)
    asset_id = uuid.uuid4()
    response = client.post(
        f"/api/v1/canon/{asset_id}/derive-crop",
        json={
            "role": "person",
            "semanticKey": "person:side",
            "box": [10, 20, 300, 800],
            "subjectFree": False,
            "view": "side",
        },
    )
    assert response.status_code == 201
    call = assets.crop_calls[0]
    assert call["sourceAssetId"] == asset_id
    assert call["semanticKey"] == "person:side"
    assert call["box"] == (10, 20, 300, 800)
    assert call["view"] == "side"


def test_compare_resolution_submits_job(tmp_path: Path) -> None:
    compare = FakeResolutionCompare()
    client = _client(tmp_path, resolution_compare=compare)
    run_id = uuid.uuid4()
    denied = client.post(
        f"/api/v1/runs/{run_id}/compare-resolution",
        json={"resolution": "720p"},
    )
    assert denied.status_code == 422
    bad_resolution = client.post(
        f"/api/v1/runs/{run_id}/compare-resolution",
        json={"resolution": "1080p", "allowPaidGeneration": True},
    )
    assert bad_resolution.status_code == 422
    response = client.post(
        f"/api/v1/runs/{run_id}/compare-resolution",
        json={
            "resolution": "480p",
            "allowPaidGeneration": True,
            "allowMultiClip": True,
        },
    )
    assert response.status_code == 202
    assert response.json()["kind"] == "compare_resolution"
    assert compare.calls[0]["resolution"] == "480p"
    assert compare.calls[0]["allowMultiClip"] is True


def test_plan_chains_keyframes_by_default(
    tmp_path: Path,
    daily_plan: DailyProductionPlan,
) -> None:
    planning = FakePlanning(daily_plan)
    production = FakeProduction()
    client = _client(tmp_path, planning=planning, production=production)
    response = client.post(
        "/api/v1/plans",
        json={"targetDate": "2026-08-01", "allowPaidGeneration": True},
    )
    assert response.status_code == 202
    job = client.get(f"/api/v1/jobs/{response.json()['jobId']}").json()
    assert job["status"] == "succeeded"
    assert len(production.keyframe_calls) == 1
    assert production.keyframe_calls[0]["allowPaidGeneration"] is True


def test_plan_skips_keyframes_when_switch_off(
    tmp_path: Path,
    daily_plan: DailyProductionPlan,
) -> None:
    planning = FakePlanning(daily_plan)
    production = FakeProduction()
    client = _client(tmp_path, planning=planning, production=production)
    response = client.post(
        "/api/v1/plans",
        json={
            "targetDate": "2026-08-01",
            "allowPaidGeneration": True,
            "autoGenerateKeyframes": False,
        },
    )
    assert response.status_code == 202
    job = client.get(f"/api/v1/jobs/{response.json()['jobId']}").json()
    assert job["status"] == "succeeded"
    assert production.keyframe_calls == []
    assert "keyframes" not in job["result"]


def test_save_prompt_overrides_roundtrip(tmp_path: Path) -> None:
    production = FakeProduction()
    client = _client(tmp_path, production=production)
    episode_id = uuid.uuid4()
    response = client.put(
        f"/api/v1/episodes/{episode_id}/prompt-overrides",
        json={"overrides": {"first_frame": "编辑后的首帧Prompt"}},
    )
    assert response.status_code == 200
    assert production.override_calls[0]["episodeId"] == episode_id
    assert production.override_calls[0]["overrides"] == {
        "first_frame": "编辑后的首帧Prompt"
    }


def test_generate_keyframes_requires_paid_and_episode(tmp_path: Path) -> None:
    client = _client(tmp_path)
    episode_id = uuid.uuid4()
    denied = client.post(f"/api/v1/episodes/{episode_id}/keyframes", json={})
    assert denied.status_code == 422
    missing = client.post(
        f"/api/v1/episodes/{episode_id}/keyframes",
        json={"allowPaidGeneration": True},
    )
    assert missing.status_code == 404


def test_generate_keyframes_submits_job(tmp_path: Path) -> None:
    production = FakeProduction()
    queries = FakeQueries()
    episode_id = uuid.uuid4()
    run_id = uuid.uuid4()
    queries.episodes[str(episode_id)] = {
        "id": str(episode_id),
        "runId": str(run_id),
        "slot": "noon",
    }
    client = _client(tmp_path, production=production, queries=queries)
    response = client.post(
        f"/api/v1/episodes/{episode_id}/keyframes",
        json={
            "allowPaidGeneration": True,
            "overrides": {"first_frame": "编辑后的首帧Prompt"},
        },
    )
    assert response.status_code == 202
    assert response.json()["kind"] == "prepare_keyframes"
    assert response.json()["dedupKey"] == f"keyframes:{episode_id}"
    job = client.get(f"/api/v1/jobs/{response.json()['jobId']}").json()
    assert job["status"] == "succeeded"
    call = production.keyframe_calls[0]
    assert call["runId"] == run_id
    assert call["slot"].value == "noon"
    assert call["promptOverrides"] == {
        "noon": {"first_frame": "编辑后的首帧Prompt"}
    }
