"""Canon、Episode 参考素材导入与人工媒体审核。"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from ..domain.pipeline import PlanningMode
from ..domain.visual_profiles import DEFAULT_STYLE_PROFILE
from ..domain.workflow import EpisodeStatus, RunStatus
from .ports import AssetStore, MediaProbe, ProductionStore


class AssetService:
    """本地不可变资产及人工审核的业务所有者。"""

    def __init__(
        self,
        *,
        repository: ProductionStore,
        asset_store: AssetStore,
        media_probe: MediaProbe,
    ) -> None:
        self._repository = repository
        self._asset_store = asset_store
        self._probe = media_probe

    def import_canon(
        self,
        *,
        role: str,
        path: Path,
        semantic_key: str,
        view: str | None,
    ) -> dict[str, Any]:
        if role not in {"person", "cat", "style"}:
            raise ValueError("Canon role 必须是 person、cat 或 style")
        _validate_semantic_key(role, semantic_key, scope="canon")
        _validate_canon_view(role, semantic_key, view)
        landed = self._asset_store.import_local(path)
        metadata = {
            **self._probe.inspect_image(landed.path),
            "referenceView": view,
            "semanticKey": semantic_key,
        }
        asset = self._repository.save_asset(
            run_id=None,
            episode_id=None,
            step_id=None,
            role=role,
            semantic_key=semantic_key,
            scope="canon",
            status="approved",
            media_type="image",
            landed=landed,
            metadata=metadata,
        )
        return {
            "assetId": str(asset.id),
            "role": role,
            "semanticKey": asset.semantic_key,
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
        semantic_key: str,
        view: str | None,
    ) -> dict[str, Any]:
        source = self._repository.asset_detail(source_asset_id)
        if source.scope != "canon" or source.status not in {"approved", "ready"}:
            raise ValueError("派生裁剪来源必须是已批准 Canon")
        if source.role != role or role not in {"person", "cat", "style"}:
            raise ValueError("派生角色必须与来源 Canon 一致")
        _validate_semantic_key(role, semantic_key, scope="canon")
        _validate_canon_view(role, semantic_key, view)
        source_meta = self._probe.inspect_image(source.path)
        if box is None:
            if role in {"style", "person"}:
                raise ValueError("人物新参考和 style 必须显式提供裁剪框")
            box = (0, 0, int(source_meta["width"]) // 3, int(source_meta["height"]))
        landed = self._asset_store.crop_local(source.path, box=box)
        metadata = {
            **self._probe.inspect_image(landed.path),
            "derivedFromAssetId": str(source.id),
            "pixelCrop": list(box),
            "referenceView": view,
            "subjectFree": subject_free if role == "style" else False,
            "semanticKey": semantic_key,
        }
        asset = self._repository.save_asset(
            run_id=None,
            episode_id=None,
            step_id=None,
            role=role,
            semantic_key=semantic_key,
            scope="canon",
            status="approved",
            media_type="image",
            landed=landed,
            metadata=metadata,
        )
        return {
            "assetId": str(asset.id),
            "role": role,
            "semanticKey": asset.semantic_key,
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
        semantic_key: str,
    ) -> dict[str, Any]:
        if role not in {"element", "scene"}:
            raise ValueError("Episode 参考 role 必须是 element 或 scene")
        _validate_semantic_key(role, semantic_key, scope="episode")
        episode = self._repository.episode_detail(episode_id)
        landed = self._asset_store.import_local(path)
        metadata = self._probe.inspect_image(landed.path)
        asset = self._repository.save_asset(
            run_id=uuid.UUID(episode["runId"]),
            episode_id=episode_id,
            step_id=None,
            role=role,
            semantic_key=semantic_key,
            scope="episode",
            status="approved",
            media_type="image",
            landed=landed,
            metadata=metadata,
        )
        return {
            "assetId": str(asset.id),
            "role": role,
            "semanticKey": asset.semantic_key,
            "localPath": str(asset.path),
            "sha256": asset.sha256,
            "episodeId": str(episode_id),
            "mediaType": "image",
            "metadata": metadata,
        }

    def review_asset(
        self,
        asset_id: uuid.UUID,
        *,
        approve: bool,
        reason: str,
    ) -> dict[str, Any]:
        """原子提交单个定妆、开场锚点或最终视频的人工决定。"""

        decision = "approved" if approve else "rejected"
        asset = self._repository.asset_detail(asset_id)
        result = self._repository.commit_asset_review(
            asset_id=asset_id,
            source="human",
            decision=decision,
            reason=reason,
            warnings=[],
            evidence={},
        )
        if (
            approve
            and asset.role == "video"
            and asset.run_id is not None
        ):
            episodes = self._repository.list_episodes(asset.run_id)
            if len(episodes) == 3 and all(
                item.status is EpisodeStatus.READY for item in episodes
            ) and (
                self._repository.get_pipeline_settings(asset.run_id).planning_mode
                is PlanningMode.AUTO_DAY
            ):
                self._repository.set_run_status(asset.run_id, RunStatus.READY)
        return {
            "reviewId": str(result.review_id),
            "assetId": str(asset_id),
            "decision": decision,
            "idempotent": result.idempotent,
        }


_SEMANTIC_KEY = re.compile(r"^[a-z][a-z0-9_]*:[a-z0-9][a-z0-9_-]{0,119}$")


def _canon_views(role: str) -> frozenset[str]:
    if role == "person":
        return frozenset({"headshot", "fullbody", "front", "side", "back"})
    if role == "cat":
        return frozenset({"front", "side", "back"})
    return frozenset()


def _validate_canon_view(role: str, semantic_key: str, view: str | None) -> None:
    if role in {"person", "cat"}:
        allowed = _canon_views(role)
        if view not in allowed:
            raise ValueError(f"{role} Canon 视角必须是 {', '.join(sorted(allowed))}")
        if semantic_key != f"{role}:{view}":
            raise ValueError("人物和猫咪 semantic_key 必须与 role、view 一致")
        return
    if view is not None:
        raise ValueError("style Canon 不能声明人物视角")
    if semantic_key not in DEFAULT_STYLE_PROFILE.reference_keys:
        raise ValueError("style Canon 必须使用当前定稿画风语义键")


def _validate_semantic_key(role: str, semantic_key: str, *, scope: str) -> None:
    if not _SEMANTIC_KEY.fullmatch(semantic_key):
        raise ValueError("semantic_key 必须使用 type:value 格式")
    prefix = semantic_key.split(":", 1)[0]
    allowed = {
        "person": {"person"},
        "cat": {"cat"},
        "style": {"style"},
        "element": {"element"},
        "scene": {"scene"},
    }[role]
    if prefix not in allowed:
        raise ValueError(f"{role} 资产不能使用 {semantic_key}")
    if scope == "canon" and semantic_key.startswith("legacy:"):
        raise ValueError("新 Canon 不能使用 legacy 语义键")
