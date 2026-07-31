"""Seedance视频下载、技术QC与候选审核落库。

本模块拥有供应商结果落地后的完整媒体生命周期；不提交或轮询Ark任务。
"""

from __future__ import annotations

from typing import Any

from ..domain.contracts import SegmentPlan
from ..domain.workflow import EpisodeStatus, StepStatus
from .ports import (
    AssetStore,
    MediaProbe,
    StoredEpisode,
    StoredStep,
    WorkflowRepository,
)
from .video_diagnostic import VideoDiagnosticService


class VideoAssetLandingService:
    """把临时下载地址变为不可变本地资产，并记录真实QC结论。"""

    def __init__(
        self,
        *,
        repository: WorkflowRepository,
        asset_store: AssetStore,
        media_probe: MediaProbe,
        resolution: str,
        video_diagnostic: VideoDiagnosticService | None,
    ) -> None:
        self._repository = repository
        self._asset_store = asset_store
        self._probe = media_probe
        self._resolution = resolution
        self._video_diagnostic = video_diagnostic

    def land_segment(
        self,
        episode: StoredEpisode,
        step: StoredStep,
        segment: SegmentPlan,
        video_url: str,
    ) -> dict[str, Any]:
        """落地一个multi_clip片段；片段审核通过前不得生成下一段或拼接。"""

        landed = self._asset_store.download(video_url, suffix=".mp4")
        qc = self._probe.inspect_video(
            landed.path,
            expected_duration_seconds=segment.duration_seconds,
            expected_resolution=self._resolution,
            minimum_duration_seconds=4,
            maximum_duration_seconds=segment.duration_seconds + 1,
            duration_tolerance_ms=400,
        )
        if not qc["passed"]:
            self._repository.fail_step(
                step.id,
                code="segment_media_qc_failed",
                message=";".join(qc["failures"]),
            )
            self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            raise RuntimeError(f"视频片段技术QC失败: {qc['failures']}")
        asset = self._repository.save_asset(
            run_id=episode.run_id,
            episode_id=episode.id,
            step_id=step.id,
            role="video_segment",
            semantic_key=segment_semantic_key(episode, segment),
            scope="episode",
            status="candidate",
            media_type="video",
            landed=landed,
            metadata={**qc, "segmentOrder": segment.order},
        )
        self._mark_awaiting_review(step)
        self._repository.record_review(
            step_id=step.id,
            asset_id=asset.id,
            source="technical",
            decision="pending",
            reason="片段技术QC通过；必须人工确认语义与切点后才能生成下一段或拼接",
            warnings=[],
            evidence={
                **qc,
                "segmentOrder": segment.order,
                "semanticReviewStatus": "pending",
                "semanticVerified": False,
            },
        )
        if self._video_diagnostic is not None:
            self._video_diagnostic.diagnose(episode, asset)
        return {
            "episodeId": str(episode.id),
            "slot": episode.plan.slot.value,
            "status": "segment_review",
            "segmentOrder": segment.order,
            "assetId": str(asset.id),
            "localPath": str(asset.path),
        }

    def land_video(
        self,
        episode: StoredEpisode,
        step: StoredStep,
        video_url: str,
    ) -> dict[str, Any]:
        """落地完整视频；技术合格只代表可播放，最终内容仍等待人工审核。"""

        landed = self._asset_store.download(video_url, suffix=".mp4")
        self._repository.set_episode_status(episode.id, EpisodeStatus.MEDIA_QC)
        qc = self._probe.inspect_video(
            landed.path,
            expected_duration_seconds=episode.plan.duration_seconds,
            expected_resolution=self._resolution,
        )
        if not qc["passed"]:
            self._repository.fail_step(
                step.id,
                code="media_qc_failed",
                message=";".join(qc["failures"]),
            )
            self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            raise RuntimeError(f"视频技术QC失败: {qc['failures']}")
        asset = self._repository.save_asset(
            run_id=episode.run_id,
            episode_id=episode.id,
            step_id=step.id,
            role="video",
            semantic_key=f"video:{episode.id}-single-pass",
            scope="episode",
            status="candidate",
            media_type="video",
            landed=landed,
            metadata=qc,
        )
        self._mark_awaiting_review(step)
        self._repository.set_episode_status(
            episode.id,
            EpisodeStatus.CONTENT_REVIEW,
        )
        self._repository.record_review(
            step_id=step.id,
            asset_id=asset.id,
            source="technical",
            decision="pending",
            reason="视频通过容器、轨道、分辨率和时长检查；等待人工内容审核",
            warnings=[],
            evidence={
                **qc,
                "semanticReviewStatus": "diagnostic_pending",
                "semanticVerified": False,
            },
        )
        return {
            "episodeId": str(episode.id),
            "slot": episode.plan.slot.value,
            "status": EpisodeStatus.CONTENT_REVIEW.value,
            "assetId": str(asset.id),
            "localPath": str(asset.path),
        }

    def _mark_awaiting_review(self, step: StoredStep) -> None:
        current_step = self._repository.get_step(step.id)
        if current_step.status is StepStatus.QUEUED:
            self._repository.set_step_status(step.id, StepStatus.RUNNING)
        self._repository.set_step_status(step.id, StepStatus.AWAITING_REVIEW)


def segment_semantic_key(
    episode: StoredEpisode,
    segment: SegmentPlan,
) -> str:
    """返回片段在同一Episode内稳定且不与完整成片混淆的语义键。"""

    return f"video_segment:{episode.id}-{segment.order}"
