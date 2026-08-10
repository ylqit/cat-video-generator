"""定妆图与单张开场锚点的图片生产链。"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from pathlib import Path

from conftest import episode_for

from cat_video_generator.application.ports import (
    ImageResult,
    ImageReviewResult,
    LandedAsset,
    ReviewCommitResult,
    StoredAsset,
    StoredEpisode,
    StoredPrompt,
    StoredStep,
)
from cat_video_generator.application.visual_preparation import VisualPreparationService
from cat_video_generator.domain.contracts import Slot
from cat_video_generator.domain.visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
)
from cat_video_generator.domain.workflow import EpisodeStatus, StepStatus


class Repository:
    def __init__(self, episode: StoredEpisode, canon: list[StoredAsset]) -> None:
        self.episode = episode
        self.assets = {item.id: item for item in canon}
        self.steps: dict[uuid.UUID, StoredStep] = {}
        self.prompts: dict[uuid.UUID, StoredPrompt] = {}

    def list_assets(self, **kwargs):
        values = list(self.assets.values())
        if "roles" in kwargs:
            values = [item for item in values if item.role in kwargs["roles"]]
        if "statuses" in kwargs:
            values = [item for item in values if item.status in kwargs["statuses"]]
        if "semantic_keys" in kwargs:
            values = [item for item in values if item.semantic_key in kwargs["semantic_keys"]]
        if kwargs.get("episode_id") is not None:
            wanted = kwargs["episode_id"]
            values = [item for item in values if item.episode_id in {None, wanted}]
        return tuple(values)

    def find_reusable_asset(self, **kwargs):
        return next(
            (
                item
                for item in self.assets.values()
                if item.episode_id == kwargs["episode_id"]
                and item.role == kwargs["role"]
                and item.status in kwargs["statuses"]
                and item.metadata.get("inputHash") == kwargs["input_hash"]
            ),
            None,
        )

    def create_step_with_prompt_intent(self, **kwargs):
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
        prompt_id = self.save_prompt(
            step_id=step.id,
            parent_prompt_id=kwargs["parent_prompt_id"],
            purpose=kwargs["prompt_purpose"],
            model=kwargs["prompt_model"],
            text=kwargs["prompt_text"],
        )
        return step, prompt_id

    def save_prompt(self, **kwargs):
        prompt_id = uuid.uuid4()
        text = kwargs["text"]
        self.prompts[prompt_id] = StoredPrompt(
            id=prompt_id,
            step_id=kwargs["step_id"],
            purpose=kwargs["purpose"],
            model=kwargs["model"],
            text=text,
            sha256=hashlib.sha256(text.encode()).hexdigest(),
        )
        return prompt_id

    def get_prompt_for_step(self, step_id, *, purpose):
        return next(
            item
            for item in self.prompts.values()
            if item.step_id == step_id and item.purpose is purpose
        )

    def set_step_status(self, step_id, target, **kwargs):
        self.steps[step_id] = replace(self.steps[step_id], status=target)

    def get_step(self, step_id):
        return self.steps[step_id]

    def set_episode_status(self, episode_id, target):
        self.episode = replace(self.episode, status=target)

    def get_episode(self, run_id, slot):
        return self.episode

    def list_episodes(self, run_id):
        return (self.episode,)

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

    def commit_asset_review(self, **kwargs):
        asset = self.assets[kwargs["asset_id"]]
        status = "approved" if kwargs["decision"] == "approved" else "rejected"
        self.assets[asset.id] = replace(asset, status=status)
        step = self.steps[asset.step_id]
        self.steps[step.id] = replace(
            step,
            status=StepStatus.SUCCEEDED if status == "approved" else StepStatus.FAILED,
        )
        return ReviewCommitResult(uuid.uuid4(), kwargs["decision"], False)

    def record_review(self, **kwargs):
        return uuid.uuid4()

    def next_step_attempt(self, **kwargs):
        return (
            max(
                (
                    step.attempt
                    for step in self.steps.values()
                    if step.operation_key == kwargs["operation_key"]
                ),
                default=0,
            )
            + 1
        )

    def fail_step(self, step_id, **kwargs):
        self.steps[step_id] = replace(self.steps[step_id], status=StepStatus.FAILED)


class MediaGateway:
    image_model = "doubao-seedream-5-0-260128"

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Path, ...]]] = []

    def generate_image(self, *, prompt, reference_paths):
        self.calls.append((prompt, reference_paths))
        return ImageResult(
            url=f"https://example.invalid/image-{len(self.calls)}.png", model=self.image_model
        )


class ReviewGateway:
    review_model = "doubao-seed-2-1-pro-260628"

    def review_image(self, *, prompt, image_path, reference_paths):
        return ImageReviewResult(
            identity_ok=True,
            style_ok=True,
            appearance_ok=True,
            composition_ok=True,
            critical_props_ok=True,
            confidence=0.96,
            violations=(),
            warnings=(),
            evidence=("人物、猫咪、画风和活动焦点均符合",),
            response_id="review-1",
            model=self.review_model,
            request_hash="b" * 64,
        )


class AssetStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.count = 0

    def download(self, url, *, suffix):
        self.count += 1
        path = self.root / f"generated-{self.count}{suffix}"
        path.write_bytes(f"image-{self.count}".encode())
        return LandedAsset(path, f"{self.count:x}".rjust(64, "0"), path.stat().st_size)

    def crop_local(self, path, *, box):
        raise AssertionError("精确9:16测试图不应裁切")


class Probe:
    def inspect_image(self, path):
        return {"passed": True, "width": 900, "height": 1600, "format": "png"}


def canon_assets(tmp_path: Path) -> list[StoredAsset]:
    keys = (
        "person:headshot",
        "person:fullbody",
        "cat:front",
        "style:line_texture",
        "style:indoor",
        "style:outdoor",
    )
    result = []
    for index, key in enumerate(keys, 1):
        path = tmp_path / f"canon-{index}.png"
        path.write_bytes(key.encode())
        result.append(
            StoredAsset(
                id=uuid.uuid4(),
                run_id=None,
                episode_id=None,
                step_id=None,
                role=key.split(":", 1)[0],
                media_type="image",
                scope="canon",
                status="approved",
                path=path,
                sha256=f"{index:x}".rjust(64, "0"),
                metadata={},
                semantic_key=key,
            )
        )
    return result


def test_episode_generates_only_look_and_opening_anchor(tmp_path: Path) -> None:
    run_id = uuid.uuid4()
    episode = StoredEpisode(
        id=uuid.uuid4(),
        run_id=run_id,
        plan=episode_for(Slot.MORNING),
        status=EpisodeStatus.PLANNED,
        selected_video_asset_id=None,
    )
    repository = Repository(episode, canon_assets(tmp_path))
    gateway = MediaGateway()
    service = VisualPreparationService(
        repository=repository,
        media_gateway=gateway,
        visual_review_gateway=ReviewGateway(),
        asset_store=AssetStore(tmp_path),
        media_probe=Probe(),
        provider_name="volcengine-ark-standard",
        image_review_mode="semantic_auto",
        image_retry_delay_seconds=0.001,
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
    )

    anchor = service.prepare(episode)

    assert anchor is not None and anchor.role == "opening_anchor"
    assert anchor.status == "approved"
    assert [step.operation_key for step in repository.steps.values()] == [
        "image:look",
        "image:opening_anchor",
    ]
    assert len(gateway.calls) == 2
    assert all("故事板" not in prompt for prompt, _ in gateway.calls)
    assert repository.episode.status is EpisodeStatus.VIDEO_PENDING


def test_same_appearance_reuses_approved_look(tmp_path: Path) -> None:
    run_id = uuid.uuid4()
    first = StoredEpisode(
        uuid.uuid4(), run_id, episode_for(Slot.MORNING), EpisodeStatus.PLANNED, None
    )
    repository = Repository(first, canon_assets(tmp_path))
    gateway = MediaGateway()
    service = VisualPreparationService(
        repository=repository,
        media_gateway=gateway,
        visual_review_gateway=ReviewGateway(),
        asset_store=AssetStore(tmp_path),
        media_probe=Probe(),
        provider_name="volcengine-ark-standard",
        image_review_mode="semantic_auto",
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
    )
    service.prepare(first)
    second = StoredEpisode(
        uuid.uuid4(),
        run_id,
        episode_for(Slot.MORNING),
        EpisodeStatus.PLANNED,
        None,
    )
    repository.episode = second
    service.prepare(second)
    look_calls = sum("全身定妆图" in prompt for prompt, _ in gateway.calls)

    assert look_calls == 1
