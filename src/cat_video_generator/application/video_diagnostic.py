"""最终视频的非阻断语义诊断。

诊断只为人工内容审核提供按时间排序的证据，不修改视频资产状态，也不会把技术
QC冒充语义批准。模型失败或低置信时保留 candidate 视频并记录 pending。
"""

from __future__ import annotations

import hashlib
from typing import Any

from ..domain.review_prompts import compile_video_diagnostic_prompt
from .ports import (
    GatewayError,
    MediaFinalizer,
    StoredAsset,
    StoredEpisode,
    VisualReviewGateway,
    WorkflowRepository,
)


class VideoDiagnosticService:
    """抽帧、调用 Ark 视觉模型并持久化不含 Base64 的诊断证据。"""

    def __init__(
        self,
        *,
        repository: WorkflowRepository,
        review_gateway: VisualReviewGateway,
        media_finalizer: MediaFinalizer,
        mode: str,
        frame_count: int = 8,
    ) -> None:
        if mode not in {"off", "diagnostic"}:
            raise ValueError("视频语义审核模式必须是off或diagnostic")
        self._repository = repository
        self._gateway = review_gateway
        self._finalizer = media_finalizer
        self._mode = mode
        self._frame_count = frame_count

    def diagnose(
        self,
        episode: StoredEpisode,
        asset: StoredAsset,
    ) -> dict[str, Any]:
        """运行一次诊断；无论结果如何，最终视频仍停在 content_review。"""

        if self._mode == "off":
            return {
                "semanticReviewStatus": "skipped",
                "semanticVerified": False,
            }
        if asset.step_id is None:
            raise ValueError("视频语义诊断需要可审计的 producing step")
        prompt = compile_video_diagnostic_prompt(episode.plan)
        self._repository.save_prompt(
            step_id=asset.step_id,
            parent_prompt_id=None,
            purpose="review",
            model=self._gateway.review_model,
            text=prompt,
        )
        frames = ()
        frame_hashes: list[str] = []
        try:
            frames = self._finalizer.extract_review_frames(
                asset,
                count=self._frame_count,
            )
            frame_hashes = [
                hashlib.sha256(path.read_bytes()).hexdigest() for path in frames
            ]
            result = self._gateway.diagnose_video_frames(
                prompt=prompt,
                frame_paths=frames,
            )
        except (GatewayError, OSError, RuntimeError, ValueError) as exc:
            evidence = {
                "semanticReviewStatus": "pending",
                "semanticVerified": False,
                "orderedFrameSha256": frame_hashes,
                "diagnosticError": getattr(exc, "code", type(exc).__name__),
            }
            self._repository.record_review(
                step_id=asset.step_id,
                asset_id=asset.id,
                source="technical",
                decision="pending",
                reason="视频语义诊断异常或不可判定；保留人工内容审核",
                warnings=[],
                evidence=evidence,
            )
            return evidence
        finally:
            for frame in frames:
                frame.unlink(missing_ok=True)

        passed = all(
            (
                result.identity_ok,
                result.style_ok,
                result.world_continuity_ok,
                result.narrative_order_ok,
            )
        )
        status = (
            "passed"
            if passed and result.confidence >= 0.8
            else "failed"
            if result.confidence >= 0.8
            else "pending"
        )
        evidence = {
            "semanticReviewStatus": status,
            "semanticVerified": status == "passed",
            "identityOk": result.identity_ok,
            "styleOk": result.style_ok,
            "worldContinuityOk": result.world_continuity_ok,
            "narrativeOrderOk": result.narrative_order_ok,
            "confidence": result.confidence,
            "violations": list(result.violations),
            "observations": list(result.evidence),
            "orderedFrameSha256": frame_hashes,
            "responseId": result.response_id,
            "providerRequestHash": result.request_hash,
        }
        self._repository.record_review(
            step_id=asset.step_id,
            asset_id=asset.id,
            source="ark_visual",
            decision=(
                "approved"
                if status == "passed"
                else "rejected"
                if status == "failed"
                else "pending"
            ),
            reason=(
                "视频抽帧身份、二维画风、可见世界连续性和动作顺序诊断完成；"
                "最终决定仍由人工内容审核作出"
            ),
            warnings=[],
            evidence=evidence,
        )
        return evidence
