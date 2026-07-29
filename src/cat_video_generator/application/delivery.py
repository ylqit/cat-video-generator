"""三时段本地交付用例。"""

from __future__ import annotations

import uuid
from typing import Any

from ..domain.contracts import Slot
from ..domain.workflow import EpisodeStatus, RunStatus
from .ports import AssetStore, WorkflowRepository


class DeliveryService:
    """验证三个ready视频并原子构建1、2、3交付包。"""

    def __init__(
        self,
        *,
        repository: WorkflowRepository,
        asset_store: AssetStore,
    ) -> None:
        self._repository = repository
        self._asset_store = asset_store

    def deliver(self, run_id: uuid.UUID) -> dict[str, Any]:
        """校验三条 ready 视频并原子生成 1、2、3 本地交付包。"""

        run = self._repository.get_run(run_id)
        if run.status != RunStatus.READY.value:
            raise ValueError("只有ready Run可以构建交付包")
        episodes = self._repository.list_episodes(run_id)
        if len(episodes) != 3 or any(
            episode.status is not EpisodeStatus.READY
            or episode.selected_video_asset_id is None
            for episode in episodes
        ):
            raise ValueError("交付要求三个Episode均选择ready视频")
        assets = tuple(
            (
                Slot(episode.plan.slot),
                self._repository.asset_detail(episode.selected_video_asset_id),
            )
            for episode in episodes
            if episode.selected_video_asset_id is not None
        )
        revision = self._repository.next_delivery_revision(run_id)
        build = self._asset_store.build_delivery(
            content_date=run.content_date,
            run_id=run_id,
            revision=revision,
            items=assets,
        )
        manifest_by_asset = {str(item["assetId"]): item for item in build.items}
        database_items = tuple(
            {
                **manifest_by_asset[str(asset.id)],
                "episodeId": str(episode.id),
            }
            for episode, (_, asset) in zip(episodes, assets, strict=True)
        )
        package_id = self._repository.save_delivery(
            run_id=run_id,
            revision=revision,
            local_path=build.path,
            manifest_sha256=build.manifest_sha256,
            items=database_items,
        )
        return {
            "deliveryPackageId": str(package_id),
            "revision": revision,
            "localPath": str(build.path),
            "manifestSha256": build.manifest_sha256,
        }
