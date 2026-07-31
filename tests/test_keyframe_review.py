from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from cat_video_generator.application.ports import (
    ImageResult,
    LandedAsset,
    ReviewCommitResult,
    StoredAsset,
    StoredEpisode,
    StoredRun,
    StoredStep,
    VisualReviewResult,
)
from cat_video_generator.application.production import ProductionService
from cat_video_generator.application.visual_preparation import (
    ReferenceSelectionPlan,
    VisualPreparationService,
)
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

    def find_reusable_asset(self, **_):
        return None

    def create_step_intent(self, **_):
        return self.step

    def save_prompt(self, **_):
        return uuid.uuid4()

    def set_step_status(self, _, target, **__):
        self.statuses.append(target)

    def set_asset_status(self, _, status):
        self.asset_status = status

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

    def commit_asset_review(self, **kwargs):
        self.review = kwargs
        self.asset_status = (
            "approved" if kwargs["decision"] == "approved" else "rejected"
        )
        self.statuses.append(
            StepStatus.SUCCEEDED
            if kwargs["decision"] == "approved"
            else StepStatus.FAILED
        )
        return ReviewCommitResult(uuid.uuid4(), kwargs["decision"], False)

    def fail_step(self, *_, **__):
        raise AssertionError("图片生成不应失败")


class ImageGateway:
    image_model = "image-model"
    video_model = "video-model"

    def generate_image(self, **_):
        return ImageResult(
            url="https://example.invalid/frame.png", model=self.image_model
        )


class ReviewGateway:
    review_model = "review-model"

    def review_keyframe(self, **_):
        return VisualReviewResult(
            identity_ok=True,
            style_ok=True,
            world_state_ok=True,
            scene_topology_ok=True,
            confidence=0.92,
            violations=(),
            evidence=("一人一猫和场景锚点均清楚",),
            response_id="review-response",
            model=self.review_model,
            request_hash="c" * 64,
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
        ("semantic_auto", "approved", StepStatus.SUCCEEDED, "passed", False),
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
    service = VisualPreparationService(
        repository=repository,
        media_gateway=ImageGateway(),
        visual_review_gateway=ReviewGateway(),
        asset_store=ImageStore(path),
        media_probe=ImageProbe(),
        provider_name="test",
        keyframe_review_mode=mode,
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
    )

    asset = service._ensure_image(  # noqa: SLF001
        episode,
        ReferenceSelectionPlan(("person:front",), (reference,)),
        target="first_frame",
        allow_unverified_keyframes=mode == "technical_auto",
    )

    assert asset.status == asset_status
    expected = [StepStatus.SUBMITTING, StepStatus.AWAITING_REVIEW]
    if mode != "manual":
        expected.append(terminal)
    assert repository.statuses == expected
    assert repository.review is not None
    evidence = repository.review["evidence"]
    assert evidence["semanticReviewStatus"] == semantic
    assert evidence["autoApprovedUnverified"] is unverified
    if mode == "technical_auto":
        assert evidence["semanticVerified"] is False
    if mode == "semantic_auto":
        assert evidence["semanticVerified"] is True


def test_keyframe_reuse_excludes_rejected_and_tracks_canon_hash(
    tmp_path: Path,
    daily_plan,
) -> None:
    episode = StoredEpisode(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        plan=daily_plan.episodes[1],
        status=EpisodeStatus.PREPARING_VISUALS,
        selected_video_asset_id=None,
    )
    reusable = StoredAsset(
        id=uuid.uuid4(),
        run_id=episode.run_id,
        episode_id=episode.id,
        step_id=uuid.uuid4(),
        role="first_frame",
        media_type="image",
        scope="episode",
        status="approved",
        path=tmp_path / "approved.png",
        sha256="f" * 64,
        metadata={"width": 720, "height": 1280},
    )

    class Repository:
        calls: list[dict] = []

        def find_reusable_asset(self, **kwargs):
            self.calls.append(kwargs)
            return reusable

    repository = Repository()
    service = VisualPreparationService(
        repository=repository,
        media_gateway=ImageGateway(),
        visual_review_gateway=ReviewGateway(),
        asset_store=ImageStore(tmp_path / "unused.png"),
        media_probe=ImageProbe(),
        provider_name="test",
        keyframe_review_mode="semantic_auto",
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
    )

    def reference(sha256: str) -> StoredAsset:
        return StoredAsset(
            id=uuid.uuid4(),
            run_id=None,
            episode_id=None,
            step_id=None,
            role="person",
            media_type="image",
            scope="canon",
            status="approved",
            path=tmp_path / f"{sha256[:4]}.png",
            sha256=sha256,
            metadata={},
            semantic_key="person:front",
        )

    first = ReferenceSelectionPlan(("person:front",), (reference("a" * 64),))
    changed = ReferenceSelectionPlan(("person:front",), (reference("b" * 64),))
    service._ensure_image(  # noqa: SLF001
        episode,
        first,
        target="first_frame",
        allow_unverified_keyframes=False,
    )
    service._ensure_image(  # noqa: SLF001
        episode,
        changed,
        target="first_frame",
        allow_unverified_keyframes=False,
    )

    assert all(
        call["statuses"] == ("candidate", "approved", "ready")
        for call in repository.calls
    )
    assert repository.calls[0]["input_hash"] != repository.calls[1]["input_hash"]


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
        def _run_episode(self, episode, **_):
            return {"episodeId": str(episode.id), "status": episode.status.value}

    repository = Repository()
    service = CompletedProductionService(
        repository=repository,
        visual_preparation=object(),
        video_execution=object(),
    )

    service.run_day(
        run_id,
        slot=daily_plan.episodes[0].slot,
        allow_paid_generation=True,
    )

    assert repository.target is RunStatus.REVIEWING
