from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from cat_video_generator.application.multi_clip_finalization import (
    MultiClipFinalization,
)
from cat_video_generator.application.ports import (
    StoredAsset,
    StoredEpisode,
    StoredRun,
    StoredStep,
)
from cat_video_generator.application.production import ProductionService
from cat_video_generator.application.resolution_comparison import (
    ResolutionComparisonService,
)
from cat_video_generator.application.retry import RetryService
from cat_video_generator.application.video_execution import VideoExecutionService
from cat_video_generator.application.visual_preparation import (
    ReferenceSelectionPlan,
    VisualPreparationService,
)
from cat_video_generator.domain.contracts import GenerationStrategy, SegmentPlan
from cat_video_generator.domain.visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
)
from cat_video_generator.domain.workflow import (
    EpisodeStatus,
    RunStatus,
    StepKind,
    StepStatus,
)
from cat_video_generator.infrastructure.media import finalizer as finalizer_module
from cat_video_generator.infrastructure.media.finalizer import (
    FfmpegMediaFinalizer,
    MediaFinalizationError,
)


def _asset(
    tmp_path: Path,
    *,
    role: str = "video",
    step_id: uuid.UUID | None = None,
    duration_ms: int = 5000,
    audio_codec: str = "aac",
) -> StoredAsset:
    path = tmp_path / f"{uuid.uuid4()}.mp4"
    path.write_bytes(b"media")
    return StoredAsset(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        episode_id=uuid.uuid4(),
        step_id=step_id,
        role=role,
        media_type="video",
        scope="episode",
        status="ready",
        path=path,
        sha256=uuid.uuid4().hex * 2,
        metadata={
            "durationMs": duration_ms,
            "videoCodec": "h264",
            "width": 720,
            "height": 1280,
            "frameRate": "25/1",
            "timeBase": "1/12800",
            "pixelFormat": "yuv420p",
            "audioCodec": audio_codec,
            "audioSampleRate": 48000,
            "audioChannels": 2,
            "audioChannelLayout": "stereo",
        },
    )


def test_ffmpeg_escalates_after_stream_copy_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parts = (_asset(tmp_path), _asset(tmp_path))
    calls: list[list[str]] = []

    def fake_run(_executable: Path, arguments: list[str]) -> None:
        calls.append(arguments)
        if len(calls) == 1:
            raise MediaFinalizationError("stream copy failed")
        Path(arguments[-1]).write_bytes(b"ok")

    monkeypatch.setattr(finalizer_module, "_run_ffmpeg", fake_run)
    result = FfmpegMediaFinalizer(
        ffmpeg_path=tmp_path / "ffmpeg.exe",
        work_root=tmp_path / "work",
    ).concat(parts, target_duration_seconds=10)

    assert result.policy == "audio_transcode"
    assert len(calls) == 2
    assert calls[0][calls[0].index("-c") : calls[0].index("-c") + 2] == [
        "-c",
        "copy",
    ]
    assert "-c:v" in calls[1] and "copy" in calls[1]


def test_ffmpeg_escalates_to_full_transcode_after_two_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parts = (_asset(tmp_path), _asset(tmp_path))
    calls: list[list[str]] = []

    def fake_run(_executable: Path, arguments: list[str]) -> None:
        calls.append(arguments)
        if len(calls) < 3:
            raise MediaFinalizationError("codec path failed")
        Path(arguments[-1]).write_bytes(b"ok")

    monkeypatch.setattr(finalizer_module, "_run_ffmpeg", fake_run)
    result = FfmpegMediaFinalizer(
        ffmpeg_path=tmp_path / "ffmpeg.exe",
        work_root=tmp_path / "work",
    ).concat(parts, target_duration_seconds=10)

    assert result.policy == "transcode"
    assert len(calls) == 3
    assert calls[2][calls[2].index("-c:v") + 1] == "libx264"


