"""Provider execution for immutable Creator snapshots."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from ..domain.creator_core import CreativeTextCandidate
from .ports import CreativeDirectorResult, GatewayError, ImageResult, LandedAsset, VideoTaskResult


class CreatorGenerationRepository(Protocol):
    def generation_work(self, task_id: uuid.UUID) -> dict[str, object]: ...
    def complete_story_candidates(
        self,
        task_id: uuid.UUID,
        *,
        candidates: list[dict[str, object]],
        raw_response: object,
        provider_model: str,
        request_hash: str,
    ) -> None: ...
    def record_provider_submission(
        self,
        task_id: uuid.UUID,
        *,
        provider_task_id: str,
        provider_status: str,
    ) -> None: ...
    def complete_media_asset(
        self,
        task_id: uuid.UUID,
        *,
        landed: LandedAsset,
        provider_url: str,
        provider_model: str,
        last_frame_landed: LandedAsset | None,
        last_frame_provider_url: str | None,
    ) -> str: ...


class CreatorGateway(Protocol):
    def generate_creative_text(
        self, *, prompt: str, output_name: str, model: str
    ) -> CreativeDirectorResult: ...
    def generate_image(
        self, *, prompt: str, reference_paths: tuple[Path, ...], model: str
    ) -> ImageResult: ...
    def submit_video(
        self,
        *,
        prompt: str,
        input_plan: object,
        input_sources: tuple[Path | str, ...],
        model: str,
    ) -> VideoTaskResult: ...
    def get_video_task(self, task_id: str) -> VideoTaskResult: ...


class CreatorAssetStore(Protocol):
    def download(self, url: str, *, suffix: str) -> LandedAsset: ...


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    status: str
    payload: dict[str, Any]
    next_attempt_at: datetime | None = None


class CreatorSnapshotExecutor:
    """Execute exactly the Provider-visible input stored in one snapshot."""

    def __init__(
        self,
        *,
        repository: CreatorGenerationRepository,
        gateway: CreatorGateway,
        asset_store: CreatorAssetStore,
        provider_poll_interval_seconds: float,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._asset_store = asset_store
        self._provider_poll_interval_seconds = provider_poll_interval_seconds

    def execute(self, task_id: uuid.UUID) -> ExecutionResult:
        work = self._repository.generation_work(task_id)
        kind = str(work["kind"])
        if kind == "story_text":
            return self._generate_story(task_id, work)
        if kind == "image":
            return self._generate_image(task_id, work)
        if kind in {"video", "video_edit"}:
            return self._generate_video(task_id, work)
        raise ValueError(f"Creator 任务类型 {kind} 尚未接入 Provider 执行")

    def _generate_story(self, task_id: uuid.UUID, work: dict[str, object]) -> ExecutionResult:
        result = self._gateway.generate_creative_text(
            prompt=str(work["prompt"]),
            output_name="StoryCandidateBatch",
            model=_provider_model(work),
        )
        candidates = _creative_candidates(result.payload)
        self._repository.complete_story_candidates(
            task_id,
            candidates=candidates,
            raw_response=result.payload,
            provider_model=result.model,
            request_hash=result.request_hash,
        )
        return ExecutionResult(
            status="awaiting_selection", payload={"candidateCount": len(candidates)}
        )

    def _generate_image(self, task_id: uuid.UUID, work: dict[str, object]) -> ExecutionResult:
        result = self._gateway.generate_image(
            prompt=str(work["prompt"]),
            reference_paths=tuple(work.get("inputSources") or ()),  # type: ignore[arg-type]
            model=_provider_model(work),
        )
        landed = self._asset_store.download(result.url, suffix=".png")
        asset_id = self._repository.complete_media_asset(
            task_id,
            landed=landed,
            provider_url=result.url,
            provider_model=result.model,
            last_frame_landed=None,
            last_frame_provider_url=None,
        )
        return ExecutionResult(status="awaiting_selection", payload={"assetId": asset_id})

    def _generate_video(self, task_id: uuid.UUID, work: dict[str, object]) -> ExecutionResult:
        provider_task_id = work.get("providerTaskId")
        if provider_task_id:
            result = self._gateway.get_video_task(str(provider_task_id))
        else:
            result = self._gateway.submit_video(
                prompt=str(work["prompt"]),
                input_plan=work["inputPlan"],
                input_sources=tuple(work.get("inputSources") or ()),  # type: ignore[arg-type]
                model=_provider_model(work),
            )
            self._repository.record_provider_submission(
                task_id,
                provider_task_id=result.task_id,
                provider_status=result.status,
            )
        if result.status in {"pending", "queued", "running"}:
            return ExecutionResult(
                status=("provider_running" if result.status == "running" else "provider_queued"),
                payload={
                    "providerTaskId": result.task_id,
                    "providerStatus": result.status,
                },
                next_attempt_at=datetime.now(UTC)
                + timedelta(seconds=self._provider_poll_interval_seconds),
            )
        if result.status != "succeeded" or not result.video_url:
            raise GatewayError(
                result.error_message or "video generation failed",
                code=result.error_code or "video_generation_failed",
                retryable=False,
            )
        landed = self._asset_store.download(result.video_url, suffix=".mp4")
        last_frame_landed = (
            self._asset_store.download(result.last_frame_url, suffix=".png")
            if result.last_frame_url
            else None
        )
        asset_id = self._repository.complete_media_asset(
            task_id,
            landed=landed,
            provider_url=result.video_url,
            provider_model=result.model or "unknown",
            last_frame_landed=last_frame_landed,
            last_frame_provider_url=result.last_frame_url,
        )
        return ExecutionResult(status="awaiting_selection", payload={"assetId": asset_id})


def _creative_candidates(payload: dict[str, Any] | str) -> list[dict[str, object]]:
    if isinstance(payload, str):
        body = payload.strip()
        if not body:
            raise ValueError("故事模型返回空文本")
        raw_candidates: object = [{"title": "未命名故事", "body": body}]
    else:
        raw_candidates = payload.get("candidates")
        if raw_candidates is None and payload.get("body"):
            raw_candidates = [payload]
    if not isinstance(raw_candidates, list):
        raise ValueError("故事模型没有返回可编辑正文")
    candidates: list[dict[str, object]] = []
    for raw in raw_candidates[:5]:
        if not isinstance(raw, dict):
            continue
        body = str(raw.get("body") or raw.get("text") or "").strip()
        if not body:
            continue
        candidate = CreativeTextCandidate.model_validate(
            {
                "title": str(raw.get("title") or "未命名故事").strip(),
                "body": body,
                "summary": raw.get("summary"),
            }
        )
        candidates.append(candidate.model_dump(mode="json"))
    if not candidates:
        raise ValueError("故事模型返回内容不可编辑")
    return candidates


def _provider_model(work: dict[str, object]) -> str:
    config = work.get("providerConfig")
    if not isinstance(config, dict):
        raise ValueError("Creator 任务缺少冻结 Provider 配置")
    model = str(config.get("model") or "").strip()
    if not model:
        raise ValueError("Creator 任务缺少冻结 Provider 模型")
    return model
