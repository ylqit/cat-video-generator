from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image
from volcenginesdkarkruntime import Ark

from cat_video_generator.ark_provider import ArkMediaProvider
from cat_video_generator.config import RuntimeSettings
from cat_video_generator.contracts import (
    ContentValidationError,
    load_json_object,
)
from cat_video_generator.generation.visual_assets import (
    keyframe_context_fingerprint,
)
from cat_video_generator.media import (
    download_to_content_address,
    probe_video,
)
from cat_video_generator.orchestration import resolve_continuation
from cat_video_generator.render_plan import (
    VisualReferences,
    compile_render_plan,
)

ROOT = Path(__file__).resolve().parents[1]


def _settings(tmp_path: Path) -> RuntimeSettings:
    return RuntimeSettings.from_env(
        {
            "ARK_ACCESS_MODE": "agent_plan",
            "ARK_AGENT_PLAN_TIER": "large",
            "ARK_API_KEY": "test-only-key",
            "ARK_BASE_URL": "https://ark.cn-beijing.volces.com/api/plan/v3",
            "ARK_IMAGE_MODEL": "doubao-seedream-5.0-lite",
            "ARK_VIDEO_MODEL": "doubao-seedance-2.0-mini",
            "ARK_VIDEO_RESOLUTION": "480p",
            "MEDIA_WORK_ROOT": str(tmp_path / "work"),
            "MEDIA_ASSET_ROOT": str(tmp_path / "assets"),
            "DELIVERY_OUTPUT_ROOT": str(tmp_path / "output"),
        }
    )


def _episode(slot: str) -> dict:
    pack = load_json_object(
        ROOT / "content" / "examples" / "daily-life-pack.travel.example.json"
    )
    return pack["slots"][slot]


def test_render_plan_compiles_direct_references() -> None:
    plan = compile_render_plan(
        _episode("evening"),
        plan_revision=3,
        render_revision=1,
        references=VisualReferences(
            person_asset_id="person-v1",
            cat_asset_id="cat-v1",
            style_asset_ids=("storybook-pencil-v1",),
        ),
    )

    assert plan["visualInputMode"] == "direct_references"
    assert plan["sceneKeyframeAssetIds"] == []
    assert plan["audioPlan"]["mode"] == "native"
    assert "无角色对白" in plan["videoPrompt"]


def test_travel_pack_exercises_all_visual_input_modes() -> None:
    references = VisualReferences(
        person_asset_id="person-v1",
        cat_asset_id="cat-v1",
        style_asset_ids=("storybook-pencil-v1",),
    )
    morning = compile_render_plan(
        _episode("morning"),
        plan_revision=3,
        render_revision=1,
        references=references,
        scene_keyframe_asset_ids=("approved-first-frame",),
    )
    noon = compile_render_plan(
        _episode("noon"),
        plan_revision=3,
        render_revision=1,
        references=references,
        scene_keyframe_asset_ids=(
            "approved-first-frame",
            "approved-last-frame",
        ),
    )
    evening = compile_render_plan(
        _episode("evening"),
        plan_revision=3,
        render_revision=1,
        references=references,
    )

    assert morning["durationMs"] == 10000
    assert morning["visualInputMode"] == "generated_first_frame"
    assert noon["visualInputMode"] == "generated_first_last_frames"
    assert evening["visualInputMode"] == "direct_references"


def test_keyframe_fingerprint_ignores_timing_but_not_visual_semantics() -> None:
    original = _episode("morning")
    retimed = copy.deepcopy(original)
    retimed["durationMs"] = 12000
    retimed["beats"][0]["endMs"] = 3000
    retimed["beats"][1]["startMs"] = 3000

    assert keyframe_context_fingerprint(
        original,
        frame_role="first",
    ) == keyframe_context_fingerprint(
        retimed,
        frame_role="first",
    )

    changed = copy.deepcopy(retimed)
    changed["beats"][0]["visualAction"] = "人物和猫咪改在车站等待。"
    assert keyframe_context_fingerprint(
        original,
        frame_role="first",
    ) != keyframe_context_fingerprint(
        changed,
        frame_role="first",
    )


def test_exact_ending_requires_two_reviewed_keyframes() -> None:
    episode = _episode("noon")
    references = VisualReferences(
        person_asset_id="person-v1",
        cat_asset_id="cat-v1",
        style_asset_ids=("storybook-pencil-v1",),
    )

    with pytest.raises(ContentValidationError, match="exactly 2"):
        compile_render_plan(
            episode,
            plan_revision=3,
            render_revision=1,
            references=references,
        )

    plan = compile_render_plan(
        episode,
        plan_revision=3,
        render_revision=1,
        references=references,
        scene_keyframe_asset_ids=("first-frame", "last-frame"),
    )
    assert plan["visualInputMode"] == "generated_first_last_frames"
    assert plan["visualInputReasonCodes"][0] == "exact_ending"


def test_continuation_waits_for_review_then_uses_ready_or_fallback() -> None:
    pack = load_json_object(
        ROOT / "content" / "examples" / "daily-life-pack.fishing.example.json"
    )
    episode = pack["slots"]["evening"]
    dependency_id = episode["dependsOnEpisodeIds"][0]

    assert resolve_continuation(episode, {dependency_id: "content_review"}) == "wait"
    assert resolve_continuation(episode, {dependency_id: "ready"}) == "primary"
    assert (
        resolve_continuation(episode, {dependency_id: "failed"}) == "content_fallback"
    )


