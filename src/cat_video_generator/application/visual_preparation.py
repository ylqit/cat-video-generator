"""故事板参考选择、Seedream组图和整组语义审核用例。

每个Episode只有一个 ``image:storyboard`` 收费步骤。Canon与元素图只用于生成
故事板；Seedance只接收审核通过的有序面板。本服务不提交视频任务。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, replace
from typing import Any

from ..domain.prompts import (
    CompiledPrompt,
    compile_storyboard_prompt,
    compile_storyboard_review_prompt,
    storyboard_panel_count,
)
from ..domain.rendering import storyboard_reference_keys
from ..domain.snapshots import ImageInputSnapshot
from ..domain.visual_profiles import SeriesVisualProfile, StyleProfile
from ..domain.workflow import EpisodeStatus, PromptPurpose, StepKind, StepStatus
from .errors import StepRetryRequired
from .ports import (
    AssetStore,
    GatewayError,
    MediaGenerationGateway,
    MediaProbe,
    ProductionStore,
    StoredAsset,
    StoredEpisode,
    StoredStep,
    VisualReviewGateway,
)


@dataclass(frozen=True, slots=True)
class ReferenceSelectionPlan:
    """故事板Prompt、Ark请求和审计快照共用的精确Canon顺序。"""

    semantic_keys: tuple[str, ...]
    assets: tuple[StoredAsset, ...]


class VisualPreparationService:
    """精确选图，并把整组故事板推进到可用、待人工或明确拒绝。"""

    def __init__(
        self,
        *,
        repository: ProductionStore,
        media_gateway: MediaGenerationGateway,
        visual_review_gateway: VisualReviewGateway,
        asset_store: AssetStore,
        media_probe: MediaProbe,
        provider_name: str,
        storyboard_review_mode: str,
        series_profile: SeriesVisualProfile,
        style_profile: StyleProfile,
    ) -> None:
        self._repository = repository
        self._media_gateway = media_gateway
        self._review_gateway = visual_review_gateway
        self._asset_store = asset_store
        self._probe = media_probe
        self._provider_name = provider_name
        self._review_mode = str(storyboard_review_mode)
        if self._review_mode not in {"semantic_auto", "manual"}:
            raise ValueError("故事板审核只允许semantic_auto或manual")
        self._series_profile = series_profile
        self._style_profile = style_profile

    def prepare(
        self,
        episode: StoredEpisode,
        *,
        prompt_overrides: dict[str, str] | None = None,
    ) -> tuple[StoredAsset, ...] | None:
        """返回按面板序号排序的已批准故事板；``None``表示等待人工审核。"""

        assets = self._ensure_storyboard(
            episode,
            self.select_references(episode),
            prompt_override=(prompt_overrides or {}).get("storyboard"),
        )
        if any(item.status == "rejected" for item in assets):
            raise RuntimeError("故事板语义审核失败，已阻断Seedance任务")
        if not all(item.status in {"approved", "ready"} for item in assets):
            return None
        return assets

    def select_references(self, episode: StoredEpisode) -> ReferenceSelectionPlan:
        """选择固定Canon与最多一张用户显式上传的Episode参考图。"""

        approved = self._repository.list_assets(
            run_id=episode.run_id,
            episode_id=episode.id,
            statuses=("approved", "ready"),
        )
        explicit_episode_assets = tuple(
            item
            for item in approved
            if item.episode_id == episode.id
            and item.scope == "episode"
            and item.role in {"element", "scene"}
            and item.media_type == "image"
            and item.semantic_key is not None
            and not item.semantic_key.startswith("legacy:")
        )
        desired = storyboard_reference_keys(
            episode.plan,
            self._style_profile,
            explicit_episode_keys=tuple(
                item.semantic_key
                for item in explicit_episode_assets
                if item.semantic_key is not None
            ),
        )
        candidates = self._repository.list_assets(
            run_id=episode.run_id,
            episode_id=episode.id,
            statuses=("approved", "ready"),
            semantic_keys=desired,
        )
        latest: dict[str, StoredAsset] = {}
        for asset in candidates:
            if asset.semantic_key in desired and not asset.semantic_key.startswith("legacy:"):
                latest[asset.semantic_key] = asset
        missing = [key for key in desired if key not in latest]
        if missing:
            raise ValueError(f"缺少已批准精确参考资产: {', '.join(missing)}")
        assets = tuple(latest[key] for key in desired)
        if any(asset.media_type != "image" for asset in assets):
            raise ValueError("当前Seedream故事板参考只允许图片资产")
        return ReferenceSelectionPlan(desired, assets)

    def _ensure_storyboard(
        self,
        episode: StoredEpisode,
        selection: ReferenceSelectionPlan,
        *,
        prompt_override: str | None = None,
        attempt: int = 1,
        retry_of_step_id: uuid.UUID | None = None,
        retry_reason: str | None = None,
    ) -> tuple[StoredAsset, ...]:
        expected_count = storyboard_panel_count(episode.plan)
        compiled = compile_storyboard_prompt(
            episode.plan,
            reference_roles=selection.semantic_keys,
            style_profile=self._style_profile,
            series_profile=self._series_profile,
            retry_feedback=retry_reason,
        )
        if prompt_override is not None and prompt_override.strip():
            compiled = _override_prompt(prompt_override)
        input_hash = _input_hash(
            compiled.text,
            str(expected_count),
            *(asset.sha256 for asset in selection.assets),
        )
        reusable = self._repository.find_reusable_storyboard(
            episode_id=episode.id,
            input_hash=input_hash,
            statuses=("candidate", "approved", "ready"),
        )
        if len(reusable) == expected_count:
            return reusable

        operation_key = "image:storyboard"
        prompt_sha = hashlib.sha256(compiled.text.encode("utf-8")).hexdigest()
        snapshot = ImageInputSnapshot(
            target="storyboard",
            expected_panel_count=expected_count,
            prompt_sha256=prompt_sha,
            reference_asset_ids=tuple(asset.id for asset in selection.assets),
            reference_sha256=tuple(asset.sha256 for asset in selection.assets),
            retry_of_step_id=retry_of_step_id,
            retry_reason=retry_reason,
        )
        step, _ = self._repository.create_step_with_prompt_intent(
            run_id=episode.run_id,
            episode_id=episode.id,
            parent_step_id=None,
            kind=StepKind.IMAGE,
            attempt=attempt,
            operation_key=operation_key,
            provider=self._provider_name,
            model=self._media_gateway.image_model,
            input_hash=input_hash,
            input_snapshot=snapshot.model_dump(mode="json"),
            prompt_purpose=PromptPurpose.STORYBOARD,
            prompt_model=self._media_gateway.image_model,
            prompt_text=compiled.text,
            parent_prompt_id=None,
        )
        if episode.status in {EpisodeStatus.PLANNED, EpisodeStatus.FAILED}:
            self._repository.set_episode_status(
                episode.id, EpisodeStatus.PREPARING_VISUALS
            )
        existing = tuple(
            asset
            for asset in self._repository.list_assets(
                run_id=episode.run_id,
                episode_id=episode.id,
                roles=("storyboard_panel",),
            )
            if asset.step_id == step.id
        )
        if len(existing) == expected_count:
            return tuple(sorted(existing, key=_panel_ordinal))
        if step.status in {StepStatus.FAILED, StepStatus.EXPIRED, StepStatus.CANCELLED}:
            raise StepRetryRequired(step.id, operation_key)
        if step.status is StepStatus.SUBMISSION_UNKNOWN:
            raise RuntimeError("故事板提交结果未知，必须先对账，禁止重复请求")

        self._repository.set_step_status(step.id, StepStatus.SUBMITTING)
        try:
            results = self._media_gateway.generate_storyboard(
                prompt=compiled.text,
                reference_paths=tuple(asset.path for asset in selection.assets),
                max_images=expected_count,
            )
        except GatewayError as exc:
            self._repository.fail_step(
                step.id,
                code=exc.code,
                message=str(exc),
                submission_unknown=exc.submission_unknown,
            )
            self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            raise
        except Exception as exc:
            # 网关之外泄的客户端异常仍属于供应商调用边界，不得错误归类为
            # 下载、尺寸或画幅等故事板技术QC问题。
            self._repository.fail_step(
                step.id,
                code="storyboard_provider_client_failed",
                message=str(exc),
            )
            self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            raise

        try:
            if len(results) != expected_count:
                raise ValueError(
                    f"Seedream故事板应返回{expected_count}张，实际返回{len(results)}张"
                )
            panels = self._download_panels(episode, step, results, expected_count)
            self._validate_panel_set(panels, expected_count)
        except Exception as exc:
            self._repository.fail_step(
                step.id,
                code="storyboard_technical_qc_failed",
                message=str(exc),
            )
            self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            raise

        if self._review_mode == "manual":
            return self._await_manual(step.id, panels, "等待人工审核整组故事板")
        return self._semantic_review(episode, step.id, panels)

    def _download_panels(
        self,
        episode: StoredEpisode,
        step: StoredStep,
        results: tuple[Any, ...],
        expected_count: int,
    ) -> tuple[StoredAsset, ...]:
        panels: list[StoredAsset] = []
        for ordinal, result in enumerate(results, 1):
            landed = self._asset_store.download(result.url, suffix=".png")
            metadata = {
                **self._probe.inspect_image(landed.path),
                "panelOrdinal": ordinal,
                "panelCount": expected_count,
            }
            panels.append(
                self._repository.save_asset(
                    run_id=episode.run_id,
                    episode_id=episode.id,
                    step_id=step.id,
                    role="storyboard_panel",
                    semantic_key=f"storyboard:panel-{ordinal:02d}",
                    scope="episode",
                    status="candidate",
                    media_type="image",
                    landed=landed,
                    metadata=metadata,
                )
            )
        return tuple(panels)

    def _semantic_review(
        self,
        episode: StoredEpisode,
        step_id: uuid.UUID,
        panels: tuple[StoredAsset, ...],
    ) -> tuple[StoredAsset, ...]:
        prompt = compile_storyboard_review_prompt(
            episode.plan,
            panel_count=len(panels),
            series_profile=self._series_profile,
            style_profile=self._style_profile,
        )
        storyboard_prompt = self._repository.get_prompt_for_step(
            step_id, purpose=PromptPurpose.STORYBOARD
        )
        try:
            self._repository.save_prompt(
                step_id=step_id,
                parent_prompt_id=storyboard_prompt.id,
                purpose=PromptPurpose.STORYBOARD_REVIEW,
                model=self._review_gateway.review_model,
                text=prompt,
            )
        except Exception as exc:
            # 审核Prompt也是供应商输入。无法持久化时绝不能调用视觉审核模型，
            # 否则该次调用没有可审计输入；图片Step明确失败并等待显式重试。
            self._repository.fail_step(
                step_id,
                code="review_prompt_persistence_failed",
                message=str(exc),
            )
            self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            raise
        try:
            result = self._review_gateway.review_storyboard(
                prompt=prompt,
                image_paths=tuple(item.path for item in panels),
            )
        except GatewayError as exc:
            return self._await_manual(
                step_id,
                panels,
                f"故事板语义审核异常，转人工：{exc.code}",
            )
        passed = all(
            (
                result.identity_ok,
                result.style_ok,
                result.action_sequence_ok,
                result.continuity_ok,
                result.ending_ok,
            )
        )
        evidence = {
            "reviewMode": "semantic_auto",
            "semanticVerified": passed and result.confidence >= 0.8,
            "identityOk": result.identity_ok,
            "styleOk": result.style_ok,
            "actionSequenceOk": result.action_sequence_ok,
            "continuityOk": result.continuity_ok,
            "endingOk": result.ending_ok,
            "confidence": result.confidence,
            "violations": list(result.violations),
            "warnings": list(result.warnings),
            "observations": list(result.evidence),
            "responseId": result.response_id,
            "providerRequestHash": result.request_hash,
            "orderedPanelSha256": [item.sha256 for item in panels],
        }
        if result.confidence < 0.8:
            return self._await_manual(step_id, panels, "故事板审核置信度低于0.80，转人工", evidence)
        self._repository.set_step_status(step_id, StepStatus.AWAITING_REVIEW)
        decision = "approved" if passed else "rejected"
        self._repository.commit_storyboard_review(
            step_id=step_id,
            asset_ids=tuple(item.id for item in panels),
            source="ark_visual",
            decision=decision,
            reason=(
                "整组故事板通过身份、二维画风、动作顺序、连续性和结尾审核"
                if passed
                else "故事板存在明确身份、画风、动作顺序、连续性或结尾错误"
            ),
            warnings=[{"code": "storyboard_warning", "message": item} for item in result.warnings],
            evidence=evidence,
        )
        return tuple(replace(item, status=decision) for item in panels)

    def retry_storyboard(
        self,
        episode: StoredEpisode,
        original_step: StoredStep,
        *,
        reason: str,
    ) -> tuple[StoredAsset, ...]:
        """显式创建故事板新attempt；旧组图、审核和Prompt全部保留。"""

        snapshot = ImageInputSnapshot.model_validate(original_step.input_snapshot)
        if snapshot.target != "storyboard":
            raise ValueError("原步骤不是可重试的故事板任务")
        if episode.status is EpisodeStatus.FAILED:
            self._repository.set_episode_status(episode.id, EpisodeStatus.PREPARING_VISUALS)
            episode = self._repository.get_episode(episode.run_id, episode.plan.slot)
        attempt = self._repository.next_step_attempt(
            episode_id=episode.id,
            kind=StepKind.IMAGE,
            operation_key="image:storyboard",
        )
        return self._ensure_storyboard(
            episode,
            self.select_references(episode),
            attempt=attempt,
            retry_of_step_id=original_step.id,
            retry_reason=reason,
        )

    def _await_manual(
        self,
        step_id: uuid.UUID,
        panels: tuple[StoredAsset, ...],
        reason: str,
        evidence: dict[str, Any] | None = None,
    ) -> tuple[StoredAsset, ...]:
        current = self._repository.get_step(step_id)
        if current.status is StepStatus.SUBMITTING:
            self._repository.set_step_status(step_id, StepStatus.AWAITING_REVIEW)
        self._repository.record_review(
            step_id=step_id,
            asset_id=None,
            source="ark_visual" if evidence else "technical",
            decision="pending",
            reason=reason,
            warnings=[],
            evidence={
                **(evidence or {}),
                "semanticReviewStatus": "pending",
                "semanticVerified": False,
                "orderedPanelIds": [str(item.id) for item in panels],
            },
        )
        return panels

    @staticmethod
    def _validate_panel_set(
        panels: tuple[StoredAsset, ...],
        expected_count: int,
    ) -> None:
        if len(panels) != expected_count:
            raise ValueError("故事板面板数量不完整")
        dimensions: set[tuple[int, int]] = set()
        for ordinal, panel in enumerate(panels, 1):
            if _panel_ordinal(panel) != ordinal:
                raise ValueError("故事板面板序号不连续")
            width = int(panel.metadata["width"])
            height = int(panel.metadata["height"])
            if abs(width / height - 9 / 16) / (9 / 16) > 0.01:
                raise ValueError(f"故事板面板{ordinal}与9:16比例偏差超过1%")
            if panel.metadata.get("blackBorderDetected") is True:
                raise ValueError(f"故事板面板{ordinal}检测到明显黑边")
            dimensions.add((width, height))
        if len(dimensions) != 1:
            raise ValueError("同一故事板组的所有面板尺寸必须一致")


def _panel_ordinal(asset: StoredAsset) -> int:
    return int(asset.metadata.get("panelOrdinal", 0))


def _override_prompt(value: str) -> CompiledPrompt:
    text = value.strip()
    if not text:
        raise ValueError("故事板Prompt覆盖不能为空")
    return CompiledPrompt(
        text=text,
        char_count=len(text),
        utf8_bytes=len(text.encode("utf-8")),
    )


def _input_hash(*values: str) -> str:
    return hashlib.sha256(
        json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
