"""Ark映射顺序和精简HTTP契约。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

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
    Episode,
    ProductionRun,
    Review,
    WorkflowStep,
)
from cat_video_generator.infrastructure.db.query_repository import _current_stage, _workflow_nodes
from cat_video_generator.interfaces.api import _SPAStaticFiles
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


def test_removed_http_flags_are_not_accepted() -> None:
    assert set(GenerateRequest.model_fields) == {"slot", "allow_paid_generation"}
    assert set(RetryStepRequest.model_fields) == {
        "reason",
        "allow_paid_generation",
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
