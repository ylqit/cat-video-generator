from __future__ import annotations

import uuid
from pathlib import Path

from cat_video_generator.application.creator_worker import CreatorSnapshotExecutor
from cat_video_generator.application.ports import (
    CreativeDirectorResult,
    LandedAsset,
    VideoTaskResult,
)
from cat_video_generator.domain.rendering import AudioPolicy, RenderOperation, VideoInputPlan


def test_creator_story_worker_accepts_plain_text_without_schema_rejection() -> None:
    step_id = uuid.uuid4()
    completed: list[dict[str, object]] = []

    class Repository:
        def generation_work(self, actual_step_id: uuid.UUID) -> dict[str, object]:
            assert actual_step_id == step_id
            return {
                "kind": "story_text",
                "prompt": "写一个完整的一人一猫故事",
                "providerConfig": {"model": "director-model"},
            }

        def complete_story_candidates(
            self,
            actual_step_id: uuid.UUID,
            *,
            candidates: list[dict[str, object]],
            raw_response: object,
            provider_model: str,
            request_hash: str,
        ) -> None:
            completed.append(
                {
                    "stepId": actual_step_id,
                    "candidates": candidates,
                    "raw": raw_response,
                    "model": provider_model,
                    "requestHash": request_hash,
                }
            )

    class Gateway:
        def generate_creative_text(self, **values: object) -> CreativeDirectorResult:
            assert values == {
                "prompt": "写一个完整的一人一猫故事",
                "output_name": "StoryCandidateBatch",
                "model": "director-model",
            }
            return CreativeDirectorResult(
                payload="孩子在雨后窗边收好画纸，猫咪安静地替她压住纸角。",
                response_id="response-1",
                model="director-model",
                request_hash="a" * 64,
            )

    executor = CreatorSnapshotExecutor(
        repository=Repository(),  # type: ignore[arg-type]
        gateway=Gateway(),  # type: ignore[arg-type]
        asset_store=object(),  # type: ignore[arg-type]
        provider_poll_interval_seconds=3,
    )

    result = executor.execute(step_id)

    assert result.status == "awaiting_selection"
    assert result.payload == {"candidateCount": 1}
    assert completed[0]["candidates"] == [
        {
            "title": "未命名故事",
            "body": "孩子在雨后窗边收好画纸，猫咪安静地替她压住纸角。",
            "summary": None,
        }
    ]


def test_creator_video_worker_records_submission_then_resumes_same_provider_task(
    tmp_path: Path,
) -> None:
    step_id = uuid.uuid4()
    provider_task_ids: list[str] = []
    completed_assets: list[LandedAsset] = []
    state = {"providerTaskId": None}
    plan = VideoInputPlan(
        operation=RenderOperation.SHOT,
        resolution="720p",
        duration_seconds=8,
        audio_policy=AudioPolicy.NATIVE_REQUIRED,
        bindings=[],
    )

    class Repository:
        def generation_work(self, _step_id: uuid.UUID) -> dict[str, object]:
            return {
                "kind": "video",
                "prompt": "生成一个八秒视频",
                "inputPlan": plan,
                "inputSources": (),
                "providerTaskId": state["providerTaskId"],
                "providerConfig": {"model": "video-model"},
            }

        def record_provider_submission(
            self,
            _step_id: uuid.UUID,
            *,
            provider_task_id: str,
            provider_status: str,
        ) -> None:
            assert provider_status == "queued"
            state["providerTaskId"] = provider_task_id
            provider_task_ids.append(provider_task_id)

        def complete_media_asset(
            self,
            _step_id: uuid.UUID,
            *,
            landed: LandedAsset,
            provider_url: str,
            provider_model: str,
            last_frame_landed: LandedAsset | None,
            last_frame_provider_url: str | None,
        ) -> str:
            assert provider_url == "https://provider.test/video.mp4"
            assert provider_model == "video-model"
            assert last_frame_landed is None
            assert last_frame_provider_url is None
            completed_assets.append(landed)
            return "video-asset-1"

    class Gateway:
        def submit_video(self, **values: object) -> VideoTaskResult:
            assert values["prompt"] == "生成一个八秒视频"
            assert values["model"] == "video-model"
            return VideoTaskResult(task_id="provider-task-1", status="queued")

        def get_video_task(self, task_id: str) -> VideoTaskResult:
            assert task_id == "provider-task-1"
            return VideoTaskResult(
                task_id=task_id,
                status="succeeded",
                video_url="https://provider.test/video.mp4",
                model="video-model",
            )

    class Store:
        def download(self, url: str, *, suffix: str) -> LandedAsset:
            assert url == "https://provider.test/video.mp4"
            assert suffix == ".mp4"
            target = tmp_path / "video.mp4"
            target.write_bytes(b"video")
            return LandedAsset(target, "b" * 64, 5)

    executor = CreatorSnapshotExecutor(
        repository=Repository(),  # type: ignore[arg-type]
        gateway=Gateway(),  # type: ignore[arg-type]
        asset_store=Store(),  # type: ignore[arg-type]
        provider_poll_interval_seconds=3,
    )

    first = executor.execute(step_id)
    second = executor.execute(step_id)

    assert first.status == "provider_queued"
    assert first.payload == {
        "providerTaskId": "provider-task-1",
        "providerStatus": "queued",
    }
    assert second.status == "awaiting_selection"
    assert second.payload == {"assetId": "video-asset-1"}
    assert provider_task_ids == ["provider-task-1"]
    assert len(completed_assets) == 1
