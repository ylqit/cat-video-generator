"""视频初始任务、官方延展、幂等状态与恢复边界。"""

from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import episode_for

from cat_video_generator.application.ports import (
    LandedAsset,
    StoredAsset,
    StoredEpisode,
    StoredStep,
    VideoTaskResult,
)
from cat_video_generator.application.video_execution import VideoExecutionService
from cat_video_generator.domain.contracts import Slot
from cat_video_generator.domain.visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
)
from cat_video_generator.domain.workflow import EpisodeStatus, StepStatus


class Repository:
    def __init__(self, episode: StoredEpisode, anchor: StoredAsset) -> None:
        self.episode = episode
        self.steps: dict[uuid.UUID, StoredStep] = {}
        self.assets: dict[uuid.UUID, StoredAsset] = {anchor.id: anchor}

    def create_step_with_prompt_intent(self, **kwargs):
        existing = next(
            (
                item
                for item in self.steps.values()
                if item.operation_key == kwargs["operation_key"]
                and item.attempt == kwargs["attempt"]
                and item.input_snapshot == kwargs["input_snapshot"]
            ),
            None,
        )
        if existing is not None:
            return existing, uuid.uuid4()
        step = StoredStep(
            id=uuid.uuid4(),
            run_id=kwargs["run_id"],
            episode_id=kwargs["episode_id"],
            kind=kwargs["kind"],
            status=StepStatus.PENDING,
            attempt=kwargs["attempt"],
            provider_task_id=None,
            model=kwargs["model"],
            operation_key=kwargs["operation_key"],
            input_snapshot=kwargs["input_snapshot"],
        )
        self.steps[step.id] = step
        return step, uuid.uuid4()

    def set_step_status(self, step_id, target, **kwargs):
        current = self.steps[step_id]
        patch = kwargs.get("input_snapshot_patch") or {}
        self.steps[step_id] = replace(
            current,
            status=target,
            provider_task_id=kwargs.get("provider_task_id", current.provider_task_id),
            input_snapshot={**current.input_snapshot, **patch},
        )

    def get_step(self, step_id):
        return self.steps[step_id]

    def patch_step_snapshot(self, step_id, patch):
        current = self.steps[step_id]
        self.steps[step_id] = replace(current, input_snapshot={**current.input_snapshot, **patch})

    def get_episode(self, run_id, slot):
        return self.episode

    def set_episode_status(self, episode_id, target):
        self.episode = replace(self.episode, status=target)

    def save_asset(self, **kwargs):
        landed = kwargs["landed"]
        asset = StoredAsset(
            id=uuid.uuid4(),
            run_id=kwargs["run_id"],
            episode_id=kwargs["episode_id"],
            step_id=kwargs["step_id"],
            role=kwargs["role"],
            media_type=kwargs["media_type"],
            scope=kwargs["scope"],
            status=kwargs["status"],
            path=landed.path,
            sha256=landed.sha256,
            metadata=kwargs["metadata"],
            semantic_key=kwargs["semantic_key"],
        )
        self.assets[asset.id] = asset
        return asset

    def asset_detail(self, asset_id):
        return self.assets[asset_id]

    def list_assets(self, **kwargs):
        return tuple(
            item
            for item in self.assets.values()
            if item.run_id == kwargs.get("run_id")
            and item.episode_id == kwargs.get("episode_id")
            and item.status in kwargs.get("statuses", (item.status,))
        )

    def next_step_attempt(self, *, episode_id, kind, operation_key):
        return (
            max(
                (
                    item.attempt
                    for item in self.steps.values()
                    if item.operation_key == operation_key
                ),
                default=0,
            )
            + 1
        )

    def fail_step(self, step_id, **kwargs):
        current = self.steps[step_id]
        target = (
            StepStatus.SUBMISSION_UNKNOWN if kwargs.get("submission_unknown") else StepStatus.FAILED
        )
        self.steps[step_id] = replace(
            current,
            status=target,
            error_code=kwargs.get("code"),
            error_message=kwargs.get("message"),
        )

    def find_step_by_provider_task_id(self, provider_task_id):
        return next(
            (item for item in self.steps.values() if item.provider_task_id == provider_task_id),
            None,
        )


class Gateway:
    def __init__(self, model="doubao-seedance-2-0-260128") -> None:
        self.video_model = model
        self.calls = []

    def submit_video(self, *, prompt, input_plan, input_paths=(), input_urls=()):
        self.calls.append((prompt, input_plan, input_paths or input_urls))
        index = len(self.calls)
        return VideoTaskResult(
            task_id=f"task-{index}",
            status="succeeded",
            video_url=f"https://example.invalid/video-{index}.mp4",
        )

    def get_video_task(self, task_id):
        return VideoTaskResult(
            task_id=task_id,
            status="succeeded",
            video_url=f"https://example.invalid/{task_id}.mp4",
        )

    def list_video_tasks(self, *, model, page_size=100):
        return ()


class AssetStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.count = 0

    def download(self, url, *, suffix):
        self.count += 1
        path = self.root / f"video-{self.count}{suffix}"
        path.write_bytes(f"video-{self.count}".encode())
        return LandedAsset(
            path=path, sha256=f"{self.count:x}".rjust(64, "0"), byte_size=path.stat().st_size
        )

    def concatenate_videos(self, paths):
        self.count += 1
        path = self.root / f"video-{self.count}-assembled.mp4"
        path.write_bytes(b"".join(item.read_bytes() for item in paths))
        return LandedAsset(
            path=path, sha256=f"{self.count:x}".rjust(64, "0"), byte_size=path.stat().st_size
        )


class Probe:
    def __init__(self) -> None:
        self.expected_totals = []

    def inspect_video(self, path, **kwargs):
        self.expected_totals.append(kwargs["expected_duration_seconds"])
        return {
            "passed": True,
            "durationSeconds": kwargs["expected_duration_seconds"],
            "resolution": kwargs["expected_resolution"],
            "failures": [],
        }


def stored_episode_and_anchor(tmp_path: Path, *, duration: int):
    run_id = uuid.uuid4()
    episode = StoredEpisode(
        id=uuid.uuid4(),
        run_id=run_id,
        plan=episode_for(Slot.NOON, duration=duration),
        status=EpisodeStatus.VIDEO_PENDING,
        selected_video_asset_id=None,
    )
    path = tmp_path / "opening.png"
    path.write_bytes(b"opening")
    anchor = StoredAsset(
        id=uuid.uuid4(),
        run_id=run_id,
        episode_id=episode.id,
        step_id=uuid.uuid4(),
        role="opening_anchor",
        media_type="image",
        scope="episode",
        status="approved",
        path=path,
        sha256="a" * 64,
        metadata={},
        semantic_key="opening:noon",
    )
    return episode, anchor


def service(repository, gateway, asset_store, probe):
    return VideoExecutionService(
        repository=repository,
        media_gateway=gateway,
        asset_store=asset_store,
        media_probe=probe,
        provider_name="volcengine-ark-standard",
        resolution="720p",
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
        diagnostic_mode="off",
        poll_interval_seconds=0.001,
        task_timeout_seconds=1,
    )


def test_medium_video_uses_initial_then_one_cumulative_extension(tmp_path: Path) -> None:
    episode, anchor = stored_episode_and_anchor(tmp_path, duration=22)
    repository = Repository(episode, anchor)
    gateway = Gateway()
    asset_store = AssetStore(tmp_path)
    probe = Probe()

    result = service(repository, gateway, asset_store, probe).execute(episode, anchor)

    assert result["status"] == "succeeded"
    assert [item.operation_key for item in repository.steps.values()] == [
        "video:single_pass",
        "video:extend:2",
    ]
    assert [call[1].operation.value for call in gateway.calls] == ["initial", "extend"]
    assert [call[1].bindings[0].provider_role.value for call in gateway.calls] == [
        "first_frame",
        "reference_video",
    ]
    assert probe.expected_totals == [11, 11, 22]
    assert sorted(
        item.role for item in repository.assets.values() if item.media_type == "video"
    ) == [
        "video",
        "video_intermediate",
    ]
    assert repository.episode.status is EpisodeStatus.CONTENT_REVIEW


def test_long_video_uses_at_most_two_extensions(tmp_path: Path) -> None:
    episode, anchor = stored_episode_and_anchor(tmp_path, duration=36)
    repository = Repository(episode, anchor)
    gateway = Gateway()
    probe = Probe()

    service(repository, gateway, AssetStore(tmp_path), probe).execute(episode, anchor)

    assert [item.operation_key for item in repository.steps.values()] == [
        "video:single_pass",
        "video:extend:2",
        "video:extend:3",
    ]
    assert probe.expected_totals == [12, 12, 12, 36]


def test_mini_model_rejects_extension_before_creating_paid_step(tmp_path: Path) -> None:
    episode, anchor = stored_episode_and_anchor(tmp_path, duration=22)
    repository = Repository(episode, anchor)
    gateway = Gateway("doubao-seedance-2-0-mini-260615")

    with pytest.raises(ValueError, match="不支持官方视频延展"):
        service(repository, gateway, AssetStore(tmp_path), Probe()).execute(episode, anchor)

    assert repository.steps == {}
    assert gateway.calls == []
