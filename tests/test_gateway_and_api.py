"""Ark映射顺序和精简HTTP契约。"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

from cat_video_generator.config import RuntimeSettings
from cat_video_generator.domain.rendering import MediaSource, VideoInputMode, build_video_input_plan
from cat_video_generator.infrastructure.ark.gateway import ArkGateway
from cat_video_generator.interfaces.api_schemas import GenerateRequest, RetryStepRequest


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
    first = tmp_path / "person.png"
    second = tmp_path / "cat.png"
    first.write_bytes(b"person")
    second.write_bytes(b"cat")
    sources = (
        MediaSource(
            asset_id=uuid.uuid4(),
            semantic_key="person:front",
            media_type="image",
            sha256="a" * 64,
            metadata={"width": 720, "height": 1280},
        ),
        MediaSource(
            asset_id=uuid.uuid4(),
            semantic_key="cat:front",
            media_type="image",
            sha256="b" * 64,
            metadata={"width": 720, "height": 1280},
        ),
    )
    plan = build_video_input_plan(
        input_mode=VideoInputMode.MULTIMODAL_REFERENCE,
        resolution="720p",
        duration_seconds=9,
        sources=sources,
    )
    tasks = Tasks()
    client = SimpleNamespace(content_generation=SimpleNamespace(tasks=tasks))
    result = ArkGateway(runtime(tmp_path), client=client).submit_video(
        prompt="测试Prompt",
        input_plan=plan,
        input_paths=(first, second),
    )
    assert result.task_id == "task-1"
    assert tasks.kwargs["model"] == "doubao-seedance-2-0-mini-260615"
    assert tasks.kwargs["generate_audio"] is True
    assert [item["role"] for item in tasks.kwargs["content"][1:]] == [
        "reference_image",
        "reference_image",
    ]


def test_removed_http_flags_are_not_accepted() -> None:
    assert set(GenerateRequest.model_fields) == {"slot", "allow_paid_generation"}
    assert set(RetryStepRequest.model_fields) == {
        "reason",
        "allow_paid_generation",
    }
