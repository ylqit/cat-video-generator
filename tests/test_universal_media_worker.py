from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

from cat_video_generator.application.ports import (
    DirectorResult,
    ImageResult,
    LandedAsset,
    StoredAsset,
    VideoTaskResult,
)
from cat_video_generator.application.universal_media_worker import UniversalMediaWorker
from cat_video_generator.application.universal_video_edit import UniversalVideoEditExecutor
from cat_video_generator.domain.rendering import RenderOperation, VideoInputPlan
from cat_video_generator.domain.workflow import StepStatus


def test_worker_claims_only_media_canvas_jobs_and_lands_one_audited_candidate(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    lease = SimpleNamespace(
        step_id=uuid.uuid4(),
        operation_key=f"media:image:batch:{uuid.uuid4()}:candidate:1",
    )

    class Queue:
        def claim_next(self, **values: object) -> object:
            assert values["operation_prefixes"] == (
                "subject:complete:",
                "media:image:batch:",
                "media:video:batch:",
                "video:edit-anchor:",
                "video:edit-recipe:",
            )
            events.append("claimed")
            return lease

        def finish(self, _step_id: uuid.UUID, **values: object) -> None:
            events.append(f"finished:{values['status']}")

    class Repository:
        def image_candidate_work(self, _step_id: uuid.UUID) -> dict[str, object]:
            events.append("loaded_persisted_prompt")
            return {"prompt": "精确已审计 Prompt", "referencePaths": ()}

        def complete_image_candidate(self, _step_id: uuid.UUID, **values: object) -> str:
            assert values["provider_url"] == "https://provider.test/candidate.png"
            events.append("candidate_persisted")
            return "asset-1"

    class Gateway:
        def generate_image(self, **values: object) -> ImageResult:
            assert values["prompt"] == "精确已审计 Prompt"
            events.append("provider_called")
            return ImageResult("https://provider.test/candidate.png", "seedream")

    class Store:
        def download(self, _url: str, *, suffix: str) -> LandedAsset:
            assert suffix == ".png"
            events.append("downloaded")
            path = tmp_path / "candidate.png"
            path.write_bytes(b"image")
            return LandedAsset(path, "a" * 64, 5)

    worker = UniversalMediaWorker(
        queue=Queue(),  # type: ignore[arg-type]
        repository=Repository(),  # type: ignore[arg-type]
        gateway=Gateway(),  # type: ignore[arg-type]
        asset_store=Store(),  # type: ignore[arg-type]
        worker_id="media-worker-test",
    )

    result = worker.run_once()

    assert result == {"stepId": str(lease.step_id), "assetId": "asset-1"}
    assert events == [
        "claimed",
        "loaded_persisted_prompt",
        "provider_called",
        "downloaded",
        "candidate_persisted",
        f"finished:{StepStatus.AWAITING_REVIEW}",
    ]


def test_worker_submits_and_lands_audited_video_batch_candidate(tmp_path: Path) -> None:
    events: list[str] = []
    lease = SimpleNamespace(
        step_id=uuid.uuid4(),
        operation_key=f"media:video:batch:{uuid.uuid4()}:candidate:1",
    )
    plan = VideoInputPlan(
        operation=RenderOperation.SHOT,
        resolution="720p",
        duration_seconds=8,
        bindings=[],
    )

    class Queue:
        def claim_next(self, **values: object) -> object:
            assert "media:video:batch:" in values["operation_prefixes"]  # type: ignore[operator]
            return lease

        def finish(self, _step_id: uuid.UUID, **values: object) -> None:
            events.append(f"finished:{values['status']}")

    class Repository:
        def video_candidate_work(self, _step_id: uuid.UUID) -> dict[str, object]:
            events.append("loaded_persisted_prompt")
            return {
                "prompt": "让已确认主体缓慢转身",
                "inputPlan": plan,
                "inputSources": (),
                "providerTaskId": None,
            }

        def record_video_candidate_submission(
            self, _step_id: uuid.UUID, **values: object
        ) -> None:
            assert values["provider_task_id"] == "video-task-1"
            events.append("submission_persisted")

        def complete_video_candidate(self, _step_id: uuid.UUID, **values: object) -> str:
            assert values["provider_url"] == "https://provider.test/candidate.mp4"
            events.append("candidate_persisted")
            return "video-asset-1"

    class Gateway:
        def submit_video(self, **values: object) -> VideoTaskResult:
            assert values["input_plan"] == plan
            events.append("provider_called")
            return VideoTaskResult(
                task_id="video-task-1",
                status="succeeded",
                video_url="https://provider.test/candidate.mp4",
                model="seedance",
            )

        def get_video_task(self, _task_id: str) -> VideoTaskResult:
            raise AssertionError("new task must be submitted, not polled")

    class Store:
        def download(self, _url: str, *, suffix: str) -> LandedAsset:
            assert suffix == ".mp4"
            events.append("downloaded")
            path = tmp_path / "candidate.mp4"
            path.write_bytes(b"video")
            return LandedAsset(path, "e" * 64, 5)

    worker = UniversalMediaWorker(
        queue=Queue(),  # type: ignore[arg-type]
        repository=Repository(),  # type: ignore[arg-type]
        gateway=Gateway(),  # type: ignore[arg-type]
        asset_store=Store(),  # type: ignore[arg-type]
        worker_id="video-batch-worker-test",
    )

    result = worker.run_once()

    assert result == {"stepId": str(lease.step_id), "assetId": "video-asset-1"}
    assert events == [
        "loaded_persisted_prompt",
        "provider_called",
        "submission_persisted",
        "downloaded",
        "candidate_persisted",
        f"finished:{StepStatus.AWAITING_REVIEW}",
    ]


def test_worker_executes_persisted_subject_completion_and_waits_for_human_review(
    tmp_path: Path,
) -> None:
    del tmp_path
    events: list[str] = []
    lease = SimpleNamespace(
        step_id=uuid.uuid4(),
        operation_key=f"subject:complete:{uuid.uuid4()}",
    )

    class Queue:
        def claim_next(self, **values: object) -> object:
            assert "subject:complete:" in values["operation_prefixes"]
            return lease

        def finish(self, _step_id: uuid.UUID, **values: object) -> None:
            events.append(f"finished:{values['status']}")

    class Repository:
        def subject_completion_work(self, _step_id: uuid.UUID) -> dict[str, object]:
            events.append("loaded_persisted_prompt")
            return {"prompt": "补齐主体但不要覆盖原版本"}

        def complete_subject_completion(
            self, _step_id: uuid.UUID, **values: object
        ) -> str:
            assert values["proposal"]["immutableTraits"] == ["额头 M 纹不变"]  # type: ignore[index]
            events.append("proposal_persisted")
            return "completion-run-1"

    class Gateway:
        def generate_structured(self, **values: object) -> DirectorResult:
            assert values["prompt"] == "补齐主体但不要覆盖原版本"
            assert values["output_name"] == "SubjectCompletionProposal"
            events.append("provider_called")
            return DirectorResult(
                payload={
                    "identityAnchors": ["灰白虎斑猫"],
                    "immutableTraits": ["额头 M 纹不变"],
                    "relationshipNotes": "提醒小满收画",
                    "dramaticFunction": "触发并解决危机",
                    "visualRisks": ["尾巴纹路容易漂移"],
                    "rationale": {},
                    "warnings": [],
                },
                response_id="response-1",
                model="director-model",
                request_hash="request-hash",
            )

    worker = UniversalMediaWorker(
        queue=Queue(),  # type: ignore[arg-type]
        repository=Repository(),  # type: ignore[arg-type]
        gateway=Gateway(),  # type: ignore[arg-type]
        asset_store=SimpleNamespace(),  # type: ignore[arg-type]
        worker_id="subject-worker-test",
    )

    result = worker.run_once()

    assert result == {"stepId": str(lease.step_id), "subjectCompletionRunId": "completion-run-1"}
    assert events == [
        "loaded_persisted_prompt",
        "provider_called",
        "proposal_persisted",
        f"finished:{StepStatus.AWAITING_REVIEW}",
    ]


def test_control_anchor_persists_boundary_before_provider_call(tmp_path: Path) -> None:
    source = _video_asset(tmp_path)
    events: list[str] = []

    class Repository:
        def video_edit_anchor_work(self, _step_id: uuid.UUID) -> dict[str, object]:
            return {
                "source": source,
                "timestampMs": 1_500,
                "prompt": "remove the marked label",
                "referencePaths": (),
            }

        def record_control_anchor_input(self, _step_id: uuid.UUID, **_values: object) -> str:
            events.append("input_persisted")
            return "boundary"

        def complete_control_anchor(self, _step_id: uuid.UUID, **_values: object) -> str:
            events.append("anchor_persisted")
            return "anchor"

    class Gateway:
        video_model = "video-model"

        def generate_image(self, **_values: object) -> ImageResult:
            events.append("provider_called")
            return ImageResult("https://provider.test/anchor.png", "image-model")

    executor = UniversalVideoEditExecutor(
        repository=Repository(),  # type: ignore[arg-type]
        gateway=Gateway(),  # type: ignore[arg-type]
        asset_store=_EditStore(tmp_path),  # type: ignore[arg-type]
        media_probe=SimpleNamespace(),  # type: ignore[arg-type]
        frame_extractor=_Extractor(tmp_path),  # type: ignore[arg-type]
        resolution="720p",
    )

    result = executor.execute(uuid.uuid4(), operation_key="video:edit-anchor:r:start")

    assert result.status is StepStatus.SUCCEEDED
    assert result.payload == {"assetId": "anchor"}
    assert events == ["input_persisted", "provider_called", "anchor_persisted"]


def test_direct_video_edit_freezes_inputs_and_preserves_full_version(
    tmp_path: Path,
) -> None:
    source = _video_asset(tmp_path)
    events: list[str] = []

    class Repository:
        def video_edit_video_work(self, _step_id: uuid.UUID) -> dict[str, object]:
            return {
                "ready": True,
                "prompt": "replace only the selected product label",
                "source": source,
                "sourceInput": source.path,
                "anchors": (),
                "startMs": 1_000,
                "endMs": 4_000,
                "providerTaskId": None,
            }

        def record_video_edit_inputs(self, _step_id: uuid.UUID, **values: object) -> None:
            assets = values["input_assets"]
            assert len(assets) == 3  # type: ignore[arg-type]
            events.append("inputs_persisted")

        def record_video_edit_submission(self, _step_id: uuid.UUID, **_values: object) -> None:
            events.append("submission_persisted")

        def complete_video_edit(self, _step_id: uuid.UUID, **_values: object) -> dict[str, str]:
            events.append("versions_persisted")
            return {"assetId": "full", "providerSegmentAssetId": "segment"}

    class Gateway:
        video_model = "video-model"

        def submit_video(self, **_values: object) -> VideoTaskResult:
            events.append("provider_called")
            return VideoTaskResult(
                task_id="task-1",
                status="succeeded",
                video_url="https://provider.test/edit.mp4",
                model="video-model",
            )

    executor = UniversalVideoEditExecutor(
        repository=Repository(),  # type: ignore[arg-type]
        gateway=Gateway(),  # type: ignore[arg-type]
        asset_store=_EditStore(tmp_path),  # type: ignore[arg-type]
        media_probe=_Probe(),  # type: ignore[arg-type]
        frame_extractor=_Extractor(tmp_path),  # type: ignore[arg-type]
        resolution="720p",
    )

    result = executor.execute(uuid.uuid4(), operation_key="video:edit-recipe:r")

    assert result.status is StepStatus.AWAITING_REVIEW
    assert result.payload == {"assetId": "full", "providerSegmentAssetId": "segment"}
    assert events == [
        "inputs_persisted",
        "provider_called",
        "submission_persisted",
        "versions_persisted",
    ]


def _video_asset(tmp_path: Path) -> StoredAsset:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"source video")
    return StoredAsset(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        scene_id=None,
        shot_card_id=None,
        step_id=None,
        role="video",
        media_type="video",
        scope="canvas_node",
        status="ready",
        path=path,
        sha256="a" * 64,
        metadata={"qc": {"durationMs": 10_000}},
        semantic_key="video:source",
    )


