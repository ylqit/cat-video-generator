from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ..config import RuntimeSettings
from ..media import probe_video
from ..models import DailyLifePack, DailySlot, EpisodeVariant, GenerationJob, MediaAsset
from ..state import transition_pack, transition_slot, transition_variant
from .errors import OrchestrationError


def recheck_video_asset(
    session_factory: sessionmaker[Session],
    settings: RuntimeSettings,
    asset_id: uuid.UUID,
) -> dict[str, Any]:
    if settings.ffprobe_path is None:
        raise OrchestrationError("ffprobe is required for media QC.")

    with session_factory() as session:
        asset = session.get(MediaAsset, asset_id)
        if asset is None or asset.asset_kind != "final_video":
            raise OrchestrationError(
                f"Final video asset {asset_id} does not exist."
            )
        job = session.get_one(GenerationJob, asset.generation_job_id)
        expected_duration_ms = job.request_snapshot_json.get("durationMs")
        expected_resolution = job.request_snapshot_json.get("resolution")
        if not isinstance(expected_duration_ms, int):
            raise OrchestrationError(
                "Video job snapshot has no valid durationMs."
            )
        if not isinstance(expected_resolution, str):
            raise OrchestrationError(
                "Video job snapshot has no valid resolution."
            )
        media_path = Path(asset.storage_path)

    probe = probe_video(
        media_path,
        ffprobe_path=settings.ffprobe_path,
        expected_duration_ms=expected_duration_ms,
        expected_resolution=expected_resolution,
    )

    with session_factory.begin() as session:
        asset = session.get_one(MediaAsset, asset_id)
        variant = session.get_one(EpisodeVariant, asset.episode_variant_id)
        slot = session.get_one(DailySlot, variant.daily_slot_id)
        asset.container = probe.container
        asset.video_codec = probe.video_codec
        asset.audio_codec = probe.audio_codec
        asset.width = probe.width
        asset.height = probe.height
        asset.duration_ms = probe.duration_ms
        asset.has_audio = probe.has_audio
        asset.qc_status = probe.qc_status
        asset.qc_report_json = probe.report

        if probe.qc_status == "passed" and slot.status == "failed":
            if slot.last_error != "media_qc_failed":
                raise OrchestrationError(
                    "Only a Slot failed by media QC can be recovered by "
                    "recheck-media."
                )
            transition_slot(slot, "media_qc")
            if variant.status == "failed":
                transition_variant(variant, "active")
            transition_slot(slot, "content_review")
            slot.last_error = None
            variant.last_error = None
            pack = session.get_one(DailyLifePack, slot.daily_life_pack_id)
            if pack.status == "failed":
                transition_pack(pack, "rendering")

        return {
            "assetId": str(asset.id),
            "qcStatus": asset.qc_status,
            "failures": probe.report["failures"],
            "width": asset.width,
            "height": asset.height,
            "durationMs": asset.duration_ms,
            "slotStatus": slot.status,
            "path": asset.storage_path,
        }