def test_ffmpeg_transcodes_only_audio_when_video_is_compatible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parts = (_asset(tmp_path), _asset(tmp_path, audio_codec="mp3"))
    calls: list[list[str]] = []

    def fake_run(_executable: Path, arguments: list[str]) -> None:
        calls.append(arguments)
        Path(arguments[-1]).write_bytes(b"ok")

    monkeypatch.setattr(finalizer_module, "_run_ffmpeg", fake_run)
    result = FfmpegMediaFinalizer(
        ffmpeg_path=tmp_path / "ffmpeg.exe",
        work_root=tmp_path / "work",
    ).concat(parts, target_duration_seconds=10)

    assert result.policy == "audio_transcode"
    assert calls[0][calls[0].index("-c:v") + 1] == "copy"
    assert calls[0][calls[0].index("-c:a") + 1] == "aac"


def test_ffmpeg_normalizes_overlong_multi_clip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parts = (
        _asset(tmp_path, duration_ms=8000),
        _asset(tmp_path, duration_ms=8000),
    )
    calls: list[list[str]] = []

    def fake_run(_executable: Path, arguments: list[str]) -> None:
        calls.append(arguments)
        Path(arguments[-1]).write_bytes(b"ok")

    monkeypatch.setattr(finalizer_module, "_run_ffmpeg", fake_run)
    result = FfmpegMediaFinalizer(
        ffmpeg_path=tmp_path / "ffmpeg.exe",
        work_root=tmp_path / "work",
    ).concat(parts, target_duration_seconds=15)

    assert result.policy == "transcode"
    assert result.duration_trimmed is True
    assert calls[0][calls[0].index("-t") + 1] == "15"


def test_ffmpeg_refuses_missing_or_too_short_segments(tmp_path: Path) -> None:
    first = _asset(tmp_path, duration_ms=4000)
    second = _asset(tmp_path, duration_ms=4000)
    finalizer = FfmpegMediaFinalizer(
        ffmpeg_path=tmp_path / "ffmpeg.exe",
        work_root=tmp_path / "work",
    )
    with pytest.raises(MediaFinalizationError, match="明显短于"):
        finalizer.concat((first, second), target_duration_seconds=10)


class RetryRepository:
    def __init__(
        self,
        episode: StoredEpisode,
        assets: tuple[StoredAsset, ...],
    ) -> None:
        self.episode = episode
        self.assets = {item.id: item for item in assets}
        self.attempt_requests: list[tuple[StepKind, str]] = []

    def next_step_attempt(self, *, kind, operation_key, **_):
        self.attempt_requests.append((kind, operation_key))
        return 2

    def asset_detail(self, asset_id):
        return self.assets[asset_id]

    def get_episode(self, *_):
        return self.episode

    def set_episode_status(self, *_):
        return None

    def list_assets(self, **_):
        return tuple(self.assets.values())


def _episode(daily_plan, *, strategy=GenerationStrategy.SINGLE_PASS) -> StoredEpisode:
    plan = daily_plan.episodes[0]
    if strategy is GenerationStrategy.MULTI_CLIP:
        plan = plan.model_copy(
            update={
                "generation_strategy": strategy,
                "segments": [
                    SegmentPlan(
                        order=1,
                        shot_order=1,
                        action_orders=[1],
                        duration_seconds=4,
                    ),
                    SegmentPlan(
                        order=2,
                        shot_order=2,
                        action_orders=[2, 3],
                        duration_seconds=6,
                    ),
                ],
            }
        )
    return StoredEpisode(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        plan=plan,
        status=EpisodeStatus.VIDEO_PENDING,
        selected_video_asset_id=None,
    )


def _step(
    episode: StoredEpisode,
    *,
    kind: StepKind,
    operation_key: str,
    input_asset_ids: tuple[uuid.UUID, ...] = (),
) -> StoredStep:
    return StoredStep(
        id=uuid.uuid4(),
        run_id=episode.run_id,
        episode_id=episode.id,
        kind=kind,
        status=StepStatus.FAILED,
        attempt=1,
        provider_task_id=None,
        model="model",
        request_summary={
            "operationKey": operation_key,
            "inputAssetIds": [str(item) for item in input_asset_ids],
        },
    )


