"""交付包的PostgreSQL事务写入。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ...domain.workflow import RunStatus, transition_run
from .models import DeliveryItem, DeliveryPackage, ProductionRun
from .query_repository import required_record as _required


class DeliveryPersistenceMixin:
    """保存完整交付目录对应的关系记录；不创建或复制媒体文件。"""

    def next_delivery_revision(self, run_id: uuid.UUID) -> int:
        with self._sessions() as session:  # type: ignore[attr-defined]
            revisions = session.execute(
                select(DeliveryPackage.revision).where(DeliveryPackage.production_run_id == run_id)
            ).scalars()
            return max(revisions, default=0) + 1

    def save_delivery(
        self,
        *,
        run_id: uuid.UUID,
        revision: int,
        local_path: Path,
        manifest_sha256: str,
        items: tuple[dict[str, Any], ...],
    ) -> uuid.UUID:
        # 事务只在三个文件和Manifest均已原子落盘后开始，数据库不会指向半成品。
        with self._sessions.begin() as session:  # type: ignore[attr-defined]
            package = DeliveryPackage(
                production_run_id=run_id,
                revision=revision,
                status="delivered",
                local_path=str(local_path),
                manifest_sha256=manifest_sha256,
            )
            session.add(package)
            session.flush()
            session.add_all(
                DeliveryItem(
                    delivery_package_id=package.id,
                    episode_id=uuid.UUID(str(item["episodeId"])),
                    asset_id=uuid.UUID(str(item["assetId"])),
                    slot=str(item["slot"]),
                    sort_order=int(item["sortOrder"]),
                    filename=str(item["filename"]),
                    sha256=str(item["sha256"]),
                )
                for item in items
            )
            run = _required(session, ProductionRun, run_id)
            run.status = transition_run(
                RunStatus(run.status),
                RunStatus.DELIVERED,
            ).value
            return package.id
