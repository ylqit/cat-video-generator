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


class FakeProduction:
    def __init__(self) -> None:
        self.run_day_calls: list[dict] = []

    def run_day(self, run_id, *, slot, allow_paid_generation):
        self.run_day_calls.append(
            {
                "runId": run_id,
                "slot": slot,
                "allowPaidGeneration": allow_paid_generation,
            }
        )
        return {"runId": str(run_id), "episodes": []}

    def resume(self, run_id):
        return [{"stepId": str(uuid.uuid4()), "status": "succeeded"}]


class FakeAssets:
    def __init__(self) -> None:
        self.review_calls: list[dict] = []
        self.import_calls: list[dict] = []

    def review_asset(self, asset_id, *, approve, reason):
        self.review_calls.append(
            {"assetId": asset_id, "approve": approve, "reason": reason}
        )
        return {
            "reviewId": str(uuid.uuid4()),
            "assetId": str(asset_id),
            "decision": "approved" if approve else "rejected",
        }

    def import_canon(self, *, role, path):
        assert path.is_file()
        self.import_calls.append({"role": role, "path": path})
        return {
            "assetId": str(uuid.uuid4()),
            "role": role,
            "localPath": str(path),
            "sha256": "0" * 64,
        }


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
        data={"role": "cat"},
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
        data={"role": "dog"},
        files={"file": ("dog.png", b"x", "image/png")},
    )
    assert bad_role.status_code == 422
    bad_suffix = client.post(
        "/api/v1/canon",
        data={"role": "cat"},
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
