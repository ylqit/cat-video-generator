"""Seedance监看窗口与submission_unknown对账恢复。"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from conftest import episode_for

from cat_video_generator.application.ports import (
    StoredEpisode,
    StoredStep,
    VideoTaskResult,
)
from cat_video_generator.application.video_execution import VideoExecutionService
from cat_video_generator.domain.contracts import Slot
from cat_video_generator.domain.rendering import (
    MediaSource,
    VideoInputMode,
    build_video_input_plan,
)
from cat_video_generator.domain.snapshots import VideoInputSnapshot
from cat_video_generator.domain.visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
)
from cat_video_generator.domain.workflow import EpisodeStatus, StepKind, StepStatus


class RecoveryRepository:
    def __init__(self, step: StoredStep) -> None:
        self.step = step
        self.bound_owners: dict[str, StoredStep] = {}
        self.snapshot_patches: list[dict[str, Any]] = []

    def get_step(self, step_id: uuid.UUID) -> StoredStep:
        assert step_id == self.step.id
        return self.step

    def patch_step_snapshot(self, step_id: uuid.UUID, patch: dict[str, Any]) -> None:
        assert step_id == self.step.id
        self.snapshot_patches.append(patch)
        self.step = replace(
            self.step,
            input_snapshot={**self.step.input_snapshot, **patch},
        )

    def find_step_by_provider_task_id(self, task_id: str) -> StoredStep | None:
        return self.bound_owners.get(task_id)

    def set_step_status(
        self,
        step_id: uuid.UUID,
        target: StepStatus,
        *,
        provider_task_id: str | None = None,
        input_snapshot_patch: dict[str, Any] | None = None,
    ) -> None:
        assert step_id == self.step.id
        self.step = replace(
            self.step,
            status=target,
            provider_task_id=provider_task_id or self.step.provider_task_id,
            input_snapshot={
                **self.step.input_snapshot,
                **(input_snapshot_patch or {}),
            },
        )

    def set_episode_status(self, *_: Any, **__: Any) -> None:
        raise AssertionError("本测试Episode已处于video_generating，不应改写")


class RecoveryGateway:
    video_model = "seedance-test"

    def __init__(self, tasks: tuple[VideoTaskResult, ...] = ()) -> None:
        self.tasks = tasks
        self.submit_calls = 0
        self.get_calls = 0
        self.list_calls = 0

    def submit_video(self, **_: Any) -> VideoTaskResult:
        self.submit_calls += 1
        raise AssertionError("恢复与对账不得重新提交Seedance")

    def get_video_task(self, task_id: str) -> VideoTaskResult:
        self.get_calls += 1
        return VideoTaskResult(task_id=task_id, status="running")

    def list_video_tasks(self, **_: Any) -> tuple[VideoTaskResult, ...]:
        self.list_calls += 1
        return self.tasks


def _snapshot() -> dict[str, Any]:
    sources = tuple(
        MediaSource(
            asset_id=uuid.uuid4(),
            semantic_key=f"storyboard:panel-{index:02d}",
            media_type="image",
            sha256=str(index) * 64,
            metadata={"width": 480, "height": 854},
        )
        for index in range(1, 4)
    )
    plan = build_video_input_plan(
        input_mode=VideoInputMode.STORYBOARD_REFERENCE,
        resolution="480p",
        duration_seconds=9,
        sources=sources,
    )
    return VideoInputSnapshot(
        prompt_sha256=hashlib.sha256(b"prompt").hexdigest(),
        input_plan=plan,
        input_asset_ids=tuple(item.asset_id for item in sources),
        api_request_timeout_seconds=120,
        task_timeout_seconds=1800,
        poll_interval_seconds=10,
    ).model_dump(mode="json")


def _step(status: StepStatus, *, task_id: str | None) -> StoredStep:
    now = datetime.now(UTC)
    return StoredStep(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        episode_id=uuid.uuid4(),
        kind=StepKind.VIDEO,
        status=status,
        attempt=1,
        provider_task_id=task_id,
        model="seedance-test",
        operation_key="video:single_pass",
        input_snapshot=_snapshot(),
        created_at=now,
        submitted_at=now,
    )


def _episode(step: StoredStep) -> StoredEpisode:
    assert step.episode_id is not None
    return StoredEpisode(
        id=step.episode_id,
        run_id=step.run_id,
        plan=episode_for(Slot.MORNING),
        status=EpisodeStatus.VIDEO_GENERATING,
        selected_video_asset_id=None,
    )


def _service(repository: RecoveryRepository, gateway: RecoveryGateway):
    return VideoExecutionService(
        repository=repository,
        media_gateway=gateway,
        asset_store=object(),
        media_probe=object(),
        provider_name="test",
        resolution="480p",
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
        task_timeout_seconds=0.002,
        poll_interval_seconds=0.01,
    )


def test_polling_window_end_keeps_task_resumable_without_second_post() -> None:
    step = _step(StepStatus.QUEUED, task_id="ark-task-running")
    repository = RecoveryRepository(step)
    gateway = RecoveryGateway()

    result = _service(repository, gateway).resume_step(_episode(step), step)

    assert result["status"] == "provider_running"
    assert repository.step.status is StepStatus.RUNNING
    assert repository.step.provider_task_id == "ark-task-running"
    assert repository.step.input_snapshot["provider_task_status"] == (
        "polling_window_elapsed"
    )
    assert gateway.submit_calls == 0
    assert gateway.get_calls == 1


def test_reconciliation_filters_specs_and_persists_candidates() -> None:
    step = _step(StepStatus.SUBMISSION_UNKNOWN, task_id=None)
    created = step.created_at
    assert created is not None
    matching = VideoTaskResult(
        task_id="ark-match",
        status="running",
        model="seedance-test",
        created_at=created,
        duration_seconds=9,
        ratio="9:16",
        resolution="480p",
        generate_audio=True,
    )
    wrong_resolution = replace(matching, task_id="ark-720", resolution="720p")
    repository = RecoveryRepository(step)
    gateway = RecoveryGateway((matching, wrong_resolution))

    candidates = _service(repository, gateway).reconciliation_candidates(step)

    assert [item["taskId"] for item in candidates] == ["ark-match"]
    assert repository.step.input_snapshot["reconciliation_candidates"] == (
        candidates[0],
    )
    assert gateway.list_calls == 1
    assert gateway.submit_calls == 0


def test_reconcile_binds_confirmed_task_then_only_queries_it() -> None:
    step = _step(StepStatus.SUBMISSION_UNKNOWN, task_id=None)
    created = step.created_at
    assert created is not None
    matching = VideoTaskResult(
        task_id="ark-match",
        status="running",
        model="seedance-test",
        created_at=created,
        duration_seconds=9,
        ratio="9:16",
        resolution="480p",
        generate_audio=True,
    )
    repository = RecoveryRepository(step)
    gateway = RecoveryGateway((matching,))

    result = _service(repository, gateway).reconcile_step(
        _episode(step),
        step,
        provider_task_id="ark-match",
    )

    assert result["status"] == "provider_running"
    assert repository.step.provider_task_id == "ark-match"
    assert repository.step.status is StepStatus.RUNNING
    assert gateway.submit_calls == 0
    assert gateway.get_calls == 1