class _FakeTasks:
    def __init__(self) -> None:
        self.create_kwargs: dict | None = None
        self.list_kwargs: dict | None = None

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return SimpleNamespace(id="task-real-boundary-test")

    def get(self, **kwargs):
        return SimpleNamespace(
            id=kwargs["task_id"],
            status="succeeded",
            content=SimpleNamespace(video_url="https://example.invalid/video.mp4"),
            error=None,
            model="video-model",
            duration=8,
            resolution="480p",
            ratio="9:16",
            generate_audio=True,
        )

    def list(self, **kwargs):
        self.list_kwargs = kwargs
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    id="task-reconciliation-candidate",
                    status="running",
                    content=None,
                    error=None,
                    model="video-model",
                    duration=8,
                    resolution="480p",
                    ratio="9:16",
                    generate_audio=True,
                    created_at=1_700_000_000,
                )
            ]
        )


class _FakeImages:
    def __init__(self) -> None:
        self.generate_kwargs: dict | None = None

    def generate(self, **kwargs):
        self.generate_kwargs = kwargs
        return SimpleNamespace(
            model="image-model",
            data=[SimpleNamespace(url="https://example.invalid/frame.png")],
            usage=SimpleNamespace(generated_images=1),
        )


def test_ark_adapter_maps_direct_references_without_unsupported_fields(
    tmp_path: Path,
) -> None:
    image_paths = []
    for index in range(3):
        path = tmp_path / f"{index}.png"
        Image.new("RGB", (32, 32), (index, index, index)).save(path)
        image_paths.append(path)
    tasks = _FakeTasks()
    images = _FakeImages()
    client = SimpleNamespace(
        images=images,
        content_generation=SimpleNamespace(
            tasks=tasks,
        ),
    )
    provider = ArkMediaProvider(_settings(tmp_path), client=client)

    result = provider.create_video(
        prompt="真实 Ark 映射测试",
        duration_ms=8000,
        visual_input_mode="direct_references",
        input_paths=tuple(image_paths),
    )

    assert result.task_id == "task-real-boundary-test"
    kwargs = tasks.create_kwargs
    assert kwargs is not None
    assert kwargs["generate_audio"] is True
    assert kwargs["duration"] == 8
    assert kwargs["ratio"] == "9:16"
    assert kwargs["resolution"] == "480p"
    assert [item["role"] for item in kwargs["content"][1:]] == [
        "reference_image",
        "reference_image",
        "reference_image",
    ]
    assert all(
        item["image_url"]["url"].startswith("data:image/png;base64,")
        for item in kwargs["content"][1:]
    )
    assert (
        not {
            "seed",
            "frames",
            "camera_fixed",
            "service_tier",
        }
        & kwargs.keys()
    )


def test_ark_adapter_lists_redactable_reconciliation_metadata(
    tmp_path: Path,
) -> None:
    tasks = _FakeTasks()
    provider = ArkMediaProvider(
        _settings(tmp_path),
        client=SimpleNamespace(
            images=_FakeImages(),
            content_generation=SimpleNamespace(tasks=tasks),
        ),
    )

    result = provider.list_video_tasks(model="video-model")

    assert len(result) == 1
    assert result[0].task_id == "task-reconciliation-candidate"
    assert result[0].video_url is None
    assert result[0].created_at == 1_700_000_000
    assert tasks.list_kwargs == {
        "page_num": 1,
        "page_size": 100,
        "model": "video-model",
        "timeout": 120.0,
    }


def test_agent_plan_base_url_maps_to_official_visual_endpoints() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, str(request.url)))
        if request.url.path.endswith("/images/generations"):
            return httpx.Response(
                200,
                json={
                    "model": "doubao-seedream-5.0-lite",
                    "data": [{"url": "https://example.invalid/image.png"}],
                },
            )
        return httpx.Response(200, json={"id": "task-agent-plan-path-test"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = Ark(
            api_key="test-only-key",
            base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
            http_client=http_client,
        )
        client.images.generate(
            model="doubao-seedream-5.0-lite",
            prompt="path probe",
        )
        client.content_generation.tasks.create(
            model="doubao-seedance-2.0-mini",
            content=[{"type": "text", "text": "path probe"}],
        )

    assert requests == [
        (
            "POST",
            "https://ark.cn-beijing.volces.com/api/plan/v3/images/generations",
        ),
        (
            "POST",
            "https://ark.cn-beijing.volces.com/api/plan/v3/contents/generations/tasks",
        ),
    ]


def test_download_is_content_addressed_and_atomic(tmp_path: Path) -> None:
    data = b"provider-result"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=data, request=request)
    )
    with httpx.Client(transport=transport) as client:
        landed = download_to_content_address(
            "https://provider.invalid/result.mp4",
            work_root=tmp_path / "work",
            asset_root=tmp_path / "assets",
            suffix=".mp4",
            client=client,
        )

    assert landed.path.read_bytes() == data
    assert landed.path.name == f"{landed.sha256}.mp4"
    assert list((tmp_path / "work").glob("*.part")) == []


def test_ffprobe_accepts_valid_vertical_video(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("ffmpeg/ffprobe are not on this test process PATH")
    path = tmp_path / "valid.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=480x854:d=8",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=8",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        timeout=120,
    )

    result = probe_video(
        path,
        ffprobe_path=Path(ffprobe),
        expected_duration_ms=8000,
        expected_resolution="480p",
    )

    assert result.qc_status == "passed", json.dumps(result.report, ensure_ascii=False)


def test_ffprobe_accepts_provider_aligned_480p_width(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("ffmpeg/ffprobe are not on this test process PATH")
    path = tmp_path / "provider-aligned.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=496x864:d=8",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=8",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        timeout=120,
    )

    result = probe_video(
        path,
        ffprobe_path=Path(ffprobe),
        expected_duration_ms=8000,
        expected_resolution="480p",
    )

    assert result.qc_status == "passed", json.dumps(result.report, ensure_ascii=False)
