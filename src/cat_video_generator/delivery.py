from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .config import RuntimeSettings
from .contracts import validate_delivery_manifest
from .models import (
    ContinuityEvent,
    DailyLifePack,
    DailySlot,
    DeliveryItem,
    DeliveryPackage,
    EpisodeVariant,
    MediaAsset,
)
from .state import transition_pack


class DeliveryError(RuntimeError):
    """Raised when three reviewed videos cannot form an immutable delivery."""


def deliver_life_pack(
    session_factory: sessionmaker[Session],
    settings: RuntimeSettings,
    life_pack_id: str,
) -> dict[str, Any]:
    with session_factory() as session:
        pack = session.execute(
            select(DailyLifePack)
            .where(DailyLifePack.life_pack_id == life_pack_id)
            .order_by(DailyLifePack.plan_revision.desc())
            .limit(1)
        ).scalar_one_or_none()
        if pack is None:
            raise DeliveryError(f"LifePack {life_pack_id!r} does not exist.")
        existing_delivery = session.execute(
            select(DeliveryPackage)
            .where(
                DeliveryPackage.daily_life_pack_id == pack.id,
                DeliveryPackage.status == "delivered",
            )
            .order_by(DeliveryPackage.delivery_revision.desc())
            .limit(1)
        ).scalar_one_or_none()
        if pack.status == "delivered" and existing_delivery is not None:
            return _delivery_result(existing_delivery, reused=True)
        if pack.status != "ready":
            raise DeliveryError(
                f"LifePack must be ready before delivery, not {pack.status!r}."
            )
        rows = _selected_media_rows(session, pack)
        delivery_revision = (
            session.scalar(
                select(
                    func.coalesce(
                        func.max(DeliveryPackage.delivery_revision),
                        0,
                    )
                ).where(DeliveryPackage.daily_life_pack_id == pack.id)
            )
            + 1
        )
        pack_snapshot = {
            "id": pack.id,
            "lifePackId": pack.life_pack_id,
            "date": pack.date.isoformat(),
            "planRevision": pack.plan_revision,
        }

    output_parent = (
        settings.delivery_root.expanduser().resolve()
        / pack_snapshot["date"]
        / life_pack_id
    )
    target = output_parent / f"delivery-r{delivery_revision}"
    created_at = datetime.now(ZoneInfo("Asia/Shanghai"))
    delivery_id = f"delivery-{life_pack_id}-r{delivery_revision}"
    manifest = {
        "schemaVersion": 1,
        "deliveryId": delivery_id,
        "lifePackId": life_pack_id,
        "date": pack_snapshot["date"],
        "planRevision": pack_snapshot["planRevision"],
        "deliveryRevision": delivery_revision,
        "createdAt": created_at.isoformat(),
        "degraded": any(row["variant"].role == "content_fallback" for row in rows),
        "videos": [
            _manifest_video_item(row)
            for row in rows
        ],
    }
    validate_delivery_manifest(manifest)
    manifest_bytes = (
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()

    output_parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        _validate_existing_target(target, manifest)
        existing_bytes = (target / "manifest.json").read_bytes()
        manifest_sha256 = hashlib.sha256(existing_bytes).hexdigest()
    else:
        building = output_parent / f".building-{uuid.uuid4().hex}"
        building.mkdir()
        try:
            for row in rows:
                source = Path(row["asset"].storage_path)
                destination = building / row["fileName"]
                shutil.copy2(source, destination)
                if _sha256_file(destination) != row["asset"].sha256:
                    raise DeliveryError(
                        f"Copied delivery media hash mismatch: {row['fileName']}"
                    )
            manifest_path = building / "manifest.json"
            with manifest_path.open("xb") as output:
                output.write(manifest_bytes)
                output.flush()
                os.fsync(output.fileno())
            os.replace(building, target)
        except Exception:
            if building.exists():
                shutil.rmtree(building)
            raise

    with session_factory.begin() as session:
        pack = session.execute(
            select(DailyLifePack)
            .where(DailyLifePack.id == pack_snapshot["id"])
            .with_for_update()
        ).scalar_one()
        existing_delivery = session.execute(
            select(DeliveryPackage).where(
                DeliveryPackage.daily_life_pack_id == pack.id,
                DeliveryPackage.delivery_revision == delivery_revision,
            )
        ).scalar_one_or_none()
        if existing_delivery is not None:
            if existing_delivery.manifest_sha256 != manifest_sha256:
                raise DeliveryError(
                    "Delivery revision exists with a different manifest hash."
                )
            return _delivery_result(existing_delivery, reused=True)
        delivery = DeliveryPackage(
            daily_life_pack_id=pack.id,
            delivery_revision=delivery_revision,
            status="delivered",
            root_path=str(target),
            manifest_path=str(target / "manifest.json"),
            manifest_sha256=manifest_sha256,
            delivered_at=datetime.now(UTC),
        )
        session.add(delivery)
        session.flush()
        for row in rows:
            slot = session.get_one(DailySlot, row["slot"].id)
            variant = session.get_one(EpisodeVariant, row["variant"].id)
            asset = session.get_one(MediaAsset, row["asset"].id)
            if (
                slot.status != "ready"
                or slot.selected_variant_id != variant.id
                or variant.status != "ready"
                or asset.review_status != "approved"
                or asset.qc_status != "passed"
            ):
                raise DeliveryError(
                    f"Slot {slot.slot} changed while delivery was being built."
                )
            session.add(
                DeliveryItem(
                    delivery_package_id=delivery.id,
                    sort_order=slot.sort_order,
                    slot=slot.slot,
                    episode_variant_id=variant.id,
                    media_asset_id=asset.id,
                    file_name=row["fileName"],
                )
            )
            for write_index, state_write in enumerate(
                variant.episode_spec_json["stateWrites"],
                start=1,
            ):
                session.add(
                    ContinuityEvent(
                        event_id=(
                            f"{delivery_id}:{slot.sort_order}:"
                            f"{write_index}:{state_write['path']}"
                        ),
                        delivery_package_id=delivery.id,
                        episode_variant_id=variant.id,
                        sort_order=slot.sort_order,
                        scope=state_write["scope"],
                        entity_id=life_pack_id,
                        operation=state_write["operation"],
                        path=state_write["path"],
                        value=state_write["value"],
                        status="applied",
                        reason=f"delivered:{variant.episode_id}",
                    )
                )
        pack.is_degraded = manifest["degraded"]
        transition_pack(pack, "delivered")
        pack.delivered_at = datetime.now(UTC)
        session.flush()
        return _delivery_result(delivery, reused=False)


def _selected_media_rows(
    session: Session,
    pack: DailyLifePack,
) -> list[dict[str, Any]]:
    slots = session.execute(
        select(DailySlot)
        .where(DailySlot.daily_life_pack_id == pack.id)
        .order_by(DailySlot.sort_order)
    ).scalars().all()
    if len(slots) != 3 or any(
        slot.status != "ready" or slot.selected_variant_id is None
        for slot in slots
    ):
        raise DeliveryError("Delivery requires exactly three ready selected Slots.")
    rows: list[dict[str, Any]] = []
    for slot in slots:
        variant = session.get_one(EpisodeVariant, slot.selected_variant_id)
        asset = session.execute(
            select(MediaAsset)
            .where(
                MediaAsset.episode_variant_id == variant.id,
                MediaAsset.asset_kind == "final_video",
                MediaAsset.qc_status == "passed",
                MediaAsset.review_status == "approved",
            )
            .order_by(MediaAsset.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if asset is None:
            raise DeliveryError(
                f"Slot {slot.slot} has no approved final video."
            )
        source = Path(asset.storage_path)
        if not source.is_file() or _sha256_file(source) != asset.sha256:
            raise DeliveryError(
                f"Slot {slot.slot} final video is missing or corrupted."
            )
        rows.append(
            {
                "slot": slot,
                "variant": variant,
                "asset": asset,
                "fileName": f"{slot.sort_order:02d}-{slot.slot}.mp4",
            }
        )
    return rows


def _manifest_video_item(row: dict[str, Any]) -> dict[str, Any]:
    slot: DailySlot = row["slot"]
    variant: EpisodeVariant = row["variant"]
    asset: MediaAsset = row["asset"]
    if None in (
        asset.duration_ms,
        asset.video_codec,
        asset.audio_codec,
        asset.width,
        asset.height,
    ):
        raise DeliveryError(f"Slot {slot.slot} media metadata is incomplete.")
    return {
        "sortOrder": slot.sort_order,
        "slot": slot.slot,
        "episodeId": variant.episode_id,
        "title": variant.episode_spec_json["title"],
        "fileName": row["fileName"],
        "relativePath": f"./{row['fileName']}",
        "sha256": asset.sha256,
        "byteSize": asset.byte_size,
        "durationMs": asset.duration_ms,
        "container": "mp4",
        "videoCodec": asset.video_codec,
        "audioCodec": asset.audio_codec,
        "width": asset.width,
        "height": asset.height,
        "hasAudio": asset.has_audio,
        "renderRevision": variant.active_render_revision,
        "selectedVariant": variant.role,
        "qcStatus": asset.qc_status,
    }


def _validate_existing_target(
    target: Path,
    expected_manifest: dict[str, Any],
) -> None:
    manifest_path = target / "manifest.json"
    try:
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliveryError(
            f"Existing delivery target is incomplete: {target}"
        ) from exc
    expected_identity = (
        expected_manifest["lifePackId"],
        expected_manifest["planRevision"],
        expected_manifest["deliveryRevision"],
    )
    actual_identity = (
        existing.get("lifePackId"),
        existing.get("planRevision"),
        existing.get("deliveryRevision"),
    )
    if actual_identity != expected_identity:
        raise DeliveryError(
            "Existing delivery target belongs to a different revision."
        )
    existing_videos = existing.get("videos", [])
    expected_videos = expected_manifest["videos"]
    existing_selection = [
        (
            item.get("sortOrder"),
            item.get("episodeId"),
            item.get("sha256"),
        )
        for item in existing_videos
    ]
    expected_selection = [
        (item["sortOrder"], item["episodeId"], item["sha256"])
        for item in expected_videos
    ]
    if existing_selection != expected_selection:
        raise DeliveryError(
            "Existing delivery target contains a different selected media set."
        )
    for item in existing_videos:
        path = target / item["fileName"]
        if not path.is_file() or _sha256_file(path) != item["sha256"]:
            raise DeliveryError(
                f"Existing delivery file failed hash verification: {path.name}"
            )


def _sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _delivery_result(
    delivery: DeliveryPackage,
    *,
    reused: bool,
) -> dict[str, Any]:
    return {
        "deliveryRevision": delivery.delivery_revision,
        "status": delivery.status,
        "rootPath": delivery.root_path,
        "manifestPath": delivery.manifest_path,
        "manifestSha256": delivery.manifest_sha256,
        "reused": reused,
    }
