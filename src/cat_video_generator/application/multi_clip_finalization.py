"""显式多片段的尾帧衔接与条件式后期。

该模块不调用 Ark，也不决定何时生成片段。它只在片段已经人工批准后提取连续
空间尾帧，并在两个片段齐备后选择最小 FFmpeg 策略、执行整片 QC 和落盘。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from ..domain.workflow import EpisodeStatus, StepKind, StepStatus
from .errors import StepRetryRequired
from .ports import (
    AssetStore,
    MediaFinalizer,
    MediaProbe,
    StoredAsset,
    StoredEpisode,
    StoredStep,
    WorkflowRepository,
)
from .video_diagnostic import VideoDiagnosticService


class MultiClipFinalization:
    """管理已批准片段到最终候选视频之间的可恢复本地生命周期。"""

    def __init__(
        self,
        *,
        repository: WorkflowRepository,
        asset_store: AssetStore,
        media_probe: MediaProbe,
        media_finalizer: MediaFinalizer,
        resolution: str,
        video_diagnostic: VideoDiagnosticService | None,
    ) -> None:
        self._repository = repository
        self._asset_store = asset_store
        self._probe = media_probe
        self._finalizer = media_finalizer
        self._resolution = resolution
        self._video_diagnostic = video_diagnostic

    def ensure_tail_frame(
        self,
        episode: StoredEpisode,
        source: StoredAsset,
    ) -> StoredAsset:
        """从已审核的第一段提取尾帧，并通过9:16技术硬门后不可变落盘。"""

        semantic_key = f"frame:{episode.id}-segment-1-tail"
        existing = self._repository.list_assets(
            episode_id=episode.id,
            statuses=("approved", "ready"),
            semantic_keys=(semantic_key,),
        )
        if existing:
            return existing[0]
        extracted = self._finalizer.extract_last_frame(source)
        try:
            landed = self._asset_store.import_local(extracted)
        finally:
            extracted.unlink(missing_ok=True)
        metadata = self._probe.inspect_image(landed.path)
        ratio_error = abs(float(metadata["ratio"]) - 9 / 16) / (9 / 16)
        if ratio_error > 0.01 or metadata.get("blackBorderDetected") is True:
            raise ValueError("第一段真实尾帧未通过9:16或黑边技术硬门")
        asset = self._repository.save_asset(
            run_id=episode.run_id,
            episode_id=episode.id,
            step_id=source.step_id,
            role="first_frame",
            semantic_key=semantic_key,
            scope="episode",
            status="approved",
            media_type="image",
            landed=landed,
            metadata={**metadata, "derivedFromAssetId": str(source.id)},
        )
        assert source.step_id is not None
        self._repository.record_review(
            step_id=source.step_id,
            asset_id=asset.id,
            source="technical",
            decision="approved",
            reason="从已人工批准的第一段提取真实尾帧，作为连续空间第二段首帧",
            warnings=[],
            evidence={
                **metadata,
                "semanticReviewStatus": "inherited_from_approved_segment",
                "semanticVerified": True,
            },
        )
        return asset

    def finalize(
        self,
        episode: StoredEpisode,
        parts: tuple[StoredAsset, StoredAsset],
        *,
        attempt: int = 1,
        retry_of_step_id: str | None = None,
        retry_reason: str | None = None,
    ) -> dict[str, Any]:
        """拼接两个已批准片段；中断后只重做本地后期，不重提 Ark 任务。"""

        input_hash = _input_hash("multi_clip_concat", *(item.sha256 for item in parts))
        operation_key = "qc:multi_clip_concat"
        step = self._repository.create_step_intent(
            run_id=episode.run_id,
            episode_id=episode.id,
            parent_step_id=None,
            kind=StepKind.QC,
            attempt=attempt,
            provider=None,
            model=None,
            input_hash=input_hash,
            request_summary={
                "operationKey": operation_key,
                "generationStrategy": "multi_clip",
                "segmentAssetIds": [str(item.id) for item in parts],
                "retryOfStepId": retry_of_step_id,
                "retryReason": retry_reason,
            },
        )
        if step.status is StepStatus.SUCCEEDED:
            existing = self._repository.list_assets(
                episode_id=episode.id,
                roles=("video",),
                statuses=("candidate", "ready"),
                semantic_keys=(f"video:{episode.id}-multi-clip",),
            )
            if existing:
                return _final_result(episode, existing[0])
        if step.status in {
            StepStatus.FAILED,
            StepStatus.EXPIRED,
            StepStatus.CANCELLED,
        }:
            raise StepRetryRequired(step.id, operation_key)
        if step.status not in {StepStatus.PENDING, StepStatus.RUNNING}:
            raise ValueError(f"多片段后期步骤状态{step.status.value}不可自动重做")
        if step.status is StepStatus.PENDING:
            self._repository.set_step_status(step.id, StepStatus.RUNNING)
        try:
            finalized = self._finalizer.concat(
                parts,
                target_duration_seconds=episode.plan.duration_seconds,
            )
        except Exception as exc:
            self._repository.fail_step(
                step.id,
                code="concat_failed",
                message=str(exc),
            )
            raise
        try:
            landed = self._asset_store.import_local(finalized.path)
        finally:
            finalized.path.unlink(missing_ok=True)
        self._repository.set_episode_status(episode.id, EpisodeStatus.MEDIA_QC)
        qc = self._probe.inspect_video(
            landed.path,
            expected_duration_seconds=episode.plan.duration_seconds,
            expected_resolution=self._resolution,
        )
        if not qc["passed"]:
            self._repository.fail_step(
                step.id,
                code="concat_media_qc_failed",
                message=";".join(qc["failures"]),
            )
            self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            raise RuntimeError(f"多片段成片技术QC失败: {qc['failures']}")
        asset = self._repository.save_asset(
            run_id=episode.run_id,
            episode_id=episode.id,
            step_id=step.id,
            role="video",
            semantic_key=f"video:{episode.id}-multi-clip",
            scope="episode",
            status="candidate",
            media_type="video",
            landed=landed,
            metadata={
                **qc,
                "finalizationPolicy": finalized.policy,
                "durationTrimmed": finalized.duration_trimmed,
                "segmentAssetIds": [str(item.id) for item in parts],
            },
        )
        self._repository.set_step_status(step.id, StepStatus.AWAITING_REVIEW)
        self._repository.set_episode_status(episode.id, EpisodeStatus.CONTENT_REVIEW)
        self._repository.record_review(
            step_id=step.id,
            asset_id=asset.id,
            source="technical",
            decision="pending",
            reason="两个已批准片段完成条件式后期和整片技术QC；等待人工内容审核",
            warnings=[],
            evidence={
                **qc,
                "finalizationPolicy": finalized.policy,
                "durationTrimmed": finalized.duration_trimmed,
                "semanticReviewStatus": "diagnostic_pending",
                "semanticVerified": False,
            },
        )
        if self._video_diagnostic is not None:
            self._video_diagnostic.diagnose(episode, asset)
        return _final_result(episode, asset)

    def retry_finalize(
        self,
        episode: StoredEpisode,
        original_step: StoredStep,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """仅重做本地拼接/QC，不再次调用Ark。"""

        step_id = original_step.id
        summary = original_step.request_summary
        parts = tuple(
            self._repository.asset_detail(uuid.UUID(str(value)))
            for value in summary.get("segmentAssetIds", [])
        )
        if len(parts) != 2:
            raise ValueError("原后期步骤没有两个可复用的片段资产")
        attempt = self._repository.next_step_attempt(
            episode_id=episode.id,
            kind=StepKind.QC,
            operation_key="qc:multi_clip_concat",
        )
        if episode.status is EpisodeStatus.FAILED:
            self._repository.set_episode_status(
                episode.id,
                EpisodeStatus.VIDEO_PENDING,
            )
            self._repository.set_episode_status(
                episode.id,
                EpisodeStatus.VIDEO_GENERATING,
            )
            episode = self._repository.get_episode(
                episode.run_id,
                episode.plan.slot,
            )
        return self.finalize(
            episode,
            (parts[0], parts[1]),
            attempt=attempt,
            retry_of_step_id=str(step_id),
            retry_reason=reason,
        )


def _input_hash(*values: str) -> str:
    return hashlib.sha256(
        json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _final_result(
    episode: StoredEpisode,
    asset: StoredAsset,
) -> dict[str, Any]:
    return {
        "episodeId": str(episode.id),
        "slot": episode.plan.slot.value,
        "status": EpisodeStatus.CONTENT_REVIEW.value,
        "assetId": str(asset.id),
        "localPath": str(asset.path),
    }