def test_video_and_segment_retry_use_next_attempt(
    tmp_path: Path,
    daily_plan,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for operation_key, strategy in (
        ("video:single_pass", GenerationStrategy.SINGLE_PASS),
        ("video:segment:1", GenerationStrategy.MULTI_CLIP),
    ):
        episode = _episode(daily_plan, strategy=strategy)
        asset = _asset(tmp_path, role="person")
        repository = RetryRepository(episode, (asset,))
        service = VideoExecutionService(
            repository=repository,
            media_gateway=type("Gateway", (), {"video_model": "video"})(),
            asset_store=object(),
            media_probe=object(),
            provider_name="test",
            resolution="720p",
            style_profile=DEFAULT_STYLE_PROFILE,
        )
        captured: dict[str, object] = {}

        def capture(*_args, target_capture=captured, **kwargs):
            target_capture.update(kwargs)
            return {"status": "captured"}

        target = (
            "_generate_single_pass"
            if strategy is GenerationStrategy.SINGLE_PASS
            else "_generate_segment"
        )
        monkeypatch.setattr(service, target, capture)
        summary = {
            "operationKey": operation_key,
            "inputAssetIds": [str(asset.id)],
        }
        if strategy is GenerationStrategy.MULTI_CLIP:
            summary["videoInputPlan"] = {"input_mode": "multimodal_reference"}
        step = _step(
            episode,
            kind=StepKind.VIDEO,
            operation_key=operation_key,
            input_asset_ids=(asset.id,),
        )
        step = replace(step, request_summary=summary)
        service.retry_video(episode, step, reason="修复供应商暂时失败")
        assert captured["attempt"] == 2
        assert captured["retry_of_step_id"] == str(step.id)


def test_image_comparison_and_finalize_retry_use_next_attempt(
    tmp_path: Path,
    daily_plan,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode = _episode(daily_plan)
    reference = _asset(tmp_path, role="person")
    repository = RetryRepository(episode, (reference,))

    image_service = VisualPreparationService(
        repository=repository,
        media_gateway=object(),
        visual_review_gateway=object(),
        asset_store=object(),
        media_probe=object(),
        provider_name="test",
        keyframe_review_mode="manual",
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
    )
    selection = ReferenceSelectionPlan(("person:front",), (reference,))
    monkeypatch.setattr(image_service, "select_references", lambda _: selection)
    image_capture: dict[str, object] = {}

    def capture_image(*_args, **kwargs):
        image_capture.update(kwargs)
        return reference

    monkeypatch.setattr(image_service, "_ensure_image", capture_image)
    image_step = _step(
        episode,
        kind=StepKind.IMAGE,
        operation_key="image:first_frame",
    )
    image_step = replace(
        image_step,
        request_summary={
            "operationKey": "image:first_frame",
            "target": "first_frame",
        },
    )
    image_service.retry_image(
        episode,
        image_step,
        reason="修复关键帧语义误杀",
        allow_unverified_keyframes=False,
    )
    assert image_capture["attempt"] == 2

    source_step_id = uuid.uuid4()
    source = _asset(tmp_path, step_id=source_step_id)
    repository.assets[source.id] = source
    comparison = ResolutionComparisonService(
        repository=repository,
        media_gateway=object(),
        asset_store=object(),
        media_probe=object(),
        provider_name="test",
    )
    compare_capture: dict[str, object] = {}

    def capture_comparison(*_args, **kwargs):
        compare_capture.update(kwargs)
        return {"status": "captured"}

    monkeypatch.setattr(comparison, "_compare_episode", capture_comparison)
    compare_step = _step(
        episode,
        kind=StepKind.VIDEO,
        operation_key="video:resolution_comparison:720p",
    )
    compare_step = replace(
        compare_step,
        request_summary={
            "operationKey": "video:resolution_comparison:720p",
            "comparisonOfStepId": str(source_step_id),
        },
    )
    comparison.retry_comparison(
        episode,
        compare_step,
        reason="修复对比任务供应商失败",
    )
    assert compare_capture["attempt"] == 2

    part1 = _asset(tmp_path, role="video_segment")
    part2 = _asset(tmp_path, role="video_segment")
    repository.assets.update({part1.id: part1, part2.id: part2})
    finalization = MultiClipFinalization(
        repository=repository,
        asset_store=object(),
        media_probe=object(),
        media_finalizer=object(),
        resolution="720p",
        video_diagnostic=None,
    )
    finalize_capture: dict[str, object] = {}

    def capture_finalize(*_args, **kwargs):
        finalize_capture.update(kwargs)
        return {"status": "captured"}

    monkeypatch.setattr(finalization, "finalize", capture_finalize)
    qc_step = _step(
        episode,
        kind=StepKind.QC,
        operation_key="qc:multi_clip_concat",
    )
    qc_step = replace(
        qc_step,
        request_summary={
            "operationKey": "qc:multi_clip_concat",
            "segmentAssetIds": [str(part1.id), str(part2.id)],
        },
    )
    finalization.retry_finalize(
        episode,
        qc_step,
        reason="重新执行本地拼接",
    )
    assert finalize_capture["attempt"] == 2


def test_submission_unknown_never_creates_retry_attempt(daily_plan) -> None:
    episode = _episode(daily_plan)
    step = replace(
        _step(
            episode,
            kind=StepKind.VIDEO,
            operation_key="video:single_pass",
        ),
        status=StepStatus.SUBMISSION_UNKNOWN,
    )

    class Repository:
        def get_step(self, _):
            return step

    class ForbiddenService:
        def __getattr__(self, _):
            raise AssertionError("submission_unknown不得触发任何生产服务")

    forbidden = ForbiddenService()
    service = RetryService(
        repository=Repository(),
        visual_preparation=forbidden,
        video_execution=forbidden,
        resolution_comparison=forbidden,
    )
    with pytest.raises(ValueError, match="只能先对账"):
        service.retry_step(
            step.id,
            reason="网络中断后响应结果未知",
            allow_paid_generation=True,
        )


def test_paid_retry_requires_fresh_explicit_permission(daily_plan) -> None:
    episode = _episode(daily_plan)
    step = _step(
        episode,
        kind=StepKind.VIDEO,
        operation_key="video:single_pass",
    )

    class Repository:
        def get_step(self, _):
            return step

    class ForbiddenService:
        def __getattr__(self, _):
            raise AssertionError("缺少付费许可时不得调用生产服务")

    forbidden = ForbiddenService()
    service = RetryService(
        repository=Repository(),
        visual_preparation=forbidden,
        video_execution=forbidden,
        resolution_comparison=forbidden,
    )
    with pytest.raises(ValueError, match="allow-paid-generation"):
        service.retry_step(
            step.id,
            reason="供应商终态失败后重新生成",
            allow_paid_generation=False,
        )


def test_run_day_reports_retry_command_without_creating_paid_attempt(
    daily_plan,
) -> None:
    episode = replace(
        _episode(daily_plan),
        status=EpisodeStatus.FAILED,
    )
    failed_step = _step(
        episode,
        kind=StepKind.VIDEO,
        operation_key="video:single_pass",
    )

    class Repository:
        run_status_changes: list[RunStatus] = []

        def get_run(self, _):
            return StoredRun(
                id=episode.run_id,
                content_date=daily_plan.content_date,
                status=RunStatus.FAILED.value,
                plan=daily_plan,
            )

        def get_episode(self, *_):
            return episode

        def list_episodes(self, _):
            return (episode,)

        def latest_retryable_step(self, _):
            return failed_step

        def set_run_status(self, _, target):
            self.run_status_changes.append(target)

    class ForbiddenProductionDependency:
        def __getattr__(self, _):
            raise AssertionError("run-day不得隐式创建新的媒体attempt")

    repository = Repository()
    forbidden = ForbiddenProductionDependency()
    result = ProductionService(
        repository=repository,
        visual_preparation=forbidden,
        video_execution=forbidden,
    ).run_day(
        episode.run_id,
        slot=episode.plan.slot,
        allow_paid_generation=True,
    )

    item = result["episodes"][0]
    assert item["failedStepId"] == str(failed_step.id)
    assert item["operationKey"] == "video:single_pass"
    assert item["nextAction"].startswith(f"cvg retry-step {failed_step.id}")
    assert repository.run_status_changes == []
