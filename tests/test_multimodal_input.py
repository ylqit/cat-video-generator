from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from cat_video_generator.config import RuntimeSettings
from cat_video_generator.domain.contracts import (
    MediaBinding,
    MediaModality,
    MediaPurpose,
    ProviderMediaRole,
    VideoInputMode,
    VideoInputPlan,
)
from cat_video_generator.domain.media import MediaSource, build_video_input_plan
from cat_video_generator.infrastructure.ark.gateway import ArkGateway


def _source(
    role: str,
    media_type: str,
    *,
    index: int,
) -> MediaSource:
    metadata = (
        {"width": 720, "height": 1280}
        if media_type == "image"
        else {"durationSeconds": 5}
    )
    return MediaSource(
        asset_id=uuid.uuid4(),
        role=role,
        media_type=media_type,
        sha256=f"{index:x}" * 64,
        metadata=metadata,
    )


def test_multimodal_plan_uses_stable_per_modality_aliases(daily_plan) -> None:
    episode = daily_plan.episodes[0].model_copy(
        update={"video_input_mode": VideoInputMode.MULTIMODAL_REFERENCE}
    )
    sources = (
        _source("person", "image", index=1),
        _source("cat", "image", index=2),
        _source("style", "image", index=3),
        _source("motion", "video", index=4),
        _source("atmosphere", "audio", index=5),
    )
    plan = build_video_input_plan(
        episode,
        model="doubao-seedance-2-0-mini-260615",
        resolution="720p",
        sources=sources,
    )
    assert [item.prompt_alias for item in plan.bindings] == [
        "@图片1",
        "@图片2",
        "@图片3",
        "@视频1",
        "@音频1",
    ]
    assert [item.provider_role for item in plan.bindings] == [
        ProviderMediaRole.REFERENCE_IMAGE,
        ProviderMediaRole.REFERENCE_IMAGE,
        ProviderMediaRole.REFERENCE_IMAGE,
        ProviderMediaRole.REFERENCE_VIDEO,
        ProviderMediaRole.REFERENCE_AUDIO,
    ]


def test_strict_first_last_rejects_extra_reference_media(daily_plan) -> None:
    episode = daily_plan.episodes[2].model_copy(
        update={"video_input_mode": VideoInputMode.STRICT_FIRST_LAST}
    )
    valid = (
        _source("first_frame", "image", index=1),
        _source("last_frame", "image", index=2),
    )
    plan = build_video_input_plan(
        episode,
        model="doubao-seedance-2-0-mini-260615",
        resolution="720p",
        sources=valid,
    )
    assert [item.provider_role for item in plan.bindings] == [
        ProviderMediaRole.FIRST_FRAME,
        ProviderMediaRole.LAST_FRAME,
    ]
    with pytest.raises(ValueError, match="只允许"):
        build_video_input_plan(
            episode,
            model="doubao-seedance-2-0-mini-260615",
            resolution="720p",
            sources=(*valid, _source("style", "image", index=3)),
        )


def test_multimodal_plan_rejects_audio_without_visual_input(daily_plan) -> None:
    episode = daily_plan.episodes[0].model_copy(
        update={"video_input_mode": VideoInputMode.MULTIMODAL_REFERENCE}
    )
    with pytest.raises(ValueError, match="纯音频"):
        build_video_input_plan(
            episode,
            model="doubao-seedance-2-0-mini-260615",
            resolution="720p",
            sources=(_source("atmosphere", "audio", index=1),),
        )


def test_video_input_contract_rejects_invalid_alias_and_strict_media() -> None:
    common = {
        "asset_id": uuid.uuid4(),
        "source_role": "first_frame",
        "modality": MediaModality.IMAGE,
        "purpose": MediaPurpose.SEMANTIC_OPENING,
        "provider_role": ProviderMediaRole.FIRST_FRAME,
        "ordinal": 1,
        "sha256": "1" * 64,
    }
    with pytest.raises(ValueError, match="素材别名必须"):
        MediaBinding(**common, prompt_alias="@图片2")

    first_frame = MediaBinding(**common, prompt_alias="@图片1")
    extra_reference = MediaBinding(
        asset_id=uuid.uuid4(),
        source_role="style",
        modality=MediaModality.IMAGE,
        purpose=MediaPurpose.STYLE,
        provider_role=ProviderMediaRole.REFERENCE_IMAGE,
        ordinal=2,
        prompt_alias="@图片2",
        sha256="2" * 64,
    )
    with pytest.raises(ValueError, match="严格帧模式"):
        VideoInputPlan(
            model="doubao-seedance-2-0-mini-260615",
            input_mode=VideoInputMode.STRICT_FIRST_FRAME,
            resolution="720p",
            duration_seconds=10,
            bindings=[first_frame, extra_reference],
        )


class _Tasks:
    def __init__(self) -> None:
        self.request = None

    def create(self, **kwargs):
        self.request = kwargs
        return SimpleNamespace(id="task-1")


class _Client:
    def __init__(self) -> None:
        self.content_generation = SimpleNamespace(tasks=_Tasks())


def test_gateway_maps_image_video_audio_content(tmp_path) -> None:
    paths = (
        tmp_path / "person.png",
        tmp_path / "motion.mp4",
        tmp_path / "atmosphere.mp3",
    )
    for path in paths:
        path.write_bytes(b"reference")
    bindings = [
        MediaBinding(
            asset_id=uuid.uuid4(),
            source_role=role,
            modality=modality,
            purpose=purpose,
            provider_role=provider_role,
            ordinal=1,
            prompt_alias=alias,
            sha256=character * 64,
        )
        for role, modality, purpose, provider_role, alias, character in (
            (
                "person",
                MediaModality.IMAGE,
                MediaPurpose.IDENTITY,
                ProviderMediaRole.REFERENCE_IMAGE,
                "@图片1",
                "1",
            ),
            (
                "motion",
                MediaModality.VIDEO,
                MediaPurpose.MOTION,
                ProviderMediaRole.REFERENCE_VIDEO,
                "@视频1",
                "2",
            ),
            (
                "atmosphere",
                MediaModality.AUDIO,
                MediaPurpose.ATMOSPHERE,
                ProviderMediaRole.REFERENCE_AUDIO,
                "@音频1",
                "3",
            ),
        )
    ]
    plan = VideoInputPlan(
        model="doubao-seedance-2-0-mini-260615",
        input_mode=VideoInputMode.MULTIMODAL_REFERENCE,
        resolution="720p",
        duration_seconds=10,
        bindings=bindings,
    )
    settings = RuntimeSettings.from_env(
        {
            "ARK_API_KEY": "test-key",
            "ARK_ACCESS_MODE": "standard",
            "ARK_BASE_URL": "https://ark.cn-beijing.volces.com/api/v3",
            "ARK_IMAGE_MODEL": "doubao-seedream-5-0-260128",
            "ARK_VIDEO_MODEL": "doubao-seedance-2-0-mini-260615",
            "ARK_PLANNING_MODEL": "director-model",
            "PATH": "",
        }
    )
    client = _Client()
    gateway = ArkGateway(settings, client=client)
    result = gateway.submit_video(
        prompt="使用@图片1、@视频1和@音频1生成视频",
        input_plan=plan,
        input_paths=paths,
    )
    assert result.task_id == "task-1"
    content = client.content_generation.tasks.request["content"]
    assert [item["type"] for item in content] == [
        "text",
        "image_url",
        "video_url",
        "audio_url",
    ]
    assert [item.get("role") for item in content[1:]] == [
        "reference_image",
        "reference_video",
        "reference_audio",
    ]
