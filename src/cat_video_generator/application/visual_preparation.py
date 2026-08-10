"""定妆图与开场视觉锚点的生产用例。

本服务只拥有Seedream图片生命周期和图片语义审核；不生成故事板组图，也不提交视频。
收费意图与Prompt先原子落库，失败重试始终创建新attempt。
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass

from ..domain.contracts import Slot
from ..domain.prompts import (
    CompiledPrompt,
    compile_image_review_prompt,
    compile_look_prompt,
    compile_opening_anchor_prompt,
)
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
    VisualReviewGateway,
)


@dataclass(frozen=True, slots=True)
class ReferenceSelectionPlan:
    semantic_keys: tuple[str, ...]
    assets: tuple[StoredAsset, ...]


class VisualPreparationService:
    """按需生成可复用定妆图和每集唯一开场锚点。"""

    def __init__(
        self,
        *,
        repository: ProductionStore,
        media_gateway: MediaGenerationGateway,
        visual_review_gateway: VisualReviewGateway,
        asset_store: AssetStore,
        media_probe: MediaProbe,
        provider_name: str,
        image_review_mode: str,
        image_request_timeout_seconds: float = 600,
        image_timeout_auto_retries: int = 1,
        image_retry_delay_seconds: float = 15,
        series_profile: SeriesVisualProfile,
        style_profile: StyleProfile,
    ) -> None:
        if image_review_mode not in {"semantic_auto", "manual"}:
            raise ValueError("图片审核只允许semantic_auto或manual")
        self._repository = repository
        self._media_gateway = media_gateway
        self._review_gateway = visual_review_gateway
        self._asset_store = asset_store
        self._probe = media_probe
        self._provider_name = provider_name
        self._review_mode = image_review_mode
        self._image_request_timeout = image_request_timeout_seconds
        self._image_timeout_auto_retries = image_timeout_auto_retries
        self._image_retry_delay = image_retry_delay_seconds
        self._series_profile = series_profile
        self._style_profile = style_profile

    def prepare(
        self,
        episode: StoredEpisode,
        *,
        prompt_overrides: dict[str, str] | None = None,
    ) -> StoredAsset | None:
        """生成或复用定妆图和开场锚点；人工审核待处理时返回None。"""

        overrides = prompt_overrides or {}
        look = self._ensure_image(
            episode,
            target="look",
            prompt_override=overrides.get("look"),
        )
        if look.status == "rejected":
            raise RuntimeError("定妆图审核失败，已阻断开场锚点和视频")
        if look.status not in {"approved", "ready"}:
            return None
        anchor = self._ensure_image(
            episode,
            target="opening_anchor",
            look=look,
            prompt_override=overrides.get("opening_anchor"),
        )
        if anchor.status == "rejected":
            raise RuntimeError("开场锚点审核失败，已阻断视频")
        if anchor.status not in {"approved", "ready"}:
            return None
        current = self._repository.get_episode(episode.run_id, episode.plan.slot)
        if current.status is EpisodeStatus.PREPARING_VISUALS:
            self._repository.set_episode_status(episode.id, EpisodeStatus.VIDEO_PENDING)
        return anchor

    def approved_opening_anchor(self, episode: StoredEpisode) -> StoredAsset:
        assets = self._repository.list_assets(
            run_id=episode.run_id,
            episode_id=episode.id,
            roles=("opening_anchor",),
            statuses=("approved", "ready"),
        )
        exact = [item for item in assets if item.episode_id == episode.id]
        if not exact:
            raise RuntimeError("Episode尚无批准的开场锚点")
        return exact[-1]

    def retry_image(
        self,
        step_id: uuid.UUID,
        *,
        reason: str,
        duplicate_billing_risk_accepted: bool = False,
    ) -> StoredAsset:
        step = self._repository.get_step(step_id)
        if step.kind is not StepKind.IMAGE:
            raise ValueError("只能用图片Step重试视觉锚点")
        if step.status not in {
            StepStatus.FAILED,
            StepStatus.EXPIRED,
            StepStatus.CANCELLED,
            StepStatus.SUBMISSION_UNKNOWN,
        }:
            raise ValueError(f"Step状态{step.status.value}不允许图片重试")
        if step.status is StepStatus.SUBMISSION_UNKNOWN and not duplicate_billing_risk_accepted:
            raise ValueError("图片提交结果未知，必须明确接受潜在重复计费")
        if step.episode_id is None:
            raise ValueError("图片Step缺少Episode")
        detail = self._repository.episode_detail(step.episode_id)
        episode = self._repository.get_episode(
            uuid.UUID(detail["runId"]), Slot(str(detail["slot"]))
        )
        snapshot = ImageInputSnapshot.model_validate(step.input_snapshot)
        attempt = self._repository.next_step_attempt(
            episode_id=episode.id,
            kind=StepKind.IMAGE,
            operation_key=f"image:{snapshot.target}",
        )
        look = None
        if snapshot.target == "opening_anchor":
            looks = self._repository.list_assets(
                run_id=episode.run_id,
                episode_id=episode.id,
                roles=("look_reference",),
                statuses=("approved", "ready"),
            )
            exact = [item for item in looks if item.episode_id == episode.id]
            if not exact:
                raise RuntimeError("重试开场锚点前必须已有批准定妆图")
            look = exact[-1]
        return self._ensure_image(
            episode,
            target=snapshot.target,
            look=look,
            attempt=attempt,
            retry_of_step_id=step.id,
            retry_reason=reason,
            retry_feedback=reason,
            duplicate_billing_risk_accepted=duplicate_billing_risk_accepted,
        )

    def _ensure_image(
        self,
        episode: StoredEpisode,
        *,
        target: str,
        look: StoredAsset | None = None,
        prompt_override: str | None = None,
        attempt: int = 1,
        retry_of_step_id: uuid.UUID | None = None,
        retry_reason: str | None = None,
        retry_feedback: str | None = None,
        auto_timeout_retry_index: int = 0,
        duplicate_billing_risk_accepted: bool = False,
    ) -> StoredAsset:
        if target not in {"look", "opening_anchor"}:
            raise ValueError(f"未知视觉目标{target}")
        signature = _appearance_signature(episode)
        if target == "look":
            existing = self._matching_look(episode, signature)
            if existing is not None and retry_of_step_id is None:
                return existing
            references = self._look_references(episode)
            compiled = compile_look_prompt(
                episode.plan,
                reference_roles=references.semantic_keys,
                series_profile=self._series_profile,
                style_profile=self._style_profile,
                retry_feedback=retry_feedback,
            )
            role = "look_reference"
            semantic_key = f"look:{episode.plan.slot.value}-{signature[:12]}"
        else:
            if look is None:
                raise ValueError("开场锚点必须绑定批准定妆图")
            references = self._anchor_references(episode, look)
            compiled = compile_opening_anchor_prompt(
                episode.plan,
                reference_roles=references.semantic_keys,
                series_profile=self._series_profile,
                style_profile=self._style_profile,
                retry_feedback=retry_feedback,
            )
            role = "opening_anchor"
            semantic_key = f"opening:{episode.plan.slot.value}"
        if prompt_override is not None:
            compiled = _override(compiled, prompt_override)
        input_hash = _input_hash(
            target,
            compiled.text,
            signature,
            *(asset.sha256 for asset in references.assets),
        )
        reusable = self._repository.find_reusable_asset(
            episode_id=episode.id,
            role=role,
            input_hash=input_hash,
            statuses=("candidate", "approved", "ready"),
        )
        if reusable is not None and retry_of_step_id is None:
            if reusable.status in {"approved", "ready"}:
                return reusable
            if reusable.step_id is None:
                raise RuntimeError("候选图片缺少生产Step")
            if self._repository.get_step(reusable.step_id).status is StepStatus.AWAITING_REVIEW:
                return reusable
            return self._review_image(episode, reusable, target, references)

        snapshot = ImageInputSnapshot(
            target=target,
            prompt_sha256=hashlib.sha256(compiled.text.encode("utf-8")).hexdigest(),
            reference_asset_ids=tuple(item.id for item in references.assets),
            reference_sha256=tuple(item.sha256 for item in references.assets),
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
            operation_key=f"image:{target}",
            provider=self._provider_name,
            model=self._media_gateway.image_model,
            input_hash=input_hash,
            input_snapshot=snapshot.model_dump(mode="json"),
            prompt_purpose=PromptPurpose.IMAGE,
            prompt_model=self._media_gateway.image_model,
            prompt_text=compiled.text,
            parent_prompt_id=None,
        )
        if episode.status in {EpisodeStatus.PLANNED, EpisodeStatus.FAILED}:
            self._repository.set_episode_status(episode.id, EpisodeStatus.PREPARING_VISUALS)
        if step.status in {StepStatus.FAILED, StepStatus.EXPIRED, StepStatus.CANCELLED}:
            raise StepRetryRequired(step.id, f"image:{target}")
        if step.status is StepStatus.SUBMISSION_UNKNOWN:
            raise RuntimeError("图片提交结果未知，必须显式确认潜在重复计费")
        self._repository.set_step_status(step.id, StepStatus.SUBMITTING)
        try:
            result = self._media_gateway.generate_image(
                prompt=compiled.text,
                reference_paths=tuple(item.path for item in references.assets),
            )
        except GatewayError as exc:
            self._repository.fail_step(
                step.id,
                code=exc.code,
                message=str(exc),
                submission_unknown=exc.submission_unknown,
                request_id=exc.request_id,
                input_snapshot_patch={
                    "provider_task_status": "submission_unknown"
                    if exc.submission_unknown
                    else "failed"
                },
            )
            if (
                exc.submission_unknown
                and exc.timed_out
                and auto_timeout_retry_index < self._image_timeout_auto_retries
            ):
                time.sleep(self._image_retry_delay)
                return self._ensure_image(
                    episode,
                    target=target,
                    look=look,
                    attempt=self._repository.next_step_attempt(
                        episode_id=episode.id,
                        kind=StepKind.IMAGE,
                        operation_key=f"image:{target}",
                    ),
                    retry_of_step_id=step.id,
                    retry_reason="Seedream同步请求超时，按配置自动重试一次",
                    auto_timeout_retry_index=auto_timeout_retry_index + 1,
                    duplicate_billing_risk_accepted=True,
                )
            self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            raise
        try:
            landed = self._asset_store.download(result.url, suffix=".png")
            metadata = self._probe.inspect_image(landed.path)
            landed, metadata = self._normalize_portrait(landed, metadata)
            metadata = {
                **metadata,
                "appearanceSignature": signature,
                "appearanceDescription": episode.plan.script.appearance.description,
                "inputHash": input_hash,
            }
            asset = self._repository.save_asset(
                run_id=episode.run_id,
                episode_id=episode.id,
                step_id=step.id,
                role=role,
                semantic_key=semantic_key,
                scope="episode",
                status="candidate",
                media_type="image",
                landed=landed,
                metadata=metadata,
            )
        except Exception as exc:
            self._repository.fail_step(step.id, code="image_technical_qc_failed", message=str(exc))
            self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            raise
        self._repository.set_step_status(step.id, StepStatus.AWAITING_REVIEW)
        if self._review_mode == "manual":
            self._repository.record_review(
                step_id=step.id,
                asset_id=asset.id,
                source="technical",
                decision="pending",
                reason="等待人工图片审核",
                warnings=[],
                evidence={"semanticReviewStatus": "pending", "semanticVerified": False},
            )
            return asset
        return self._review_image(episode, asset, target, references)

    def _review_image(
        self,
        episode: StoredEpisode,
        asset: StoredAsset,
        target: str,
        references: ReferenceSelectionPlan,
    ) -> StoredAsset:
        if asset.step_id is None:
            raise RuntimeError("待审核图片缺少Step")
        prompt = compile_image_review_prompt(
            episode.plan,
            target=target,
            reference_roles=references.semantic_keys,
            series_profile=self._series_profile,
            style_profile=self._style_profile,
        )
        generation = self._repository.get_prompt_for_step(
            asset.step_id, purpose=PromptPurpose.IMAGE
        )
        self._repository.save_prompt(
            step_id=asset.step_id,
            parent_prompt_id=generation.id,
            purpose=PromptPurpose.REVIEW,
            model=self._review_gateway.review_model,
            text=prompt,
        )
        try:
            result = self._review_gateway.review_image(
                prompt=prompt,
                image_path=asset.path,
                reference_paths=tuple(item.path for item in references.assets),
            )
        except Exception as exc:
            self._repository.record_review(
                step_id=asset.step_id,
                asset_id=asset.id,
                source="ark_visual",
                decision="pending",
                reason=f"图片语义审核异常，转人工：{type(exc).__name__}",
                warnings=[],
                evidence={"semanticReviewStatus": "pending", "semanticVerified": False},
            )
            return asset
        # 定妆图只负责人物身份和完整服装，不应因剧情场景、猫咪或道具缺席被拒绝；
        # 开场锚点才承担一人一猫、活动焦点和关键道具的画面语义。
        passed = all(
            (
                result.identity_ok,
                result.style_ok,
                result.appearance_ok,
                result.composition_ok,
                *((result.critical_props_ok,) if target == "opening_anchor" else ()),
            )
        )
        evidence = {
            "identityOk": result.identity_ok,
            "styleOk": result.style_ok,
            "appearanceOk": result.appearance_ok,
            "compositionOk": result.composition_ok,
            "criticalPropsOk": result.critical_props_ok,
            "confidence": result.confidence,
            "violations": list(result.violations),
            "observations": list(result.evidence),
            "responseId": result.response_id,
            "requestHash": result.request_hash,
        }
        if result.confidence < 0.8:
            self._repository.record_review(
                step_id=asset.step_id,
                asset_id=asset.id,
                source="ark_visual",
                decision="pending",
                reason="图片审核置信度低，转人工",
                warnings=[{"code": "image_warning", "message": item} for item in result.warnings],
                evidence=evidence,
            )
            return asset
        self._repository.commit_asset_review(
            asset_id=asset.id,
            source="ark_visual",
            decision="approved" if passed else "rejected",
            reason="图片语义审核通过" if passed else "图片存在身份、画风、构图或关键道具错误",
            warnings=[{"code": "image_warning", "message": item} for item in result.warnings],
            evidence=evidence,
        )
        return self._repository.asset_detail(asset.id)

    def _look_references(self, episode: StoredEpisode) -> ReferenceSelectionPlan:
        contextual = (
            self._style_profile.indoor_reference_key
            if episode.plan.script.style_context == "indoor"
            else self._style_profile.outdoor_reference_key
        )
        keys = (
            *self._series_profile.person_reference_keys,
            self._style_profile.line_reference_key,
            contextual,
        )
        return self._select_assets(episode, keys)

    def _anchor_references(
        self, episode: StoredEpisode, look: StoredAsset
    ) -> ReferenceSelectionPlan:
        contextual = (
            self._style_profile.indoor_reference_key
            if episode.plan.script.style_context == "indoor"
            else self._style_profile.outdoor_reference_key
        )
        keys = [str(look.semantic_key), "cat:front", contextual]
        optional = [f"element:{item.entity_key}" for item in episode.plan.script.critical_props[:1]]
        assets = [look]
        selected = self._select_assets(episode, tuple(keys[1:]), allow_missing=tuple(optional))
        assets.extend(selected.assets)
        reference_roles = [str(look.semantic_key), *selected.semantic_keys]
        optional_assets = self._select_assets(episode, tuple(optional), all_optional=True)
        assets.extend(optional_assets.assets)
        reference_roles.extend(optional_assets.semantic_keys)

        # 跨时段共享道具不能只靠 entityKey 文本维持造型。若上一时段已有批准
        # 开场锚点且两集声明了同一关键道具，就把该画面作为“只继承道具”的限定
        # 参考；人物、猫咪和场景仍分别由本集定妆、Canon 与风格图负责。
        current_keys = {item.entity_key for item in episode.plan.script.critical_props}
        slot_order = {item: index for index, item in enumerate(Slot)}
        prior_episodes = sorted(
            (
                item
                for item in self._repository.list_episodes(episode.run_id)
                if slot_order[item.plan.slot] < slot_order[episode.plan.slot]
            ),
            key=lambda item: slot_order[item.plan.slot],
            reverse=True,
        )
        handoff_role: str | None = None
        handoff_asset: StoredAsset | None = None
        for prior in prior_episodes:
            shared = current_keys & {
                item.entity_key for item in prior.plan.script.critical_props
            }
            if not shared:
                continue
            candidates = self._repository.list_assets(
                run_id=episode.run_id,
                episode_id=prior.id,
                roles=("opening_anchor",),
                statuses=("approved", "ready"),
            )
            exact = [item for item in candidates if item.episode_id == prior.id]
            if exact:
                handoff_asset = exact[-1]
                handoff_role = "handoff:" + ",".join(sorted(shared))
                break
        if handoff_asset is not None and handoff_role is not None:
            assets.append(handoff_asset)
            reference_roles.append(handoff_role)
        return ReferenceSelectionPlan(
            semantic_keys=tuple(reference_roles),
            assets=tuple(assets),
        )

    def _select_assets(
        self,
        episode: StoredEpisode,
        keys: tuple[str, ...],
        *,
        allow_missing: tuple[str, ...] = (),
        all_optional: bool = False,
    ) -> ReferenceSelectionPlan:
        candidates = self._repository.list_assets(
            run_id=episode.run_id,
            episode_id=episode.id,
            statuses=("approved", "ready"),
            semantic_keys=keys,
        )
        latest = {
            str(item.semantic_key): item
            for item in candidates
            if item.media_type == "image" and item.semantic_key is not None
        }
        required = set() if all_optional else set(keys) - set(allow_missing)
        missing = [key for key in keys if key in required and key not in latest]
        if missing:
            raise ValueError("缺少视觉参考：" + ", ".join(missing))
        selected = tuple(latest[key] for key in keys if key in latest)
        return ReferenceSelectionPlan(tuple(str(item.semantic_key) for item in selected), selected)

    def _matching_look(self, episode: StoredEpisode, signature: str) -> StoredAsset | None:
        assets = self._repository.list_assets(
            run_id=episode.run_id,
            roles=("look_reference",),
            statuses=("approved", "ready"),
        )
        matches = [item for item in assets if item.metadata.get("appearanceSignature") == signature]
        return matches[-1] if matches else None

    def _normalize_portrait(self, landed, metadata: dict):
        width, height = int(metadata["width"]), int(metadata["height"])
        ratio = width / height
        target = 9 / 16
        deviation = abs(ratio - target) / target
        if deviation > 0.02:
            raise ValueError("图片比例偏离9:16超过2%")
        if deviation <= 0.01:
            return landed, metadata
        box = _center_crop_box(width, height)
        cropped = self._asset_store.crop_local(landed.path, box=box)
        return cropped, {
            **self._probe.inspect_image(cropped.path),
            "normalizedToNineSixteen": True,
            "normalizedFromSha256": landed.sha256,
            "normalizationCropBox": list(box),
        }


def _appearance_signature(episode: StoredEpisode) -> str:
    source = f"{episode.plan.script.appearance.description}|{episode.plan.script.style_context}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _input_hash(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _override(compiled: CompiledPrompt, text: str) -> CompiledPrompt:
    value = text.strip()
    if not value:
        raise ValueError("Prompt覆盖不能为空")
    return CompiledPrompt(value, len(value), len(value.encode("utf-8")))


def _center_crop_box(width: int, height: int) -> tuple[int, int, int, int]:
    target_width = round(height * 9 / 16)
    if target_width <= width:
        left = (width - target_width) // 2
        return left, 0, left + target_width, height
    target_height = round(width * 16 / 9)
    top = (height - target_height) // 2
    return 0, top, width, top + target_height
