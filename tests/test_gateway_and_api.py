"""Ark请求映射、HTTP DTO与Web节点投影。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from conftest import episode_for

from cat_video_generator.config import RuntimeSettings
from cat_video_generator.domain.contracts import Slot, StoryInputMode
from cat_video_generator.domain.rendering import (
    MediaSource,
    RenderOperation,
    build_render_plan,
    build_video_input_plan,
)
from cat_video_generator.infrastructure.ark.gateway import ArkGateway
from cat_video_generator.infrastructure.db.models import Episode, WorkflowStep
from cat_video_generator.infrastructure.db.query_repository import _workflow_nodes
from cat_video_generator.infrastructure.db.records import step_dict
from cat_video_generator.interfaces.api_schemas import RetryStepRequest, StoryProjectRequest


class TaskClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(id=f"task-{len(self.calls)}")


class Client:
    def __init__(self) -> None:
        self.tasks = TaskClient()
        self.content_generation = SimpleNamespace(tasks=self.tasks)


def settings(tmp_path: Path) -> RuntimeSettings:
    event_root = tmp_path / "events"
    event_root.mkdir()
    return RuntimeSettings.from_env(
        {
            "ARK_API_KEY": "test-key",
            "ARK_VIDEO_MODEL": "doubao-seedance-2-0-260128",
            "CAT_VIDEO_EVENT_SEED_ROOT": str(event_root),
        },
        config_root=tmp_path,
    )


def test_gateway_maps_initial_anchor_and_extension_video_roles(tmp_path: Path) -> None:
    image_path = tmp_path / "opening.png"
    image_path.write_bytes(b"png")
    client = Client()
    gateway = ArkGateway(settings(tmp_path), client=client)

    image_plan = build_video_input_plan(
        operation=RenderOperation.INITIAL,
        resolution="720p",
        duration_seconds=12,
        source=MediaSource(uuid4(), "opening:morning", "image", "a" * 64, {}),
    )
    extend_plan = build_video_input_plan(
        operation=RenderOperation.EXTEND,
        resolution="720p",
        duration_seconds=10,
        source=MediaSource(uuid4(), "video:section-1", "video", "b" * 64, {}),
    )

    gateway.submit_video(prompt="initial", input_plan=image_plan, input_sources=(image_path,))
    gateway.submit_video(
        prompt="extend",
        input_plan=extend_plan,
        input_sources=("https://example.invalid/previous.mp4",),
    )

    first_media = client.tasks.calls[0]["content"][1]
    second_media = client.tasks.calls[1]["content"][1]
    assert first_media["role"] == "first_frame"
    assert first_media["type"] == "image_url"
    assert second_media["role"] == "reference_video"
    assert second_media["type"] == "video_url"
    assert all(call["generate_audio"] is True for call in client.tasks.calls)
    assert all(call["ratio"] == "9:16" for call in client.tasks.calls)


def test_story_project_request_accepts_web_focus_and_duration_controls() -> None:
    request = StoryProjectRequest.model_validate(
        {
            "contentDate": "2026-08-10",
            "projectInput": {
                "theme": "春日放风筝的一天",
                "inputMode": "theme_expand",
                "sceneRoute": "progressive_locations",
            },
            "allowPaidGeneration": True,
            "creativeControls": {
                "default_activity_focus": "cat_lead",
                "slot_controls": [
                    {"slot": "morning", "activity_focus": "inherit", "duration_mode": "short"},
                    {"slot": "noon", "activity_focus": "cat_lead", "duration_mode": "medium"},
                    {"slot": "evening", "activity_focus": "inherit", "duration_mode": "short"},
                ],
            },
        }
    )

    assert request.creative_controls is not None
    assert request.creative_controls.requested_focus(Slot.NOON).value == "cat_lead"
    assert request.creative_controls.slot_controls[1].duration_mode.value == "medium"


def test_retry_request_keeps_duplicate_billing_ack_explicit() -> None:
    request = RetryStepRequest.model_validate(
        {
            "reason": "供应商同步图片请求超时后人工确认再次生成",
            "allowPaidGeneration": True,
            "acknowledgeDuplicateBilling": True,
        }
    )
    assert request.allow_paid_generation is True
    assert request.acknowledge_duplicate_billing is True


def _step(*, episode_id, operation_key: str, status: str, kind: str = "video", attempt: int = 1):
    now = datetime.now(timezone.utc)
    return WorkflowStep(
        id=uuid4(),
        production_run_id=uuid4(),
        episode_id=episode_id,
        parent_step_id=None,
        kind=kind,
        status=status,
        attempt=attempt,
        operation_key=operation_key,
        idempotency_key=(operation_key + str(attempt)).ljust(64, "0")[:64],
        provider="volcengine-ark-standard",
        provider_task_id="task-existing" if status in {"queued", "running"} else None,
        model="video-model",
        input_hash="a" * 64,
        input_snapshot_json={},
        error_json=None,
        submitted_at=now,
        completed_at=None,
        created_at=now,
    )


def test_step_projection_returns_resume_reconcile_and_retry_actions() -> None:
    episode_id = uuid4()
    running = step_dict(
        _step(episode_id=episode_id, operation_key="video:single_pass", status="running")
    )
    unknown = _step(
        episode_id=episode_id, operation_key="video:single_pass", status="submission_unknown"
    )
    failed = step_dict(
        _step(episode_id=episode_id, operation_key="video:extend:2", status="failed")
    )

    assert running["availableActions"][0]["type"] == "continue_query"
    assert step_dict(unknown)["availableActions"][0]["type"] == "reconcile"
    assert failed["availableActions"][0]["type"] == "retry"


def test_medium_episode_graph_aggregates_extensions_into_stable_video_node() -> None:
    episode_id = uuid4()
    plan = episode_for(Slot.NOON, duration=22)
    row = Episode(
        id=episode_id,
        production_run_id=uuid4(),
        slot="noon",
        sort_order=2,
        script_json=plan.script.model_dump(mode="json"),
        prompt_overrides_json={},
        status="video_pending",
        selected_video_asset_id=None,
    )
    steps = (
        _step(episode_id=episode_id, operation_key="video:single_pass", status="succeeded"),
        _step(episode_id=episode_id, operation_key="video:extend:2", status="running"),
    )
    nodes = _workflow_nodes(
        (row,),
        steps,
        (),
        (),
        (),
        guided=False,
        project_ready=True,
        input_mode=StoryInputMode.THEME_EXPAND,
    )

    ids = {item["semanticNodeId"] for item in nodes}
    assert "noon:video" in ids
    assert not any(item.startswith("video:noon:") for item in ids)
    video = next(item for item in nodes if item["semanticNodeId"] == "noon:video")
    assert len(video["attemptIds"]) == 2
    assert len(build_render_plan(plan).sections) == 2


def test_guided_canvas_projects_locked_future_nodes_without_fake_steps() -> None:
    nodes = _workflow_nodes(
        (),
        (),
        (),
        (),
        (),
        guided=True,
        project_ready=False,
        input_mode=StoryInputMode.THEME_EXPAND,
    )
    by_id = {item["semanticNodeId"]: item for item in nodes}

    assert by_id["morning:director"]["availability"] == "locked"
    assert by_id["noon:director"]["executionStatus"] == "not_created"
    assert by_id["evening:video"]["stepId"] is None
    assert by_id["noon:director"]["lockReason"] == "确认生活故事项目边界"


def test_guided_canvas_unlocks_only_next_director_after_outcome() -> None:
    morning = episode_for(Slot.MORNING)
    row = Episode(
        id=uuid4(),
        production_run_id=uuid4(),
        slot="morning",
        sort_order=1,
        script_json=morning.script.model_dump(mode="json"),
        prompt_overrides_json={},
        status="ready",
        selected_video_asset_id=None,
    )
    nodes = _workflow_nodes(
        (row,),
        (),
        (),
        (),
        (),
        {"morning": {"summary": "上午实际结果"}},
        guided=True,
        project_ready=True,
        input_mode=StoryInputMode.THEME_EXPAND,
    )
    by_id = {item["semanticNodeId"]: item for item in nodes}

    assert by_id["noon:director"]["availability"] == "ready"
    assert by_id["evening:director"]["availability"] == "locked"
    assert by_id["noon:director"]["stepId"] is None


def test_auto_day_canvas_unlocks_all_slot_directors_after_project_outline() -> None:
    nodes = _workflow_nodes(
        (),
        (),
        (),
        (),
        (),
        guided=False,
        project_ready=True,
        input_mode=StoryInputMode.THEME_EXPAND,
    )
    by_id = {item["semanticNodeId"]: item for item in nodes}

    assert all(by_id[f"{slot.value}:director"]["availability"] == "ready" for slot in Slot)
