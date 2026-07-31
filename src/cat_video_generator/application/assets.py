"""Canon导入和人工媒体审核用例。"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from ..domain.workflow import EpisodeStatus, RunStatus
from .ports import AssetStore, MediaProbe, WorkflowRepository


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

    def import_canon(
        self,
        *,
        role: str,
        path: Path,
        semantic_key: str,
        view: str | None,
    ) -> dict[str, Any]:
        """导入长期人物、猫咪或画风Canon。"""

        if role not in {"person", "cat", "style"}:
            raise ValueError("Canon role必须是person、cat或style")
        _validate_semantic_key(role, semantic_key, scope="canon")
        if role in {"person", "cat"}:
            if view not in {"front", "side", "back"}:
                raise ValueError("人物和猫咪Canon必须提供front、side或back视角")
            if semantic_key != f"{role}:{view}":
                raise ValueError("人物和猫咪semantic_key必须与role和view一致")
        elif view is not None:
            raise ValueError("style Canon不能声明人物视角")
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
            "semanticKey": semantic_key,
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
        """从已批准Canon确定性裁出单视图或无主体画风参考。"""

        source = self._repository.asset_detail(source_asset_id)
        if source.scope != "canon" or source.status not in {"approved", "ready"}:
            raise ValueError("派生裁剪的来源必须是已批准Canon")
        if source.role != role or role not in {"person", "cat", "style"}:
            raise ValueError("派生角色必须与来源Canon角色一致")
        _validate_semantic_key(role, semantic_key, scope="canon")
        if role in {"person", "cat"}:
            if view not in {"front", "side", "back"}:
                raise ValueError("人物和猫咪裁片必须声明front、side或back")
            if semantic_key != f"{role}:{view}":
                raise ValueError("裁片semantic_key必须与role和view一致")
        elif view is not None:
            raise ValueError("style裁片不能声明人物视角")
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
            "semanticKey": semantic_key,
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
        """导入仅供一个Episode使用的场景、元素、动作或声音参考。"""

        media_types = {
            "element": "image",
            "scene": "image",
            "motion": "video",
            "atmosphere": "audio",
        }
        if role not in media_types:
            raise ValueError("Episode参考role必须是element、scene、motion或atmosphere")
        _validate_semantic_key(role, semantic_key, scope="episode")
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
            semantic_key=semantic_key,
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
            "semanticKey": semantic_key,
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

        decision = "approved" if approve else "rejected"
        result = self._repository.commit_asset_review(
            asset_id=asset_id,
            source="human",
            decision=decision,
            reason=reason,
            warnings=[],
            evidence={},
        )
        asset = self._repository.asset_detail(asset_id)
        if (
            approve
            and asset.role == "video"
            and asset.run_id is not None
            and all(
                item.status is EpisodeStatus.READY
                for item in self._repository.list_episodes(asset.run_id)
            )
        ):
            self._repository.set_run_status(asset.run_id, RunStatus.READY)
        return {
            "reviewId": str(result.review_id),
            "assetId": str(asset_id),
            "decision": decision,
            "idempotent": result.idempotent,
        }


_SEMANTIC_KEY = re.compile(r"^[a-z][a-z0-9_]*:[a-z0-9][a-z0-9_-]{0,119}$")


def _validate_semantic_key(role: str, semantic_key: str, *, scope: str) -> None:
    """阻止模糊role重新成为资产选择条件。"""

    if not _SEMANTIC_KEY.fullmatch(semantic_key):
        raise ValueError("semantic_key必须使用type:value格式")
    prefix = semantic_key.split(":", 1)[0]
    allowed = {
        "person": {"person"},
        "cat": {"cat"},
        "style": {"style"},
        "element": {"element"},
        "scene": {"scene"},
        "motion": {"motion"},
        "atmosphere": {"atmosphere"},
    }[role]
    if prefix not in allowed:
        raise ValueError(f"{role}资产不能使用{semantic_key}")
    if scope == "canon" and semantic_key.startswith("legacy:"):
        raise ValueError("新Canon不能使用legacy语义键")
