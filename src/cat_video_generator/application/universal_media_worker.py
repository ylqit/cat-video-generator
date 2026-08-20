"""Durable worker dispatch for universal media canvas jobs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..domain.workflow import StepStatus
from .ports import GatewayError, ImageResult, LandedAsset


class MediaCanvasQueue(Protocol):
    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        operation_prefixes: tuple[str, ...] = (),
    ) -> Any | None: ...

    def finish(
        self,
        step_id: uuid.UUID,
        *,
        worker_id: str,
        status: StepStatus,
        error: dict[str, object] | None = None,
    ) -> None: ...


class MediaCanvasWorkRepository(Protocol):
    def image_candidate_work(self, step_id: uuid.UUID) -> dict[str, object]: ...

    def complete_image_candidate(
        self,
        step_id: uuid.UUID,
        *,
        landed: LandedAsset,
        provider_url: str,
        provider_model: str,
    ) -> str: ...


class ImageGateway(Protocol):
    def generate_image(self, *, prompt: str, reference_paths: tuple[Path, ...]) -> ImageResult: ...


class DownloadStore(Protocol):
    def download(self, url: str, *, suffix: str) -> LandedAsset: ...


class VideoEditExecutor(Protocol):
    def execute(
        self,
        step_id: uuid.UUID,
        *,
        operation_key: str,
    ) -> MediaExecutionResult: ...


@dataclass(frozen=True, slots=True)
class MediaExecutionResult:
    payload: dict[str, str]
    status: StepStatus


class UniversalMediaWorker:
    """Executes only media-canvas operation prefixes from the shared durable queue."""

    def __init__(
        self,
        *,
        queue: MediaCanvasQueue,
        repository: MediaCanvasWorkRepository,
        gateway: ImageGateway,
        asset_store: DownloadStore,
        worker_id: str,
        video_edit_executor: VideoEditExecutor | None = None,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id cannot be empty")
        self._queue = queue
        self._repository = repository
        self._gateway = gateway
        self._asset_store = asset_store
        self._worker_id = worker_id.strip()
        self._video_edit_executor = video_edit_executor

    def run_once(self) -> dict[str, str] | None:
        lease = self._queue.claim_next(
            worker_id=self._worker_id,
            operation_prefixes=(
                "media:image:batch:",
                "video:edit-anchor:",
                "video:edit-recipe:",
            ),
        )
        if lease is None:
            return None
        try:
            if lease.operation_key.startswith("media:image:batch:"):
                execution = MediaExecutionResult(
                    payload=self._generate_image_candidate(lease.step_id),
                    status=StepStatus.AWAITING_REVIEW,
                )
            elif self._video_edit_executor is not None:
                execution = self._video_edit_executor.execute(
                    lease.step_id,
                    operation_key=lease.operation_key,
                )
            else:
                raise RuntimeError("VIDEO_EDIT_V2 worker executor is not configured")
        except GatewayError as exc:
            status = StepStatus.SUBMISSION_UNKNOWN if exc.submission_unknown else StepStatus.FAILED
            self._queue.finish(
                lease.step_id,
                worker_id=self._worker_id,
                status=status,
                error={"code": exc.code, "message": str(exc)},
            )
            raise
        except Exception as exc:
            self._queue.finish(
                lease.step_id,
                worker_id=self._worker_id,
                status=StepStatus.FAILED,
                error={"code": "media_worker_failed", "message": str(exc)},
            )
            raise
        self._queue.finish(
            lease.step_id,
            worker_id=self._worker_id,
            status=execution.status,
        )
        return {"stepId": str(lease.step_id), **execution.payload}

    def _generate_image_candidate(self, step_id: uuid.UUID) -> dict[str, str]:
        work = self._repository.image_candidate_work(step_id)
        result = self._gateway.generate_image(
            prompt=str(work["prompt"]),
            reference_paths=tuple(work["referencePaths"]),  # type: ignore[arg-type]
        )
        landed = self._asset_store.download(result.url, suffix=".png")
        asset_id = self._repository.complete_image_candidate(
            step_id,
            landed=landed,
            provider_url=result.url,
            provider_model=result.model,
        )
        return {"assetId": asset_id}
