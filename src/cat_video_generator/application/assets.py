"""Canon导入和人工媒体审核用例。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from ..domain.workflow import EpisodeStatus, RunStatus, StepStatus
from .ports import AssetStore, MediaProbe, StoredAsset, WorkflowRepository


class AssetService:
    """本地Canon资产与人工审核的业务所有者。"""

    def __init__(
        self,
        *,
        repository: WorkflowRepository,
        asset_store: AssetStore,
        media_probe: MediaProbe,
    ) -> None:
        self._repository = repository
        self._asset_store = asset_store
        self._probe = media_probe

    def import_canon(self, *, role: str, path: Path) -> dict[str, Any]:
        """导入长期人物、猫咪或画风Canon。"""

        if role not in {"person", "cat", "style"}:
            raise ValueError("Canon role必须是person、cat或style")
        landed = self._asset_store.import_local(path)
        metadata = self._probe.inspect_image(landed.path)
        asset = self._repository.save_asset(
            run_id=None,
            episode_id=None,
            step_id=None,
            role=role,
            scope="canon",
            status="approved",
            media_type="image",
            landed=landed,
            metadata=metadata,
        )
        return {
            "assetId": str(asset.id),
            "role": role,
            "localPath": str(asset.path),
            "sha256": asset.sha256,
        }

    def derive_canon_crop(
        self,
        *,
        source_asset_id: uuid.UUID,
        role: str,
        box: tuple[int, int, int, int] | None,
        subject_free: bool,
    ) -> dict[str, Any]:
        """从已批准Canon确定性裁出单视图或无主体画风参考。"""

        source = self._repository.asset_detail(source_asset_id)
        if source.scope != "canon" or source.status not in {"approved", "ready"}:
            raise ValueError("派生裁剪的来源必须是已批准Canon")
        if source.role != role or role not in {"person", "cat", "style"}:
            raise ValueError("派生角色必须与来源Canon角色一致")
        source_meta = self._probe.inspect_image(source.path)
        if box is None:
            if role == "style":
                raise ValueError("style必须显式提供无主体区域的裁剪框")
            box = (0, 0, int(source_meta["width"]) // 3, int(source_meta["height"]))
        landed = self._asset_store.crop_local(source.path, box=box)
        metadata = {
            **self._probe.inspect_image(landed.path),
            "derivedFromAssetId": str(source.id),
            "pixelCrop": list(box),
            "referenceView": "front" if role in {"person", "cat"} else None,
            "subjectFree": subject_free if role == "style" else False,
        }
        asset = self._repository.save_asset(
            run_id=None,
            episode_id=None,
            step_id=None,
            role=role,
            scope="canon",
            status="approved",
            media_type="image",
            landed=landed,
            metadata=metadata,
        )
        return {
            "assetId": str(asset.id),
            "role": role,
            "localPath": str(asset.path),
            "sha256": asset.sha256,
            "metadata": metadata,
        }

    def import_episode_reference(
        self,
        *,
        episode_id: uuid.UUID,
        role: str,
        path: Path,
    ) -> dict[str, Any]:
        """导入仅供一个Episode使用的场景、元素、动作或声音参考。"""

        media_types = {
            "element": "image",
            "scene": "image",
            "motion": "video",
            "atmosphere": "audio",
        }
        if role not in media_types:
            raise ValueError("Episode参考role必须是element、scene、motion或atmosphere")
        episode = self._repository.episode_detail(episode_id)
        landed = self._asset_store.import_local(path)
        media_type = media_types[role]
        metadata = (
            self._probe.inspect_image(landed.path)
            if media_type == "image"
            else self._probe.inspect_reference(
                landed.path,
                media_type=media_type,
            )
        )
        asset = self._repository.save_asset(
            run_id=uuid.UUID(episode["runId"]),
            episode_id=episode_id,
            step_id=None,
            role=role,
            scope="episode",
            status="approved",
            media_type=media_type,
            landed=landed,
            metadata=metadata,
        )
        return {
            "assetId": str(asset.id),
            "episodeId": str(episode_id),
            "role": role,
            "mediaType": media_type,
            "localPath": str(asset.path),
            "sha256": asset.sha256,
            "metadata": metadata,
        }

    def review_asset(
        self,
        asset_id: uuid.UUID,
        *,
        approve: bool,
        reason: str,
    ) -> dict[str, Any]:
        """记录人工决定；拒绝后只保留审计，不自动再次付费。"""

        asset = self._repository.asset_detail(asset_id)
        if asset.step_id is None:
            raise ValueError("该资产没有可审核的生产步骤")
        decision = "approved" if approve else "rejected"
        review_id = self._repository.record_review(
            step_id=asset.step_id,
            asset_id=asset.id,
            source="human",
            decision=decision,
            reason=reason,
            warnings=[],
            evidence={},
        )
        self._repository.set_asset_status(
            asset.id,
            "ready" if approve else "rejected",
        )
        if asset.episode_id is not None:
            if asset.role == "video":
                self._apply_video_decision(asset, approve)
            else:
                self._apply_image_decision(asset, approve)
        return {
            "reviewId": str(review_id),
            "assetId": str(asset.id),
            "decision": decision,
        }

    def _apply_image_decision(
        self,
        asset: StoredAsset,
        approve: bool,
    ) -> None:
        """结束manual关键帧审核；拒绝不会自动再次产生图片费用。"""

        assert asset.step_id is not None
        step = self._repository.get_step(asset.step_id)
        if step.status is not StepStatus.AWAITING_REVIEW:
            raise ValueError("该图片步骤当前不在等待人工审核状态")
        self._repository.set_step_status(
            step.id,
            StepStatus.SUCCEEDED if approve else StepStatus.FAILED,
        )
        if not approve:
            assert asset.episode_id is not None
            self._repository.set_episode_status(
                asset.episode_id,
                EpisodeStatus.FAILED,
            )

    def _apply_video_decision(
        self,
        asset: StoredAsset,
        approve: bool,
    ) -> None:
        assert asset.episode_id is not None
        if not approve:
            self._repository.set_episode_status(
                asset.episode_id,
                EpisodeStatus.FAILED,
            )
            return
        self._repository.select_video_asset(
            episode_id=asset.episode_id,
            asset_id=asset.id,
        )
        assert asset.run_id is not None
        episodes = self._repository.list_episodes(asset.run_id)
        if all(item.status is EpisodeStatus.READY for item in episodes):
            self._repository.set_run_status(asset.run_id, RunStatus.READY)
