"""参考资产选择、关键帧生成和语义审核用例。

本模块拥有“哪些图可以进入本集”和“关键帧是否可交给Seedance”的业务边界。
它不提交视频任务，也不负责视频下载或最终媒体审核。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, replace
from typing import Any

from ..domain.contracts import VideoInputMode
from ..domain.prompts import CompiledPrompt, compile_image_prompt
from ..domain.review_prompts import compile_keyframe_review_prompt
from ..domain.visual_profiles import (
    SeriesVisualProfile,
    StyleProfile,
)
from ..domain.workflow import EpisodeStatus, StepKind, StepStatus
from .errors import StepRetryRequired
from .ports import (
    AssetStore,
    GatewayError,
    MediaGenerationGateway,
    MediaProbe,
    StoredAsset,
    StoredEpisode,
    StoredStep,
    VisualReviewGateway,
    WorkflowRepository,
)


@dataclass(frozen=True, slots=True)
class ReferenceSelectionPlan:
    """图片Prompt、Ark请求和审计记录共用的精确素材顺序。"""

    semantic_keys: tuple[str, ...]
    assets: tuple[StoredAsset, ...]

    def summary(self) -> dict[str, Any]:
        return {
            "items": [
                {
                    "ordinal": index,
                    "assetId": str(asset.id),
                    "semanticKey": key,
                    "role": asset.role,
                    "sha256": asset.sha256,
                }
                for index, (key, asset) in enumerate(
                    zip(self.semantic_keys, self.assets, strict=True),
                    start=1,
                )
            ]
        }


class VisualPreparationService:
    """精确选图，并把关键帧推进到可用、待人工或明确拒绝。"""

    def __init__(
        self,
        *,
        repository: WorkflowRepository,
        media_gateway: MediaGenerationGateway,
        visual_review_gateway: VisualReviewGateway,
        asset_store: AssetStore,
        media_probe: MediaProbe,
        provider_name: str,
        keyframe_review_mode: str,
        series_profile: SeriesVisualProfile,
        style_profile: StyleProfile,
    ) -> None:
        self._repository = repository
        self._media_gateway = media_gateway
        self._review_gateway = visual_review_gateway
        self._asset_store = asset_store
        self._probe = media_probe
        self._provider_name = provider_name
        self._review_mode = str(keyframe_review_mode)
        self._series_profile = series_profile
        self._style_profile = style_profile

    def prepare(
        self,
        episode: StoredEpisode,
        *,
        allow_unverified_keyframes: bool,
        prompt_overrides: dict[str, str] | None = None,
    ) -> tuple[StoredAsset, ...] | None:
        """返回Seedance实际输入；``None``表示关键帧等待人工审核。"""

        selection = self.select_references(episode)
        if episode.plan.video_input_mode is VideoInputMode.MULTIMODAL_REFERENCE:
            return selection.assets
        if episode.status in {EpisodeStatus.PLANNED, EpisodeStatus.FAILED}:
            self._repository.set_episode_status(
                episode.id,
                EpisodeStatus.PREPARING_VISUALS,
            )
        first = self._ensure_image(
            episode,
            selection,
            target="first_frame",
            allow_unverified_keyframes=allow_unverified_keyframes,
            prompt_override=(prompt_overrides or {}).get("first_frame"),
        )
        if first.status == "candidate":
            return None
        if first.status == "rejected":
            raise RuntimeError("首帧语义审核失败，已阻断Seedance任务")
        if episode.plan.video_input_mode is VideoInputMode.STRICT_FIRST_FRAME:
            return (first,)

        # 尾帧必须读取已经通过审核的首帧，使身份、空间和物体终态建立在同一画面上。
        last_selection = ReferenceSelectionPlan(
            semantic_keys=("frame:approved-first", *selection.semantic_keys[:4]),
            assets=(first, *selection.assets[:4]),
        )
        last = self._ensure_image(
            episode,
            last_selection,
            target="last_frame",
            allow_unverified_keyframes=allow_unverified_keyframes,
            prompt_override=(prompt_overrides or {}).get("last_frame"),
        )
        if last.status == "candidate":
            return None
        if last.status == "rejected":
            raise RuntimeError("尾帧语义审核失败，已阻断Seedance任务")
        return (first, last)

    def select_references(self, episode: StoredEpisode) -> ReferenceSelectionPlan:
        """按语义键选择最新批准版本，绝不回退到同role的其他资产。"""

        first_view = (
            episode.plan.shots[0].dominant_view.value
            if episode.plan.shots
            else "front"
        )
        view = first_view if first_view in {"front", "side", "back"} else "front"
        style_context = episode.plan.style_context
        desired = (
            f"person:{view}",
            f"cat:{view}",
            self._style_profile.line_reference_key,
            (
                self._style_profile.indoor_reference_key
                if style_context == "indoor"
                else self._style_profile.outdoor_reference_key
            ),
            *episode.plan.reference_semantic_keys,
        )
        desired = tuple(dict.fromkeys(desired))
        if not 3 <= len(desired) <= 5:
            raise ValueError("Seedream每次必须选择3至5张必要参考图")
        candidates = self._repository.list_assets(
            run_id=episode.run_id,
            episode_id=episode.id,
            statuses=("approved", "ready"),
            semantic_keys=desired,
        )
        latest: dict[str, StoredAsset] = {}
        for asset in candidates:
            if asset.semantic_key in desired and not asset.semantic_key.startswith(
                "legacy:"
            ):
                latest[asset.semantic_key] = asset
        missing = [key for key in desired if key not in latest]
        if missing:
            raise ValueError(f"缺少已批准精确参考资产: {', '.join(missing)}")
        assets = tuple(latest[key] for key in desired)
        if any(asset.media_type != "image" for asset in assets):
            raise ValueError("当前Seedream参考选择只允许图片资产")
        return ReferenceSelectionPlan(desired, assets)

    def _ensure_image(
        self,
        episode: StoredEpisode,
        selection: ReferenceSelectionPlan,
        *,
        target: str,
        allow_unverified_keyframes: bool,
        prompt_override: str | None = None,
        attempt: int = 1,
        retry_of_step_id: uuid.UUID | None = None,
        retry_reason: str | None = None,
    ) -> StoredAsset:
        base_compiled = compile_image_prompt(
            episode.plan,
            target=target,
            reference_roles=selection.semantic_keys,
            style_profile=self._style_profile,
            series_profile=self._series_profile,
        )
        compiled = (
            base_compiled
            if retry_reason is None
            else compile_image_prompt(
                episode.plan,
                target=target,
                reference_roles=selection.semantic_keys,
                style_profile=self._style_profile,
                series_profile=self._series_profile,
                retry_feedback=retry_reason,
            )
        )
        has_override = prompt_override is not None and bool(prompt_override.strip())
        if has_override:
            assert prompt_override is not None
            override_text = prompt_override.strip()
            compiled = CompiledPrompt(
                text=override_text,
                char_count=len(override_text),
                utf8_bytes=len(override_text.encode("utf-8")),
                warnings=(),
            )
        base_input_hash = _input_hash(
            base_compiled.text,
            *(asset.sha256 for asset in selection.assets),
        )
        input_hash = _input_hash(
            compiled.text,
            *(asset.sha256 for asset in selection.assets),
        )
        # 编辑版按真实文本哈希复用：同一编辑重复触发命中既有帧（幂等不重复扣费），
        # 不同编辑产生新哈希自然重新生成；无编辑时按基础Prompt哈希复用。
        reusable = self._repository.find_reusable_asset(
            episode_id=episode.id,
            role=target,
            input_hash=input_hash if has_override else base_input_hash,
            statuses=("candidate", "approved", "ready"),
        )
        if reusable is not None:
            return reusable
        operation_key = f"image:{target}"
        step = self._repository.create_step_intent(
            run_id=episode.run_id,
            episode_id=episode.id,
            parent_step_id=None,
            kind=StepKind.IMAGE,
            attempt=attempt,
            provider=self._provider_name,
            model=self._media_gateway.image_model,
            input_hash=input_hash,
            request_summary={
                "operationKey": operation_key,
                "baseInputHash": base_input_hash,
                "target": target,
                "referenceSelectionPlan": selection.summary(),
                "retryOfStepId": (
                    None if retry_of_step_id is None else str(retry_of_step_id)
                ),
                "retryReason": retry_reason,
            },
        )
        self._repository.save_prompt(
            step_id=step.id,
            parent_prompt_id=None,
            purpose="image",
            model=self._media_gateway.image_model,
            text=compiled.text,
        )
        if step.status is StepStatus.SUCCEEDED:
            raise RuntimeError("关键帧步骤已成功但缺少对应资产")
        if step.status in {
            StepStatus.FAILED,
            StepStatus.EXPIRED,
            StepStatus.CANCELLED,
        }:
            raise StepRetryRequired(step.id, operation_key)
        self._repository.set_step_status(step.id, StepStatus.SUBMITTING)
        try:
            result = self._media_gateway.generate_image(
                prompt=compiled.text,
                reference_paths=tuple(asset.path for asset in selection.assets),
            )
            landed = self._asset_store.download(result.url, suffix=".png")
            metadata = self._probe.inspect_image(landed.path)
            self._validate_technical_frame(
                target=target,
                metadata=metadata,
                references=selection.assets,
            )
        except GatewayError as exc:
            self._repository.fail_step(
                step.id,
                code=exc.code,
                message=str(exc),
                submission_unknown=exc.submission_unknown,
            )
            raise
        except Exception as exc:
            self._repository.fail_step(
                step.id,
                code="keyframe_technical_qc_failed",
                message=str(exc),
            )
            raise
        asset = self._repository.save_asset(
            run_id=episode.run_id,
            episode_id=episode.id,
            step_id=step.id,
            role=target,
            semantic_key=f"frame:{episode.id}-{target.replace('_frame', '')}",
            scope="episode",
            status="candidate",
            media_type="image",
            landed=landed,
            metadata=metadata,
        )
        if self._review_mode == "manual":
            return self._await_manual(step.id, asset, metadata, "manual")
        if self._review_mode == "technical_auto":
            if not allow_unverified_keyframes:
                raise ValueError(
                    "technical_auto生成视频必须显式提供"
                    "--allow-unverified-keyframes"
                )
            self._repository.set_step_status(step.id, StepStatus.AWAITING_REVIEW)
            self._repository.commit_asset_review(
                asset_id=asset.id,
                source="technical",
                decision="approved",
                reason="技术QC通过；实验模式未执行语义审核",
                warnings=[],
                evidence={
                    **metadata,
                    "reviewMode": "technical_auto",
                    "semanticReviewStatus": "skipped",
                    "semanticVerified": False,
                    "autoApprovedUnverified": True,
                },
            )
            return replace(asset, status="approved")
        return self._semantic_review(episode, step.id, asset, metadata, target)

    def _semantic_review(
        self,
        episode: StoredEpisode,
        step_id: uuid.UUID,
        asset: StoredAsset,
        metadata: dict[str, Any],
        target: str,
    ) -> StoredAsset:
        prompt = compile_keyframe_review_prompt(
            episode.plan,
            target=target,
            series_profile=self._series_profile,
            style_profile=self._style_profile,
        )
        self._repository.save_prompt(
            step_id=step_id,
            parent_prompt_id=None,
            purpose="review",
            model=self._review_gateway.review_model,
            text=prompt,
        )
        try:
            result = self._review_gateway.review_keyframe(
                prompt=prompt,
                image_path=asset.path,
            )
        except GatewayError as exc:
            return self._await_manual(
                step_id,
                asset,
                metadata,
                "semantic_auto",
                reason=f"语义审核异常，转人工：{exc.code}",
            )
        passed = all(
            (
                result.identity_ok,
                result.style_ok,
                result.world_state_ok,
                result.scene_topology_ok,
            )
        )
        evidence = {
            **metadata,
            "reviewMode": "semantic_auto",
            "semanticReviewStatus": (
                "passed" if passed and result.confidence >= 0.8 else "failed"
            ),
            "semanticVerified": passed and result.confidence >= 0.8,
            "autoApprovedUnverified": False,
            "identityOk": result.identity_ok,
            "styleOk": result.style_ok,
            "worldStateOk": result.world_state_ok,
            "sceneTopologyOk": result.scene_topology_ok,
            "confidence": result.confidence,
            "violations": list(result.violations),
            "observations": list(result.evidence),
            "responseId": result.response_id,
            "providerRequestHash": result.request_hash,
        }
        if passed and result.confidence >= 0.8:
            self._repository.set_step_status(step_id, StepStatus.AWAITING_REVIEW)
            self._repository.commit_asset_review(
                asset_id=asset.id,
                source="ark_visual",
                decision="approved",
                reason="关键帧通过身份、二维画风、世界状态和空间拓扑审核",
                warnings=[],
                evidence=evidence,
            )
            return replace(asset, status="approved")
        if result.confidence < 0.8:
            return self._await_manual(
                step_id,
                asset,
                evidence,
                "semantic_auto",
                reason="语义审核置信度低于0.80，转人工",
            )
        self._repository.set_step_status(step_id, StepStatus.AWAITING_REVIEW)
        self._repository.commit_asset_review(
            asset_id=asset.id,
            source="ark_visual",
            decision="rejected",
            reason="关键帧存在明确身份、画风、世界状态或空间拓扑错误",
            warnings=[],
            evidence=evidence,
        )
        return replace(asset, status="rejected")

    def retry_image(
        self,
        episode: StoredEpisode,
        original_step: StoredStep,
        *,
        reason: str,
        allow_unverified_keyframes: bool,
    ) -> StoredAsset:
        """显式创建关键帧新attempt；拒绝资产和旧Prompt始终保留。"""

        if episode.status is EpisodeStatus.FAILED:
            self._repository.set_episode_status(
                episode.id,
                EpisodeStatus.PREPARING_VISUALS,
            )
            episode = self._repository.get_episode(
                episode.run_id,
                episode.plan.slot,
            )
        target = str(original_step.request_summary.get("target", ""))
        if target not in {"first_frame", "last_frame"}:
            raise ValueError("原步骤不是可重试的首帧或尾帧任务")
        selection = self.select_references(episode)
        if target == "last_frame":
            first = self._repository.list_assets(
                run_id=episode.run_id,
                episode_id=episode.id,
                roles=("first_frame",),
                statuses=("approved", "ready"),
            )
            if not first:
                raise ValueError("重试尾帧前必须存在已批准首帧")
            selection = ReferenceSelectionPlan(
                semantic_keys=("frame:approved-first", *selection.semantic_keys[:4]),
                assets=(first[-1], *selection.assets[:4]),
            )
        attempt = self._repository.next_step_attempt(
            episode_id=episode.id,
            kind=StepKind.IMAGE,
            operation_key=f"image:{target}",
        )
        return self._ensure_image(
            episode,
            selection,
            target=target,
            allow_unverified_keyframes=allow_unverified_keyframes,
            attempt=attempt,
            retry_of_step_id=original_step.id,
            retry_reason=reason,
        )

    def _await_manual(
        self,
        step_id: uuid.UUID,
        asset: StoredAsset,
        evidence: dict[str, Any],
        review_mode: str,
        *,
        reason: str = "图片技术QC通过，等待人工语义审核",
    ) -> StoredAsset:
        self._repository.set_step_status(step_id, StepStatus.AWAITING_REVIEW)
        self._repository.record_review(
            step_id=step_id,
            asset_id=asset.id,
            source="technical",
            decision="pending",
            reason=reason,
            warnings=[],
            evidence={
                **evidence,
                "reviewMode": review_mode,
                "semanticReviewStatus": "pending",
                "semanticVerified": False,
                "autoApprovedUnverified": False,
            },
        )
        return asset

    @staticmethod
    def _validate_technical_frame(
        *,
        target: str,
        metadata: dict[str, Any],
        references: tuple[StoredAsset, ...],
    ) -> None:
        width = int(metadata["width"])
        height = int(metadata["height"])
        ratio_error = abs(width / height - 9 / 16) / (9 / 16)
        if ratio_error > 0.01:
            raise ValueError("关键帧与9:16比例偏差超过1%")
        if metadata.get("blackBorderDetected") is True:
            raise ValueError("关键帧检测到明显黑边")
        if target != "last_frame":
            return
        first = next(
            (asset for asset in references if asset.role == "first_frame"),
            None,
        )
        if first is None:
            raise ValueError("尾帧必须加载已经批准的首帧")
        if (first.metadata.get("width"), first.metadata.get("height")) != (
            width,
            height,
        ):
            raise ValueError("同一Episode首尾帧尺寸必须完全一致")


def _input_hash(*values: str) -> str:
    return hashlib.sha256(
        json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
