from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from cat_video_generator.application.ports import (
    ImageResult,
    LandedAsset,
    StoredAsset,
    StoredEpisode,
    StoredRun,
    StoredStep,
)
from cat_video_generator.application.production import ProductionService
from cat_video_generator.domain.workflow import (
    EpisodeStatus,
    RunStatus,
    StepKind,
    StepStatus,
)


class ImageRepository:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.step = StoredStep(
            id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            episode_id=uuid.uuid4(),
            kind=StepKind.IMAGE,
            status=StepStatus.PENDING,
            attempt=1,
            provider_task_id=None,
            model="image-model",
        )
        self.statuses: list[StepStatus] = []
        self.asset_status: str | None = None
        self.review: dict | None = None

    def list_assets(self, **_):
        return ()

    def create_step_intent(self, **_):
        return self.step

    def save_prompt(self, **_):
        return uuid.uuid4()

    def set_step_status(self, _, target, **__):
        self.statuses.append(target)

    def save_asset(self, **kwargs):
        self.asset_status = kwargs["status"]
        return StoredAsset(
            id=uuid.uuid4(),
            run_id=kwargs["run_id"],
            episode_id=kwargs["episode_id"],
            step_id=kwargs["step_id"],
            role=kwargs["role"],
            media_type=kwargs["media_type"],
            scope=kwargs["scope"],
            status=kwargs["status"],
            path=kwargs["landed"].path,
            sha256=kwargs["landed"].sha256,
            metadata=kwargs["metadata"],
        )

    def record_review(self, **kwargs):
        self.review = kwargs
        return uuid.uuid4()

    def fail_step(self, *_, **__):
        raise AssertionError("图片生成不应失败")


class ImageGateway:
    image_model = "image-model"
    video_model = "video-model"

    def generate_image(self, **_):
        return ImageResult(
            url="https://example.invalid/frame.png", model=self.image_model
        )


class ImageStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def download(self, *_args, **_kwargs):
        return LandedAsset(self.path, "a" * 64, 10)


class ImageProbe:
    def inspect_image(self, _):
        return {"passed": True, "width": 720, "height": 1280, "format": "png"}


@pytest.mark.parametrize(
    ("mode", "asset_status", "terminal", "semantic", "unverified"),
    [
        ("technical_auto", "approved", StepStatus.SUCCEEDED, "skipped", True),
        ("manual", "candidate", StepStatus.AWAITING_REVIEW, "pending", False),
    ],
)
def test_keyframe_review_mode_controls_continuation(
    tmp_path: Path,
    daily_plan,
    mode: str,
    asset_status: str,
    terminal: StepStatus,
    semantic: str,
    unverified: bool,
) -> None:
    path = tmp_path / "frame.png"
    path.write_bytes(b"not-read-by-fake")
    repository = ImageRepository(tmp_path)
    episode = StoredEpisode(
        id=repository.step.episode_id,
        run_id=repository.step.run_id,
        plan=daily_plan.episodes[1],
        status=EpisodeStatus.PREPARING_VISUALS,
        selected_video_asset_id=None,
    )
    reference = StoredAsset(
        id=uuid.uuid4(),
        run_id=None,
        episode_id=None,
        step_id=None,
        role="person",
        media_type="image",
        scope="canon",
        status="approved",
        path=path,
        sha256="b" * 64,
        metadata={"referenceView": "front"},
    )
    service = ProductionService(
        repository=repository,
        media_gateway=ImageGateway(),
        asset_store=ImageStore(path),
        media_probe=ImageProbe(),
        provider_name="test",
        resolution="720p",
        keyframe_review_mode=mode,
    )

    asset = service._ensure_image(  # noqa: SLF001
        episode,
        (reference,),
        target="first_frame",
    )

    assert asset.status == asset_status
    assert repository.statuses == [StepStatus.SUBMITTING, terminal]
    assert repository.review is not None
    evidence = repository.review["evidence"]
    assert evidence["semanticReviewStatus"] == semantic
    assert evidence["autoApprovedUnverified"] is unverified
    if mode == "technical_auto":
        assert evidence["semanticVerified"] is False


def test_single_slot_completion_updates_run_to_reviewing(daily_plan) -> None:
    run_id = uuid.uuid4()
    episodes = tuple(
        StoredEpisode(
            id=uuid.uuid4(),
            run_id=run_id,
            plan=plan,
            status=EpisodeStatus.CONTENT_REVIEW,
            selected_video_asset_id=None,
        )
        for plan in daily_plan.episodes
    )

    class Repository:
        target: RunStatus | None = None

        def get_run(self, _):
            return StoredRun(
                id=run_id,
                content_date=daily_plan.content_date,
                status=RunStatus.GENERATING.value,
                plan=daily_plan,
            )

        def get_episode(self, _, slot):
            return next(item for item in episodes if item.plan.slot is slot)

        def list_episodes(self, _):
            return episodes

        def set_run_status(self, _, target):
            self.target = target

    class CompletedProductionService(ProductionService):
        def _run_episode(self, episode):
            return {"episodeId": str(episode.id), "status": episode.status.value}

    repository = Repository()
    service = CompletedProductionService(
        repository=repository,
        media_gateway=object(),
        asset_store=object(),
        media_probe=object(),
        provider_name="test",
        resolution="720p",
        keyframe_review_mode="technical_auto",
    )

    service.run_day(
        run_id,
        slot=daily_plan.episodes[0].slot,
        allow_paid_generation=True,
    )

    assert repository.target is RunStatus.REVIEWING
