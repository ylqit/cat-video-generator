"""Ark映射顺序和精简HTTP契约。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import episode_for
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner
from volcenginesdkarkruntime.types.images import SequentialImageGenerationOptions

from cat_video_generator.config import RuntimeSettings
from cat_video_generator.domain.contracts import Slot
from cat_video_generator.domain.rendering import MediaSource, VideoInputMode, build_video_input_plan
from cat_video_generator.infrastructure.ark.gateway import ArkGateway
from cat_video_generator.infrastructure.db.models import (
    Asset,
    Episode,
    ProductionRun,
    Review,
    WorkflowStep,
)
from cat_video_generator.infrastructure.db.query_repository import _current_stage, _workflow_nodes
from cat_video_generator.infrastructure.db.records import run_dict, step_dict
from cat_video_generator.interfaces.api import _SPAStaticFiles, create_app
from cat_video_generator.interfaces.api_schemas import GenerateRequest, RetryStepRequest
from cat_video_generator.interfaces.cli import app as cli_app


class Tasks:
    def __init__(self) -> None:
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(id="task-1")


def runtime(tmp_path: Path) -> RuntimeSettings:
    return RuntimeSettings.from_env(
        {
            "ARK_API_KEY": "test-key",
            "ARK_BASE_URL": "https://ark.cn-beijing.volces.com/api/v3",
            "ARK_IMAGE_MODEL": "doubao-seedream-5-0-260128",
            "ARK_VIDEO_MODEL": "doubao-seedance-2-0-mini-260615",
            "ARK_PLANNING_MODEL": "doubao-seed-2-1-pro-260628",
            "ARK_REVIEW_MODEL": "doubao-seed-2-1-pro-260628",
            "PATH": "",
        },
        config_root=tmp_path,
    )


def test_gateway_preserves_binding_order(tmp_path) -> None:
    paths = tuple(tmp_path / f"panel-{index}.png" for index in range(1, 4))
    for index, path in enumerate(paths, 1):
        path.write_bytes(f"panel-{index}".encode())
    sources = tuple(
        MediaSource(
            asset_id=uuid.uuid4(),
            semantic_key=f"storyboard:panel-{index:02d}",
            media_type="image",
            sha256=str(index) * 64,
            metadata={"width": 720, "height": 1280},
        )
        for index in range(1, 4)
    )
    plan = build_video_input_plan(
        input_mode=VideoInputMode.STORYBOARD_REFERENCE,
        resolution="720p",
        duration_seconds=9,
        sources=sources,
    )
    tasks = Tasks()
    client = SimpleNamespace(content_generation=SimpleNamespace(tasks=tasks))
    result = ArkGateway(runtime(tmp_path), client=client).submit_video(
        prompt="测试Prompt",
        input_plan=plan,
        input_paths=paths,
    )
    assert result.task_id == "task-1"
    assert tasks.kwargs["model"] == "doubao-seedance-2-0-mini-260615"
    assert tasks.kwargs["generate_audio"] is True
    assert [item["role"] for item in tasks.kwargs["content"][1:]] == [
        "reference_image",
        "reference_image",
        "reference_image",
    ]


def test_gateway_requests_seedream_group_images(tmp_path) -> None:
    class Images:
        def __init__(self) -> None:
            self.kwargs = None
            self.serialized_options = None

        def generate(self, **kwargs):
            self.kwargs = kwargs
            # 与真实Ark SDK保持同一序列化契约；传入dict时本测试会直接复现
            # 生产环境中的“没有model_dump”故障。
            self.serialized_options = kwargs[
                "sequential_image_generation_options"
            ].model_dump(mode="json")
            return SimpleNamespace(
                model="seedream",
                data=[SimpleNamespace(url=f"https://example/{index}.png") for index in range(3)],
            )

    reference = tmp_path / "person.png"
    reference.write_bytes(b"person")
    images = Images()
    client = SimpleNamespace(images=images)
    results = ArkGateway(runtime(tmp_path), client=client).generate_storyboard(
        prompt="三张独立故事板",
        reference_paths=(reference,),
        max_images=3,
    )
    assert len(results) == 3
    assert images.kwargs["sequential_image_generation"] == "auto"
    options = images.kwargs["sequential_image_generation_options"]
    assert isinstance(options, SequentialImageGenerationOptions)
    assert images.serialized_options == {"max_images": 3}


def test_gateway_lists_video_tasks_with_configured_timeout(tmp_path) -> None:
    class ListTasks:
        def __init__(self) -> None:
            self.kwargs = None

        def list(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                items=[
                    SimpleNamespace(
                        id="task-list-1",
                        status="running",
                        model="doubao-seedance-2-0-mini-260615",
                        created_at=1_786_000_000,
                        duration=9,
                        ratio="9:16",
                        resolution="720p",
                        generate_audio=True,
                        content=None,
                        error=None,
                    )
                ]
            )

    tasks = ListTasks()
    client = SimpleNamespace(content_generation=SimpleNamespace(tasks=tasks))
    results = ArkGateway(runtime(tmp_path), client=client).list_video_tasks(
        model="doubao-seedance-2-0-mini-260615"
    )

    assert [item.task_id for item in results] == ["task-list-1"]
    assert results[0].duration_seconds == 9
    assert tasks.kwargs == {
        "page_num": 1,
        "page_size": 100,
        "model": "doubao-seedance-2-0-mini-260615",
        "timeout": 120.0,
    }


def test_removed_http_flags_are_not_accepted() -> None:
    assert set(GenerateRequest.model_fields) == {"slot", "allow_paid_generation"}
    assert set(RetryStepRequest.model_fields) == {
        "reason",
        "allow_paid_generation",
        "acknowledge_duplicate_billing",
    }


def test_api_command_exposes_host_option() -> None:
    result = CliRunner().invoke(cli_app, ["api", "--help"])

    assert result.exit_code == 0
    assert "--host" in result.stdout
    assert "127.0.0.1" in result.stdout


def test_api_command_rejects_missing_static_bundle(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli_app,
        ["api", "--static-dir", str(tmp_path / "missing")],
    )

    assert result.exit_code != 0
    assert "index.html" in result.output


def test_spa_static_files_falls_back_only_for_browser_routes(tmp_path: Path) -> None:
    static_dir = tmp_path / "web-dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text('<div id="app"></div>', encoding="utf-8")
    app = FastAPI()
    app.mount("/", _SPAStaticFiles(directory=static_dir, html=True), name="web")
    client = TestClient(app)

    assert client.get("/runs/example").status_code == 200
    assert client.get("/missing.js").status_code == 404
    assert client.get("/api/v1/missing").status_code == 404


def test_health_exposes_timeouts_without_secrets(tmp_path: Path) -> None:
    query = SimpleNamespace(
        health=lambda: {
            "database": "test",
            "user": "postgres",
            "alembicRevision": "0009",
            "expectedAlembicRevision": "0009",
            "ready": True,
        }
    )
    app = create_app(
        query,
        allowed_media_roots=(tmp_path,),
        runtime_report={
            "arkImageRequestTimeoutSeconds": 600,
            "arkTaskTimeoutSeconds": 1800,
        },
    )
    payload = TestClient(app).get("/api/v1/health").json()

    assert payload["arkImageRequestTimeoutSeconds"] == 600
    assert payload["arkTaskTimeoutSeconds"] == 1800
    assert "arkApiKey" not in payload
    assert "databasePassword" not in payload


def test_workflow_node_separates_provider_contract_and_semantic_status() -> None:
    now = datetime.now(UTC)
    run_id = uuid.uuid4()
    step_id = uuid.uuid4()
    step = WorkflowStep(
        id=step_id,
        production_run_id=run_id,
        episode_id=None,
        parent_step_id=None,
        kind="director",
        status="succeeded",
        attempt=1,
        operation_key="director:episode:morning",
        idempotency_key="a" * 64,
        provider="test",
        provider_task_id=None,
        model="planning-model",
        input_hash="b" * 64,
        input_snapshot_json={},
        error_json=None,
        created_at=now,
        updated_at=now,
    )
    review = Review(
        id=uuid.uuid4(),
        step_id=step_id,
        asset_id=None,
        source="technical",
        decision="rejected",
        reason="关键实体生命周期不合法",
        warnings_json=[],
        evidence_json={"phase": "episode_contract"},
        created_at=now,
    )

    nodes = _workflow_nodes((), (step,), (), (), (review,))
    node = next(item for item in nodes if item["id"] == "director:morning")

    assert node["providerStatus"] == "succeeded"
    assert node["contractStatus"] == "parsed"
    assert node["semanticReviewStatus"] == "rejected"
    assert node["status"] == "planning_rejected"


def test_failed_storyboard_blocks_review_and_keeps_storyboard_stage() -> None:
    now = datetime.now(UTC)
    run_id = uuid.uuid4()
    episode_id = uuid.uuid4()
    run = ProductionRun(
        id=run_id,
        content_date=date(2026, 8, 4),
        planning_json={"dayBrief": {"theme": "雨后花园"}},
        pipeline_settings_json={},
        status="planned",
        created_at=now,
        updated_at=now,
    )
    episode = Episode(
        id=episode_id,
        production_run_id=run_id,
        slot="morning",
        sort_order=1,
        script_json=episode_for(Slot.MORNING).script.model_dump(mode="json"),
        prompt_overrides_json=None,
        status="failed",
        selected_video_asset_id=None,
        created_at=now,
        updated_at=now,
    )
    step = WorkflowStep(
        id=uuid.uuid4(),
        production_run_id=run_id,
        episode_id=episode_id,
        parent_step_id=None,
        kind="image",
        status="failed",
        attempt=1,
        operation_key="image:storyboard",
        idempotency_key="c" * 64,
        provider="volcengine-ark-standard",
        provider_task_id=None,
        model="seedream",
        input_hash="d" * 64,
        input_snapshot_json={},
        error_json={
            "code": "provider_request_serialization_failed",
            "message": "请求序列化失败",
        },
        created_at=now,
        updated_at=now,
    )

    nodes = _workflow_nodes((episode,), (step,), (), (), ())
    review_node = next(item for item in nodes if item["id"] == "storyboard-review:morning")

    assert review_node["status"] == "failed"
    assert review_node["providerStatus"] == "failed"
    assert review_node["semanticReviewStatus"] == "not_started"
    assert review_node["error"]["code"] == "provider_request_serialization_failed"
    assert _current_stage(run, (episode,), (step,), ()) == "storyboard"


def test_replanned_storyboard_uses_newest_step_instead_of_largest_attempt() -> None:
    now = datetime.now(UTC)
    run_id = uuid.uuid4()
    episode_id = uuid.uuid4()

    def storyboard_step(*, attempt: int, created_at: datetime) -> WorkflowStep:
        return WorkflowStep(
            id=uuid.uuid4(),
            production_run_id=run_id,
            episode_id=episode_id,
            parent_step_id=None,
            kind="image",
            status="failed",
            attempt=attempt,
            operation_key="image:storyboard",
            idempotency_key=uuid.uuid4().hex.ljust(64, "0"),
            provider="volcengine-ark-standard",
            provider_task_id=None,
            model="seedream",
            input_hash=uuid.uuid4().hex.ljust(64, "0"),
            input_snapshot_json={},
            error_json={"code": "review_rejected", "message": "rejected"},
            created_at=created_at,
            updated_at=created_at,
        )

    old_script_retry = storyboard_step(attempt=2, created_at=now)
    new_script_first_attempt = storyboard_step(
        attempt=1,
        created_at=now + timedelta(seconds=1),
    )
    episode = Episode(
        id=episode_id,
        production_run_id=run_id,
        slot="morning",
        sort_order=1,
        script_json=episode_for(Slot.MORNING).script.model_dump(mode="json"),
        prompt_overrides_json=None,
        status="failed",
        selected_video_asset_id=None,
        created_at=now,
        updated_at=now,
    )
    old_asset = Asset(
        id=uuid.uuid4(),
        production_run_id=run_id,
        episode_id=episode_id,
        producing_step_id=old_script_retry.id,
        role="storyboard_panel",
        semantic_key="storyboard:panel-01",
        scope="episode",
        status="rejected",
        media_type="image",
        local_path="old.png",
        sha256="a" * 64,
        byte_size=1,
        metadata_json={"panelOrdinal": 1},
        created_at=now,
    )
    new_asset = Asset(
        id=uuid.uuid4(),
        production_run_id=run_id,
        episode_id=episode_id,
        producing_step_id=new_script_first_attempt.id,
        role="storyboard_panel",
        semantic_key="storyboard:panel-01",
        scope="episode",
        status="rejected",
        media_type="image",
        local_path="new.png",
        sha256="b" * 64,
        byte_size=1,
        metadata_json={"panelOrdinal": 1},
        created_at=now + timedelta(seconds=1),
    )

    nodes = _workflow_nodes(
        (episode,),
        (old_script_retry, new_script_first_attempt),
        (),
        (old_asset, new_asset),
        (),
    )
    node = next(item for item in nodes if item["id"] == "storyboard:morning")

    assert node["stepId"] == str(new_script_first_attempt.id)
    assert node["assetIds"] == [str(new_asset.id)]
    assert [item["id"] for item in node["attempts"]] == [
        str(old_script_retry.id),
        str(new_script_first_attempt.id),
    ]


@pytest.mark.parametrize("status", ["failed", "expired", "cancelled"])
def test_terminal_media_steps_advertise_explicit_retry(status: str) -> None:
    now = datetime.now(UTC)
    step = WorkflowStep(
        id=uuid.uuid4(),
        production_run_id=uuid.uuid4(),
        episode_id=uuid.uuid4(),
        parent_step_id=None,
        kind="video",
        status=status,
        attempt=1,
        operation_key="video:single_pass",
        idempotency_key=uuid.uuid4().hex.ljust(64, "0"),
        provider="volcengine-ark-standard",
        provider_task_id="task-old",
        model="seedance",
        input_hash="e" * 64,
        input_snapshot_json={},
        error_json={"code": status, "message": status},
        created_at=now,
        updated_at=now,
    )

    assert step_dict(step)["availableActions"] == [
        {"type": "retry", "label": "重试该节点", "paid": True}
    ]


def test_provider_running_and_unknown_steps_advertise_safe_recovery() -> None:
    now = datetime.now(UTC)

    def step(*, kind: str, status: str, task_id: str | None):
        return WorkflowStep(
            id=uuid.uuid4(),
            production_run_id=uuid.uuid4(),
            episode_id=uuid.uuid4(),
            parent_step_id=None,
            kind=kind,
            status=status,
            attempt=1,
            operation_key=("video:single_pass" if kind == "video" else "image:storyboard"),
            idempotency_key=uuid.uuid4().hex.ljust(64, "0"),
            provider="volcengine-ark-standard",
            provider_task_id=task_id,
            model="provider-model",
            input_hash="f" * 64,
            input_snapshot_json={},
            error_json=None,
            created_at=now,
            updated_at=now,
        )

    running = step_dict(step(kind="video", status="running", task_id="task-1"))
    video_unknown = step_dict(
        step(kind="video", status="submission_unknown", task_id=None)
    )
    image_unknown = step_dict(
        step(kind="image", status="submission_unknown", task_id=None)
    )

    assert running["availableActions"][0]["type"] == "continue_query"
    assert video_unknown["availableActions"][0]["type"] == "reconcile"
    assert image_unknown["availableActions"][0] == {
        "type": "retry_unknown_image",
        "label": "接受风险并重新生成",
        "paid": True,
        "requiresDuplicateBillingAck": True,
    }


def test_run_dict_exposes_delivery_as_backend_owned_action() -> None:
    now = datetime.now(UTC)
    row = SimpleNamespace(
        id=uuid.uuid4(),
        content_date=date(2026, 8, 4),
        planning_json={"dayBrief": {"theme": "雨后花园"}},
        status="ready",
        pipeline_settings_json={
            "allowPaidGeneration": True,
            "dayBrief": "auto",
            "script": "auto",
            "storyboard": "auto",
            "video": "auto",
        },
        created_at=now,
        updated_at=now,
    )

    assert run_dict(row)["availableActions"] == [
        {"type": "deliver", "label": "构建交付包", "paid": False}
    ]