class _Extractor:
    def __init__(self, root: Path) -> None:
        self.root = root

    def extract_frames_at(
        self,
        _source: StoredAsset,
        *,
        timestamps_ms: tuple[int, ...],
    ) -> tuple[Path, ...]:
        paths = []
        for index, _timestamp in enumerate(timestamps_ms):
            path = self.root / f"frame-{uuid.uuid4()}-{index}.png"
            path.write_bytes(b"frame")
            paths.append(path)
        return tuple(paths)


class _EditStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def import_local(self, path: Path) -> LandedAsset:
        target = self.root / f"landed-{uuid.uuid4()}{path.suffix}"
        shutil.copyfile(path, target)
        return LandedAsset(target, "b" * 64, target.stat().st_size)

    def download(self, _url: str, *, suffix: str) -> LandedAsset:
        path = self.root / f"download-{uuid.uuid4()}{suffix}"
        path.write_bytes(b"provider result")
        return LandedAsset(path, "c" * 64, path.stat().st_size)

    def render_range_replacement(self, **_values: object) -> LandedAsset:
        path = self.root / f"composed-{uuid.uuid4()}.mp4"
        path.write_bytes(b"non-destructive full version")
        return LandedAsset(path, "d" * 64, path.stat().st_size)


class _Probe:
    def __init__(self) -> None:
        self.calls = 0

    def inspect_video(self, _path: Path, **_values: object) -> dict[str, object]:
        self.calls += 1
        return {
            "passed": True,
            "durationMs": 4_000 if self.calls == 1 else 10_000,
        }
