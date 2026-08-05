"""日内定妆、故事板参考选择、Seedream组图和整组语义审核用例。

外观相同的时段复用已批准定妆图，变化时创建 ``image:look``；每个Episode只有
一个 ``image:storyboard`` 收费步骤。Canon与元素图只用于生成故事板；Seedance
只接收审核通过的有序面板。本服务不提交视频任务。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any

from ..domain.continuity import EntityKind, EntityLifecycle
from ..domain.prompts import (
    CompiledPrompt,
    compile_look_prompt,
    compile_look_review_prompt,
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
        image_request_timeout_seconds: float = 600,
        image_timeout_auto_retries: int = 1,
        image_retry_delay_seconds: float = 15,
        series_profile: SeriesVisualProfile,
        style_profile: StyleProfile,
    ) -> None:
        self._repository = repository
        self._media_gateway = media_gateway
        self._review_gateway = visual_review_gateway
        self._asset_store = asset_store
        self._probe = media_probe
        self._provider_name = provider_name
        self._image_request_timeout = image_request_timeout_seconds
        self._image_timeout_auto_retries = image_timeout_auto_retries
        self._image_retry_delay = image_retry_delay_seconds
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

        look = self._ensure_look_reference(episode)
        if look.status == "rejected":
            raise RuntimeError("日内定妆图语义审核失败，已阻断故事板和Seedance任务")
        if look.status not in {"approved", "ready"}:
            return None
        assets = self._ensure_storyboard(
            episode,
            self.select_references(episode, look),
            prompt_override=(prompt_overrides or {}).get("storyboard"),
        )
        if any(item.status == "rejected" for item in assets):
            raise RuntimeError("故事板语义审核失败，已阻断Seedance任务")
        if not all(item.status in {"approved", "ready"} for item in assets):
            return None
        current = self._repository.get_episode(episode.run_id, episode.plan.slot)
        if current.status is EpisodeStatus.PREPARING_VISUALS:
            # 故事板生成、审核可能由Web手动入口或自动流水线触发；统一在成功出口
            # 推进Episode，避免调用入口遗漏状态而重复生成已经批准的组图。
            self._repository.set_episode_status(episode.id, EpisodeStatus.VIDEO_PENDING)
        return assets

    def approved_storyboard(
        self,
        episode: StoredEpisode,
    ) -> tuple[StoredAsset, ...]:
        """取得视频阶段已经冻结的完整故事板组。

        这里不重新编译图片Prompt，也不按旧失败attempt回退；缺少完整批准组时直接
        停止视频提交，避免一次“生成视频”操作意外产生新的Seedream费用。
        """

        assets = self._repository.latest_approved_storyboard(episode.id)
        expected_count = storyboard_panel_count(episode.plan)
        if len(assets) != expected_count:
            raise RuntimeError(
                f"Episode已进入视频阶段，但批准故事板应为{expected_count}张，实际为{len(assets)}张"
            )
        return assets

    def _ensure_look_reference(
        self,
        episode: StoredEpisode,
        *,
        attempt: int = 1,
        retry_of_step_id: uuid.UUID | None = None,
        retry_reason: str | None = None,
        retry_feedback: str | None = None,
        auto_timeout_retry_index: int = 0,
        duplicate_billing_risk_accepted: bool = False,
    ) -> StoredAsset:
        """按本时段外观签名生成或复用日内定妆图。"""

        signature = _appearance_signature(episode)
        existing_looks = self._repository.list_assets(
            run_id=episode.run_id,
            roles=("look_reference",),
            statuses=("approved", "ready"),
        )
        matching = [
            item
            for item in existing_looks
            if item.metadata.get("appearanceSignature") == signature
        ]
        if matching:
            return matching[-1]

        series_profile = self._series_profile_for_run(episode.run_id)
        contextual_style = (
            self._style_profile.indoor_reference_key
            if episode.plan.script.style_context == "indoor"
            else self._style_profile.outdoor_reference_key
        )
        desired = (
            *series_profile.person_reference_keys,
            self._style_profile.line_reference_key,
            contextual_style,
        )
        candidates = self._repository.list_assets(
            run_id=episode.run_id,
            statuses=("approved", "ready"),
            semantic_keys=desired,
        )
        latest: dict[str, StoredAsset] = {}
        for asset in candidates:
            if asset.semantic_key in desired and asset.media_type == "image":
                latest[str(asset.semantic_key)] = asset
        missing = [key for key in desired if key not in latest]
        if missing:
            raise ValueError(f"缺少生成日内定妆图所需Canon: {', '.join(missing)}")
        references = tuple(latest[key] for key in desired)
        compiled = compile_look_prompt(
            episode.plan,
            reference_roles=desired,
            series_profile=series_profile,
            style_profile=self._style_profile,
            retry_feedback=retry_feedback,
        )
        input_hash = _input_hash(
            compiled.text,
            signature,
            *(asset.sha256 for asset in references),
        )
        reusable = self._repository.find_reusable_asset(
            episode_id=episode.id,
            role="look_reference",
            input_hash=input_hash,
            statuses=("candidate", "approved", "ready"),
        )
        if reusable is not None:
            if reusable.status in {"approved", "ready"}:
                return reusable
            if reusable.step_id is None:
                raise RuntimeError("可复用定妆图缺少生成Step")
            reusable_step = self._repository.get_step(reusable.step_id)
            if reusable_step.status is StepStatus.AWAITING_REVIEW:
                # 低置信或manual模式已经把决定权交给人工。重复点击生成按钮
                # 只能复用同一张候选图，不能再次调用Ark审核产生隐性费用。
                return reusable
            return self._semantic_review_look(
                episode, reusable.step_id, reusable, series_profile
            )

        prompt_sha = hashlib.sha256(compiled.text.encode("utf-8")).hexdigest()
        snapshot = ImageInputSnapshot(
            target="look",
            expected_panel_count=1,
            prompt_sha256=prompt_sha,
            reference_asset_ids=tuple(asset.id for asset in references),
            reference_sha256=tuple(asset.sha256 for asset in references),
            retry_of_step_id=retry_of_step_id,
            retry_reason=retry_reason,
            request_timeout_seconds=self._image_request_timeout,
            auto_timeout_retry_index=auto_timeout_retry_index,
            duplicate_billing_risk_accepted=duplicate_billing_risk_accepted,
        )
        step, _ = self._repository.create_step_with_prompt_intent(
            run_id=episode.run_id,
            episode_id=episode.id,
            parent_step_id=None,
            kind=StepKind.IMAGE,
            attempt=attempt,
            operation_key="image:look",
            provider=self._provider_name,
            model=self._media_gateway.image_model,
            input_hash=input_hash,
            input_snapshot=snapshot.model_dump(mode="json"),
            prompt_purpose=PromptPurpose.LOOK,
            prompt_model=self._media_gateway.image_model,
            prompt_text=compiled.text,
            parent_prompt_id=None,
        )
        if episode.status in {EpisodeStatus.PLANNED, EpisodeStatus.FAILED}:
            self._repository.set_episode_status(
                episode.id, EpisodeStatus.PREPARING_VISUALS
            )
        if step.status in {StepStatus.FAILED, StepStatus.EXPIRED, StepStatus.CANCELLED}:
            raise StepRetryRequired(step.id, "image:look")
        if step.status is StepStatus.SUBMISSION_UNKNOWN:
            raise RuntimeError("定妆图提交结果未知，需确认潜在重复计费后重试")

        self._repository.set_step_status(step.id, StepStatus.SUBMITTING)
        try:
            result = self._media_gateway.generate_look(
                prompt=compiled.text,
                reference_paths=tuple(asset.path for asset in references),
            )
        except GatewayError as exc:
            self._repository.fail_step(
                step.id,
                code=exc.code,
                message=str(exc),
                submission_unknown=exc.submission_unknown,
                request_id=exc.request_id,
                input_snapshot_patch={
                    "provider_task_status": (
                        "submission_unknown_timeout"
                        if exc.submission_unknown and exc.timed_out
                        else "submission_unknown"
                        if exc.submission_unknown
                        else "failed"
                    )
                },
            )
            if (
                exc.submission_unknown
                and exc.timed_out
                and auto_timeout_retry_index < self._image_timeout_auto_retries
            ):
                time.sleep(self._image_retry_delay)
                next_attempt = self._repository.next_step_attempt(
                    episode_id=episode.id,
                    kind=StepKind.IMAGE,
                    operation_key="image:look",
                )
                return self._ensure_look_reference(
                    episode,
                    attempt=next_attempt,
                    retry_of_step_id=step.id,
                    retry_reason="Seedream定妆图同步请求超时，按配置自动重试一次",
                    auto_timeout_retry_index=auto_timeout_retry_index + 1,
                    duplicate_billing_risk_accepted=True,
                )
            self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            raise

        try:
            landed = self._asset_store.download(result.url, suffix=".png")
            metadata = {
                **self._probe.inspect_image(landed.path),
                "appearanceSignature": signature,
                "appearanceDescription": episode.plan.script.appearance.description,
            }
            deviation = _validate_portrait_image(metadata, label="定妆图")
            if deviation > 0.01:
                original = landed
                box = _center_crop_box(
                    int(metadata["width"]),
                    int(metadata["height"]),
                )
                cropped = self._asset_store.crop_local(landed.path, box=box)
                cropped_metadata = self._probe.inspect_image(cropped.path)
                landed = cropped
                metadata = {
                    **cropped_metadata,
                    "appearanceSignature": signature,
                    "appearanceDescription": episode.plan.script.appearance.description,
                    "normalizedToNineSixteen": True,
                    "normalizedFromPath": str(original.path),
                    "normalizedFromSha256": original.sha256,
                    "normalizationCropBox": list(box),
                }
            asset = self._repository.save_asset(
                run_id=episode.run_id,
                episode_id=episode.id,
                step_id=step.id,
                role="look_reference",
                semantic_key=f"look:{episode.plan.slot.value}-{signature[:12]}",
                scope="episode",
                status="candidate",
                media_type="image",
                landed=landed,
                metadata=metadata,
            )
        except Exception as exc:
            # Ark 已返回图片后，下载、探测或画幅检查仍可能失败。这里明确结束本次
            # Step，避免留下永久 submitting 状态；再次生成必须走显式 retry-step。
            self._repository.fail_step(
                step.id,
                code="look_technical_qc_failed",
                message=str(exc),
            )
            self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            raise
        if self._review_mode == "manual":
            self._repository.set_step_status(step.id, StepStatus.AWAITING_REVIEW)
            self._repository.record_review(
                step_id=step.id,
                asset_id=asset.id,
                source="technical",
                decision="pending",
                reason="等待人工审核日内定妆图",
                warnings=[],
                evidence={"semanticReviewStatus": "pending", "semanticVerified": False},
            )
            return asset
        return self._semantic_review_look(episode, step.id, asset, series_profile)

    def _semantic_review_look(
        self,
        episode: StoredEpisode,
        step_id: uuid.UUID,
        asset: StoredAsset,
        series_profile: SeriesVisualProfile,
    ) -> StoredAsset:
        reference_assets = self._look_review_references(step_id, series_profile)
        prompt = compile_look_review_prompt(
            episode.plan,
            reference_roles=tuple(
                str(item.semantic_key)
                for item in reference_assets
                if item.semantic_key is not None
            ),
            series_profile=series_profile,
            style_profile=self._style_profile,
        )
        generation_prompt = self._repository.get_prompt_for_step(
            step_id, purpose=PromptPurpose.LOOK
        )
        try:
            self._repository.save_prompt(
                step_id=step_id,
                parent_prompt_id=generation_prompt.id,
                purpose=PromptPurpose.LOOK_REVIEW,
                model=self._review_gateway.review_model,
                text=prompt,
            )
        except Exception as exc:
            # 审核输入无法持久化时不调用Ark，保证每一次模型调用都有可追溯Prompt。
            self._repository.fail_step(
                step_id,
                code="look_review_prompt_persistence_failed",
                message=str(exc),
            )
            self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            raise
        try:
            result = self._review_gateway.review_look(
                prompt=prompt,
                image_path=asset.path,
                reference_paths=tuple(item.path for item in reference_assets),
            )
        except Exception as exc:
            current = self._repository.get_step(step_id)
            if current.status is StepStatus.SUBMITTING:
                self._repository.set_step_status(step_id, StepStatus.AWAITING_REVIEW)
            self._repository.record_review(
                step_id=step_id,
                asset_id=asset.id,
                source="ark_visual",
                decision="pending",
                reason=f"定妆图语义审核异常，转人工：{type(exc).__name__}",
                warnings=[],
                evidence={"semanticReviewStatus": "pending", "semanticVerified": False},
            )
            return asset

        passed = result.identity_ok and result.style_ok and result.appearance_ok
        evidence = {
            "semanticReviewStatus": "approved" if passed else "rejected",
            "semanticVerified": passed and result.confidence >= 0.8,
            "identityOk": result.identity_ok,
            "styleOk": result.style_ok,
            "appearanceOk": result.appearance_ok,
            "confidence": result.confidence,
            "violations": list(result.violations),
            "observations": list(result.evidence),
            "responseId": result.response_id,
            "providerRequestHash": result.request_hash,
        }
        current = self._repository.get_step(step_id)
        if current.status is StepStatus.SUBMITTING:
            self._repository.set_step_status(step_id, StepStatus.AWAITING_REVIEW)
        if result.confidence < 0.8:
            self._repository.record_review(
                step_id=step_id,
                asset_id=asset.id,
                source="ark_visual",
                decision="pending",
                reason="定妆图审核置信度低，转人工",
                warnings=[{"code": "look_warning", "message": item} for item in result.warnings],
                evidence=evidence,
            )
            return asset
        decision = "approved" if passed else "rejected"
        self._repository.commit_asset_review(
            asset_id=asset.id,
            source="ark_visual",
            decision=decision,
            reason=(
                "定妆图通过身份、画风和本时段装扮审核"
                if passed
                else "定妆图存在身份、画风或装扮错误"
            ),
            warnings=[{"code": "look_warning", "message": item} for item in result.warnings],
            evidence=evidence,
        )
        return replace(asset, status=decision)

    def retry_look(
        self,
        episode: StoredEpisode,
        original_step: StoredStep,
        *,
        reason: str,
        duplicate_billing_risk_accepted: bool = False,
    ) -> StoredAsset:
        """为失败或未知提交的定妆图显式创建新attempt。"""

        snapshot = ImageInputSnapshot.model_validate(original_step.input_snapshot)
        if snapshot.target != "look":
            raise ValueError("原步骤不是可重试的定妆图任务")
        if episode.status is EpisodeStatus.FAILED:
            self._repository.set_episode_status(
                episode.id, EpisodeStatus.PREPARING_VISUALS
            )
            episode = self._repository.get_episode(episode.run_id, episode.plan.slot)
        attempt = self._repository.next_step_attempt(
            episode_id=episode.id,
            kind=StepKind.IMAGE,
            operation_key="image:look",
        )
        return self._ensure_look_reference(
            episode,
            attempt=attempt,
            retry_of_step_id=original_step.id,
            retry_reason=reason,
            retry_feedback=(
                None if original_step.status is StepStatus.SUBMISSION_UNKNOWN else reason
            ),
            duplicate_billing_risk_accepted=duplicate_billing_risk_accepted,
        )

    def _series_profile_for_run(self, run_id: uuid.UUID) -> SeriesVisualProfile:
        metadata = self._repository.get_planning_context(run_id).get(
            "planningMetadata", {}
        )
        raw = metadata.get("seriesProfile") if isinstance(metadata, dict) else None
        if isinstance(raw, dict):
            return SeriesVisualProfile.model_validate(raw)
        return self._series_profile

    def _look_review_references(
        self,
        step_id: uuid.UUID,
        series_profile: SeriesVisualProfile,
    ) -> tuple[StoredAsset, ...]:
        """恢复生成时冻结的人物Canon，避免用后来上传的版本审核旧定妆图。"""

        snapshot = ImageInputSnapshot.model_validate(
            self._repository.get_step(step_id).input_snapshot
        )
        assets = tuple(
            self._repository.asset_detail(asset_id)
            for asset_id in snapshot.reference_asset_ids
        )
        selected_by_key = {
            item.semantic_key: item
            for item in assets
            if item.semantic_key in series_profile.person_reference_keys
        }
        missing = [
            key for key in series_profile.person_reference_keys if key not in selected_by_key
        ]
        if missing:
            raise RuntimeError(f"定妆图审核缺少生成时冻结的人物Canon: {', '.join(missing)}")
        return tuple(selected_by_key[key] for key in series_profile.person_reference_keys)

    def select_references(
        self,
        episode: StoredEpisode,
        look: StoredAsset,
    ) -> ReferenceSelectionPlan:
        """选择固定Canon与最多一张用户显式上传的Episode参考图。"""

        if look.semantic_key is None:
            raise ValueError("日内定妆图缺少semantic_key，不能建立可审计的参考绑定")
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
            self._series_profile_for_run(episode.run_id),
            look_key=look.semantic_key,
            explicit_episode_keys=tuple(
                item.semantic_key
                for item in explicit_episode_assets
                if item.semantic_key is not None
            ),
        )
        explicit_keys = {
            asset.semantic_key
            for asset in explicit_episode_assets
            if asset.semantic_key is not None
        }
        canonical_keys = tuple(
            key
            for key in desired
            if key != look.semantic_key and key not in explicit_keys
        )
        candidates = self._repository.list_assets(
            run_id=episode.run_id,
            statuses=("approved", "ready"),
            semantic_keys=canonical_keys,
        )
        # 日内定妆图可以由上午生成并在中午、傍晚复用，因此不能按当前 Episode
        # 过滤。Episode 专属素材则只接受本 Episode 明确上传的资产，避免串片。
        latest: dict[str, StoredAsset] = {str(look.semantic_key): look}
        latest.update(
            {
                str(asset.semantic_key): asset
                for asset in explicit_episode_assets
                if asset.semantic_key is not None
            }
        )
        for asset in candidates:
            if (
                asset.scope == "canon"
                and asset.semantic_key in desired
                and not asset.semantic_key.startswith("legacy:")
            ):
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
        prompt_retry_feedback: str | None = None,
        auto_timeout_retry_index: int = 0,
        duplicate_billing_risk_accepted: bool = False,
    ) -> tuple[StoredAsset, ...]:
        expected_count = storyboard_panel_count(episode.plan)
        series_profile = self._series_profile_for_run(episode.run_id)
        compiled = compile_storyboard_prompt(
            episode.plan,
            reference_roles=selection.semantic_keys,
            style_profile=self._style_profile,
            series_profile=series_profile,
            retry_feedback=prompt_retry_feedback,
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
            if all(item.status in {"approved", "ready"} for item in reusable):
                return reusable
            step_ids = {item.step_id for item in reusable}
            if None in step_ids or len(step_ids) != 1:
                raise RuntimeError("可复用故事板没有唯一的生成Step，无法安全恢复审核")
            reusable_step_id = next(iter(step_ids))
            reusable_step = self._repository.get_step(reusable_step_id)
            if reusable_step.status is StepStatus.AWAITING_REVIEW:
                # 已转人工的整组面板保持原状态；刷新或重复点击不会再次调用
                # 视觉审核模型，也不会把manual模式悄悄升级成自动审核。
                return reusable
            # 图片已经成功落盘时，只恢复语义审核；绝不能因为审核模型异常而再次调用Seedream。
            return self._semantic_review(episode, reusable_step_id, reusable)

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
            request_timeout_seconds=self._image_request_timeout,
            auto_timeout_retry_index=auto_timeout_retry_index,
            duplicate_billing_risk_accepted=duplicate_billing_risk_accepted,
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
            stored_snapshot = ImageInputSnapshot.model_validate(step.input_snapshot)
            if (
                stored_snapshot.provider_task_status == "submission_unknown_timeout"
                and stored_snapshot.auto_timeout_retry_index
                < self._image_timeout_auto_retries
            ):
                return self._retry_after_timeout(
                    episode,
                    selection,
                    step,
                    prompt_override=prompt_override,
                    auto_timeout_retry_index=stored_snapshot.auto_timeout_retry_index + 1,
                )
            raise RuntimeError(
                "故事板提交结果未知；自动重试已用尽，需确认潜在重复计费后再生成"
            )

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
                request_id=exc.request_id,
                input_snapshot_patch={
                    "provider_task_status": (
                        "submission_unknown_timeout"
                        if exc.submission_unknown and exc.timed_out
                        else "submission_unknown"
                        if exc.submission_unknown
                        else "failed"
                    )
                },
            )
            if (
                exc.submission_unknown
                and exc.timed_out
                and auto_timeout_retry_index < self._image_timeout_auto_retries
            ):
                return self._retry_after_timeout(
                    episode,
                    selection,
                    step,
                    prompt_override=prompt_override,
                    auto_timeout_retry_index=auto_timeout_retry_index + 1,
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
        downloaded: list[tuple[Any, dict[str, Any]]] = []
        for ordinal, result in enumerate(results, 1):
            landed = self._asset_store.download(result.url, suffix=".png")
            metadata = {
                **self._probe.inspect_image(landed.path),
                "panelOrdinal": ordinal,
                "panelCount": expected_count,
            }
            downloaded.append((landed, metadata))

        dimensions = {
            (int(metadata["width"]), int(metadata["height"]))
            for _, metadata in downloaded
        }
        if len(dimensions) != 1:
            raise ValueError("同一故事板组的所有原始面板尺寸必须一致")
        if any(metadata.get("blackBorderDetected") is True for _, metadata in downloaded):
            raise ValueError("故事板面板检测到明显黑边")
        deviations = [
            abs(int(metadata["width"]) / int(metadata["height"]) - 9 / 16)
            / (9 / 16)
            for _, metadata in downloaded
        ]
        if any(value > 0.02 for value in deviations):
            raise ValueError("故事板面板的9:16比例偏差超过2%")

        normalized: list[tuple[Any, dict[str, Any]]] = downloaded
        if any(value > 0.01 for value in deviations):
            normalized = []
            width, height = next(iter(dimensions))
            box = _center_crop_box(width, height)
            for landed, metadata in downloaded:
                cropped = self._asset_store.crop_local(landed.path, box=box)
                cropped_metadata = {
                    **self._probe.inspect_image(cropped.path),
                    "panelOrdinal": metadata["panelOrdinal"],
                    "panelCount": expected_count,
                    "normalizedToNineSixteen": True,
                    "normalizedFromPath": str(landed.path),
                    "normalizedFromSha256": landed.sha256,
                    "normalizationCropBox": list(box),
                }
                normalized.append((cropped, cropped_metadata))

        panels: list[StoredAsset] = []
        for landed, metadata in normalized:
            ordinal = int(metadata["panelOrdinal"])
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
        snapshot = ImageInputSnapshot.model_validate(
            self._repository.get_step(step_id).input_snapshot
        )
        generation_references = tuple(
            self._repository.asset_detail(asset_id)
            for asset_id in snapshot.reference_asset_ids
        )
        review_references = tuple(
            item
            for item in generation_references
            if item.role == "look_reference"
            or (item.semantic_key or "").startswith("cat:")
            or item.semantic_key == self._style_profile.line_reference_key
        )
        prompt = compile_storyboard_review_prompt(
            episode.plan,
            panel_count=len(panels),
            reference_roles=tuple(
                str(item.semantic_key)
                for item in review_references
                if item.semantic_key is not None
            ),
            series_profile=self._series_profile_for_run(episode.run_id),
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
                reference_paths=tuple(item.path for item in review_references),
            )
        except GatewayError as exc:
            return self._await_manual(
                step_id,
                panels,
                f"故事板语义审核异常，转人工：{exc.code}",
            )
        except Exception as exc:
            # 审核响应解析失败、SDK内部异常等都不能被误认为生图失败，更不能触发新的媒体费用。
            # 已下载组图保持candidate，允许后续对同一批图片重新审核或由人工决定。
            return self._await_manual(
                step_id,
                panels,
                f"故事板语义审核异常，转人工：{type(exc).__name__}",
            )
        # 纯人物与猫咪互动的故事板只承担身份、画风和大致构图锚定。
        # 抚摸、转头、闭眼等连续姿态由Seedance完成；若把每个微动作都设为静态组图硬门，
        # 会在没有关键道具风险时反复产生图片费用。涉及道具或生命周期变化时仍执行完整硬门。
        continuity_entities = episode.plan.script.continuity.entities
        pose_only_storyboard = all(
            item.kind in {EntityKind.PERSON, EntityKind.CAT}
            and item.lifecycle is EntityLifecycle.PERSIST
            and item.start_state == item.end_state
            for item in continuity_entities
        )
        required_checks = [result.identity_ok, result.style_ok]
        if not pose_only_storyboard:
            required_checks.extend(
                (
                    result.action_sequence_ok,
                    result.continuity_ok,
                    result.ending_ok,
                )
            )
        passed = all(required_checks)
        evidence = {
            "reviewMode": "semantic_auto",
            "semanticPolicyVersion": "lightweight_relationship_arc_v1",
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
            "poseOnlyStoryboardPolicy": pose_only_storyboard,
        }
        if result.confidence < 0.8:
            return self._await_manual(step_id, panels, "故事板审核置信度低于0.80，转人工", evidence)
        current_step = self._repository.get_step(step_id)
        if current_step.status is StepStatus.SUBMITTING:
            self._repository.set_step_status(step_id, StepStatus.AWAITING_REVIEW)
        elif current_step.status is not StepStatus.AWAITING_REVIEW:
            raise RuntimeError(
                f"故事板Step状态{current_step.status.value}不允许提交审核结论"
            )
        decision = "approved" if passed else "rejected"
        if passed and pose_only_storyboard:
            review_reason = (
                "整组故事板通过身份与二维画风硬门；动作顺序、连续性和结尾细节"
                "作为视频阶段诊断证据保留"
            )
        elif passed:
            review_reason = "整组故事板通过身份、二维画风、动作顺序、连续性和结尾审核"
        else:
            review_reason = "故事板存在明确身份、画风、动作顺序、连续性或结尾错误"
        self._repository.commit_storyboard_review(
            step_id=step_id,
            asset_ids=tuple(item.id for item in panels),
            source="ark_visual",
            decision=decision,
            reason=review_reason,
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
        duplicate_billing_risk_accepted: bool = False,
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
        look = self._ensure_look_reference(episode)
        return self._ensure_storyboard(
            episode,
            self.select_references(episode, look),
            attempt=attempt,
            retry_of_step_id=original_step.id,
            retry_reason=reason,
            # 同步超时并不表示故事板内容有问题。只在明确的媒体失败重试时
            # 把人工反馈加入Prompt，避免未知提交的重做无故改变幂等输入语义。
            prompt_retry_feedback=(
                None
                if original_step.status is StepStatus.SUBMISSION_UNKNOWN
                else reason
            ),
            auto_timeout_retry_index=(
                self._image_timeout_auto_retries
                if original_step.status is StepStatus.SUBMISSION_UNKNOWN
                else 0
            ),
            duplicate_billing_risk_accepted=duplicate_billing_risk_accepted,
        )

    def _retry_after_timeout(
        self,
        episode: StoredEpisode,
        selection: ReferenceSelectionPlan,
        original_step: StoredStep,
        *,
        prompt_override: str | None,
        auto_timeout_retry_index: int,
    ) -> tuple[StoredAsset, ...]:
        """Seedream同步超时后仅自动再提交一次，并完整保留未知旧attempt。"""

        time.sleep(self._image_retry_delay)
        attempt = self._repository.next_step_attempt(
            episode_id=episode.id,
            kind=StepKind.IMAGE,
            operation_key="image:storyboard",
        )
        return self._ensure_storyboard(
            episode,
            selection,
            prompt_override=prompt_override,
            attempt=attempt,
            retry_of_step_id=original_step.id,
            retry_reason="Seedream同步请求超时，按运行策略自动重试一次",
            auto_timeout_retry_index=auto_timeout_retry_index,
            duplicate_billing_risk_accepted=True,
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


def _appearance_signature(episode: StoredEpisode) -> str:
    appearance = episode.plan.script.appearance
    # 定妆图只表达当前真正可见的外观。changes_from_previous和change_reason
    # 属于叙事来源，不应让视觉结果完全相同的下午/傍晚重复产生Seedream费用。
    payload = {"description": " ".join(appearance.description.split())}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _validate_portrait_image(metadata: dict[str, Any], *, label: str) -> float:
    width = int(metadata["width"])
    height = int(metadata["height"])
    deviation = abs(width / height - 9 / 16) / (9 / 16)
    if deviation > 0.02:
        raise ValueError(f"{label}的9:16比例偏差超过2%")
    if metadata.get("blackBorderDetected") is True:
        raise ValueError(f"{label}检测到明显黑边")
    return deviation


def _center_crop_box(width: int, height: int) -> tuple[int, int, int, int]:
    """以全组相同裁剪框把轻微比例偏差归一到9:16。"""

    target_ratio = 9 / 16
    if width / height > target_ratio:
        cropped_width = max(1, round(height * target_ratio))
        left = (width - cropped_width) // 2
        return left, 0, left + cropped_width, height
    cropped_height = max(1, round(width / target_ratio))
    top = (height - cropped_height) // 2
    return 0, top, width, top + cropped_height


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
