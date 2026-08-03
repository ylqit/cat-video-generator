"""故事板组图用例：一次收费步骤、整组审核和失败阻断。"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from conftest import episode_for

from cat_video_generator.application.ports import (
    GatewayError,
    ImageResult,
    LandedAsset,
    ReviewCommitResult,
    StoredAsset,
    StoredEpisode,
    StoredPrompt,
    StoredStep,
    StoryboardReviewResult,
)
from cat_video_generator.application.visual_preparation import VisualPreparationService
from cat_video_generator.domain.contracts import Slot
from cat_video_generator.domain.visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
)
from cat_video_generator.domain.workflow import EpisodeStatus, PromptPurpose, StepStatus


class StoryboardRepository:
    def __init__(self, episode: StoredEpisode, canon: tuple[StoredAsset, ...]) -> None:
        self.episode = episode
        self.assets = list(canon)
        self.steps: list[StoredStep] = []
        self.prompts: list[dict[str, Any]] = []
        self.reviews: list[dict[str, Any]] = []
        self.step_errors: dict[uuid.UUID, dict[str, Any]] = {}

    def set_episode_status(self, episode_id: uuid.UUID, target: EpisodeStatus) -> None:
        assert episode_id == self.episode.id
        self.episode = replace(self.episode, status=target)

    def list_assets(self, **kwargs: Any) -> tuple[StoredAsset, ...]:
        semantic_keys = set(kwargs.get("semantic_keys") or ())
        roles = set(kwargs.get("roles") or ())
        rows = self.assets
        if semantic_keys:
            rows = [item for item in rows if item.semantic_key in semantic_keys]
        if roles:
            rows = [item for item in rows if item.role in roles]
        return tuple(rows)

    def find_reusable_storyboard(self, **kwargs: Any) -> tuple[StoredAsset, ...]:
        input_hash = kwargs["input_hash"]
        matching_steps = {
            step.id
            for step in self.steps
            if step.input_snapshot.get("input_hash") == input_hash
        }
        candidates = [
            item
            for item in self.assets
            if item.role == "storyboard_panel"
            and item.step_id in matching_steps
            and item.status in kwargs["statuses"]
        ]
        return tuple(sorted(candidates, key=lambda item: item.metadata["panelOrdinal"]))

    def create_step_with_prompt_intent(
        self, **kwargs: Any
    ) -> tuple[StoredStep, uuid.UUID]:
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
            input_snapshot={**kwargs["input_snapshot"], "input_hash": kwargs["input_hash"]},
        )
        self.steps.append(step)
        prompt_id = self.save_prompt(
            step_id=step.id,
            parent_prompt_id=kwargs["parent_prompt_id"],
            purpose=kwargs["prompt_purpose"],
            model=kwargs["prompt_model"],
            text=kwargs["prompt_text"],
        )
        return step, prompt_id

    def save_prompt(self, **kwargs: Any) -> uuid.UUID:
        prompt_id = uuid.uuid4()
        self.prompts.append({"id": prompt_id, **kwargs})
        return prompt_id

    def get_prompt_for_step(
        self, step_id: uuid.UUID, *, purpose: PromptPurpose
    ) -> StoredPrompt:
        row = next(
            item
            for item in self.prompts
            if item["step_id"] == step_id and item["purpose"] is purpose
        )
        text = str(row["text"])
        return StoredPrompt(
            id=row["id"],
            step_id=step_id,
            purpose=purpose,
            model=str(row["model"]),
            text=text,
            sha256=hashlib.sha256(text.encode()).hexdigest(),
        )

    def get_step(self, step_id: uuid.UUID) -> StoredStep:
        return next(item for item in self.steps if item.id == step_id)

    def set_step_status(self, step_id: uuid.UUID, target: StepStatus, **_: Any) -> None:
        self.steps = [
            replace(item, status=target) if item.id == step_id else item for item in self.steps
        ]

    def fail_step(self, step_id: uuid.UUID, **kwargs: Any) -> None:
        self.step_errors[step_id] = kwargs
        self.set_step_status(step_id, StepStatus.FAILED)

    def save_asset(self, **kwargs: Any) -> StoredAsset:
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
        self.assets.append(asset)
        return asset

    def commit_storyboard_review(self, **kwargs: Any) -> ReviewCommitResult:
        asset_ids = set(kwargs["asset_ids"])
        self.assets = [
            replace(item, status=kwargs["decision"]) if item.id in asset_ids else item
            for item in self.assets
        ]
        self.set_step_status(
            kwargs["step_id"],
            StepStatus.SUCCEEDED if kwargs["decision"] == "approved" else StepStatus.FAILED,
        )
        self.reviews.append(kwargs)
        return ReviewCommitResult(uuid.uuid4(), kwargs["decision"], False)

    def record_review(self, **kwargs: Any) -> uuid.UUID:
        self.reviews.append(kwargs)
        return uuid.uuid4()


class StoryboardGateway:
    image_model = "seedream-test"
    review_model = "review-test"

    def __init__(self, panel_count: int, *, approved: bool = True) -> None:
        self.panel_count = panel_count
        self.approved = approved
        self.generate_calls = 0

    def generate_storyboard(self, **_: Any) -> tuple[ImageResult, ...]:
        self.generate_calls += 1
        return tuple(
            ImageResult(url=f"https://example.invalid/{index}.png", model=self.image_model)
            for index in range(1, self.panel_count + 1)
        )

    def review_storyboard(self, **_: Any) -> StoryboardReviewResult:
        return StoryboardReviewResult(
            identity_ok=self.approved,
            style_ok=self.approved,
            action_sequence_ok=self.approved,
            continuity_ok=self.approved,
            ending_ok=self.approved,
            confidence=0.95,
            violations=() if self.approved else ("continuity",),
            warnings=("minor background drift",),
            evidence=("ordered panels inspected",),
            response_id="review-response",
            model=self.review_model,
            request_hash="review-hash",
        )


class StoryboardStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.index = 0

    def download(self, url: str, *, suffix: str) -> LandedAsset:
        self.index += 1
        path = self.root / f"panel-{self.index}{suffix}"
        payload = url.encode("utf-8")
        path.write_bytes(payload)
        return LandedAsset(path, hashlib.sha256(payload).hexdigest(), len(payload))


class StoryboardProbe:
    def inspect_image(self, _: Path) -> dict[str, Any]:
        return {"width": 720, "height": 1280, "blackBorderDetected": False}


def _canon_assets(tmp_path: Path, run_id: uuid.UUID) -> tuple[StoredAsset, ...]:
    keys = (
        "person:front",
        "cat:front",
        "style:line_texture",
        "style:indoor",
        "element:pinwheel",
    )
    return tuple(
        StoredAsset(
            id=uuid.uuid4(),
            run_id=None,
            episode_id=None,
            step_id=None,
            role="canon",
            media_type="image",
            scope="canon",
            status="approved",
            path=tmp_path / f"canon-{index}.png",
            sha256=hashlib.sha256(f"canon-{index}".encode()).hexdigest(),
            metadata={},
            semantic_key=key,
        )
        for index, key in enumerate(keys, 1)
    )


def _service(tmp_path: Path, *, returned: int = 3, approved: bool = True):
    run_id = uuid.uuid4()
    episode = StoredEpisode(
        id=uuid.uuid4(),
        run_id=run_id,
        plan=episode_for(Slot.MORNING),
        status=EpisodeStatus.PLANNED,
        selected_video_asset_id=None,
    )
    repository = StoryboardRepository(episode, _canon_assets(tmp_path, run_id))
    gateway = StoryboardGateway(returned, approved=approved)
    service = VisualPreparationService(
        repository=repository,
        media_gateway=gateway,
        visual_review_gateway=gateway,
        asset_store=StoryboardStore(tmp_path),
        media_probe=StoryboardProbe(),
        provider_name="test",
        storyboard_review_mode="semantic_auto",
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
    )
    return service, repository, gateway, episode


def test_episode_creates_one_storyboard_step_and_reuses_approved_group(tmp_path: Path) -> None:
    service, repository, gateway, episode = _service(tmp_path)
    panels = service.prepare(episode)
    assert panels is not None
    assert [item.metadata["panelOrdinal"] for item in panels] == [1, 2, 3]
    assert all(item.status == "approved" for item in panels)
    assert gateway.generate_calls == 1
    assert [item.operation_key for item in repository.steps] == ["image:storyboard"]
    generation = next(
        item for item in repository.prompts if item["purpose"] is PromptPurpose.STORYBOARD
    )
    review = next(
        item
        for item in repository.prompts
        if item["purpose"] is PromptPurpose.STORYBOARD_REVIEW
    )
    assert review["parent_prompt_id"] == generation["id"]
    assert len(repository.steps[0].input_snapshot["reference_asset_ids"]) == 4
    assert repository.reviews[0]["warnings"] == [
        {"code": "storyboard_warning", "message": "minor background drift"}
    ]

    repeated = service.prepare(repository.episode)
    assert repeated is not None
    assert gateway.generate_calls == 1
    assert len(repository.steps) == 1


def test_incomplete_storyboard_fails_before_video_can_exist(tmp_path: Path) -> None:
    service, repository, gateway, episode = _service(tmp_path, returned=2)
    with pytest.raises(ValueError, match="应返回3张"):
        service.prepare(episode)
    assert gateway.generate_calls == 1
    assert repository.steps[0].status is StepStatus.FAILED
    assert repository.episode.status is EpisodeStatus.FAILED
    assert not any(item.role == "storyboard_panel" for item in repository.assets)


def test_gateway_failure_is_not_misclassified_as_storyboard_qc(tmp_path: Path) -> None:
    service, repository, gateway, episode = _service(tmp_path)

    def fail_generation(**_: Any) -> tuple[ImageResult, ...]:
        raise GatewayError(
            "SDK请求参数无法序列化",
            code="provider_request_serialization_failed",
            retryable=False,
        )

    gateway.generate_storyboard = fail_generation  # type: ignore[method-assign]
    with pytest.raises(GatewayError, match="无法序列化"):
        service.prepare(episode)

    step = repository.steps[0]
    assert step.status is StepStatus.FAILED
    assert repository.episode.status is EpisodeStatus.FAILED
    assert repository.step_errors[step.id]["code"] == (
        "provider_request_serialization_failed"
    )


def test_semantic_storyboard_rejection_is_atomic(tmp_path: Path) -> None:
    service, repository, _, episode = _service(tmp_path, approved=False)
    with pytest.raises(RuntimeError, match="语义审核失败"):
        service.prepare(episode)
    panels = [item for item in repository.assets if item.role == "storyboard_panel"]
    assert len(panels) == 3
    assert all(item.status == "rejected" for item in panels)
    assert repository.steps[0].status is StepStatus.FAILED
