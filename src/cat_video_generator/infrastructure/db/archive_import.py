"""把经过校验的旧 V5 归档映射为精简八表记录。

这里只做一次性的字段映射，不读取文件、不删除 Schema，也不参与当前生产流程。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Connection

from .models import (
    Asset,
    Episode,
    ProductionRun,
    PromptRecord,
    Review,
    WorkflowStep,
)


def import_archive(
    connection: Connection,
    bundle: dict[str, Any],
    *,
    archive_format: str,
) -> None:
    """在已创建的新 Schema 事务内导入 V5 与 Canon 只读记录。"""

    legacy = bundle["legacy"]
    run_ids = {str(row["id"]) for row in legacy["productionRuns"]}
    slots = {str(row["id"]): row for row in legacy["slots"]}
    variants = {str(row["id"]): row for row in legacy["variants"]}
    episode_ids = {variant_id: uuid.UUID(variant_id) for variant_id in variants}
    connection.execute(
        ProductionRun.__table__.insert(),
        [
            {
                "id": uuid.UUID(str(row["id"])),
                "content_date": date.fromisoformat(str(row["date"])),
                "theme": (
                    row["source_json"].get("dayContext", {}).get("theme")
                    or row["life_pack_id"]
                ),
                "context_json": {
                    "legacyLifePackId": row["life_pack_id"],
                    "legacyPlanRevision": row["plan_revision"],
                    "legacyV5Plan": row["source_json"],
                    "archiveFormat": archive_format,
                },
                "plan_json": None,
                "selected_candidate": None,
                "status": "archived",
                "archived_source": "legacy-v5",
            }
            for row in legacy["productionRuns"]
        ],
    )
    connection.execute(
        Episode.__table__.insert(),
        [
            {
                "id": episode_ids[variant_id],
                "production_run_id": uuid.UUID(
                    str(slots[str(row["daily_slot_id"])]["daily_life_pack_id"])
                ),
                "slot": slots[str(row["daily_slot_id"])]["slot"],
                "sort_order": slots[str(row["daily_slot_id"])]["sort_order"],
                "title": (row["episode_spec_json"].get("title") or row["episode_id"]),
                "script_json": row["episode_spec_json"],
                "video_input_mode": _legacy_video_input_mode(row["render_plan_json"]),
                "status": "archived",
            }
            for variant_id, row in variants.items()
            if str(slots[str(row["daily_slot_id"])]["daily_life_pack_id"]) in run_ids
        ],
    )
    connection.execute(
        WorkflowStep.__table__.insert(),
        [
            {
                "id": uuid.UUID(str(row["id"])),
                "production_run_id": _run_id_for_variant(
                    row["episode_variant_id"],
                    variants,
                    slots,
                ),
                "episode_id": episode_ids[str(row["episode_variant_id"])],
                "kind": "video" if row["job_type"] == "video" else "image",
                "status": "archived",
                "attempt": row["attempt_no"],
                "idempotency_key": row["idempotency_key"],
                "provider": row["provider"],
                "model": row["request_snapshot_json"].get("model"),
                "provider_task_id": row["provider_task_id"],
                "input_hash": row["normalized_input_hash"],
                "request_summary_json": {
                    "legacyJobType": row["job_type"],
                    "legacyStatus": row["status"],
                    "legacyRequest": row["request_snapshot_json"],
                },
                "error_json": {
                    "code": row["error_code"],
                    "message": row["error_message"],
                },
                "submitted_at": _as_datetime(row["submitted_at"]),
                "completed_at": _as_datetime(row["completed_at"]),
            }
            for row in legacy["jobs"]
        ],
    )
    _import_prompts(connection, legacy["jobs"])
    _import_assets(connection, legacy, episode_ids)


def _import_prompts(
    connection: Connection,
    jobs: list[dict[str, Any]],
) -> None:
    prompts = [
        (
            row,
            row["request_snapshot_json"].get("videoPrompt")
            or row["request_snapshot_json"].get("prompt"),
        )
        for row in jobs
    ]
    records = [
        {
            "id": uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"cat-video:legacy-prompt:{row['id']}",
            ),
            "step_id": uuid.UUID(str(row["id"])),
            "purpose": "video" if row["job_type"] == "video" else "image",
            "model": row["request_snapshot_json"].get(
                "model",
                "legacy-unknown",
            ),
            "prompt_text": prompt,
            "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "char_count": len(prompt),
            "utf8_bytes": len(prompt.encode("utf-8")),
        }
        for row, prompt in prompts
        if prompt
    ]
    if records:
        connection.execute(PromptRecord.__table__.insert(), records)


def _import_assets(
    connection: Connection,
    legacy: dict[str, Any],
    episode_ids: dict[str, uuid.UUID],
) -> None:
    variants = {str(row["id"]): row for row in legacy["variants"]}
    slots = {str(row["id"]): row for row in legacy["slots"]}
    media = [
        {
            "id": uuid.UUID(str(row["id"])),
            "production_run_id": _run_id_for_variant(
                row["episode_variant_id"],
                variants,
                slots,
            ),
            "episode_id": episode_ids[str(row["episode_variant_id"])],
            "producing_step_id": uuid.UUID(str(row["generation_job_id"])),
            "role": row["asset_kind"],
            "scope": "episode",
            "status": "archived",
            "media_type": "video" if row["container"] else "image",
            "local_path": row["storage_path"],
            "sha256": row["sha256"],
            "byte_size": row["byte_size"],
            "metadata_json": {
                "legacyQcStatus": row["qc_status"],
                "legacyReviewStatus": row["review_status"],
                "container": row["container"],
                "videoCodec": row["video_codec"],
                "audioCodec": row["audio_codec"],
                "width": row["width"],
                "height": row["height"],
                "durationMs": row["duration_ms"],
                "hasAudio": row["has_audio"],
                "qcReport": row["qc_report_json"],
            },
        }
        for row in legacy["mediaAssets"]
    ]
    canon = [
        {
            "id": uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"cat-video:legacy-canon:{row['id']}",
            ),
            "production_run_id": None,
            "episode_id": None,
            "producing_step_id": None,
            "role": row["role"],
            "scope": "canon",
            "status": ("approved" if row["status"] == "approved" else "archived"),
            "media_type": "image",
            "local_path": row["storage_path"],
            "sha256": row["sha256"],
            "byte_size": row["byte_size"],
            "metadata_json": {
                "legacyReferenceAssetId": str(row["id"]),
                "assetId": row["asset_id"],
                "versionId": row["version_id"],
                "mimeType": row["mime_type"],
                "width": row["width"],
                "height": row["height"],
            },
        }
        for row in legacy["canonAssets"]
    ]
    if media or canon:
        connection.execute(Asset.__table__.insert(), media + canon)
    _import_reviews(connection, legacy)


def _import_reviews(
    connection: Connection,
    legacy: dict[str, Any],
) -> None:
    media_ids = {str(row["id"]) for row in legacy["mediaAssets"]}
    reviews = [
        {
            "id": uuid.UUID(str(row["id"])),
            "step_id": _job_id_for_media(
                row["media_asset_id"],
                legacy["mediaAssets"],
            ),
            "asset_id": uuid.UUID(str(row["media_asset_id"])),
            "source": (
                row["decision_source"]
                if row["decision_source"] in {"human", "ark_visual"}
                else "human"
            ),
            "decision": row["decision"],
            "reason": row["reason"],
            "warnings_json": [],
            "evidence_json": row["evidence_json"] or {},
        }
        for row in legacy["reviews"]
        if str(row["media_asset_id"]) in media_ids
    ]
    if reviews:
        connection.execute(Review.__table__.insert(), reviews)


def _legacy_video_input_mode(render_plan: dict[str, Any] | None) -> str:
    if render_plan is None:
        return "multimodal_reference"
    mode = render_plan.get("visualInputMode")
    if mode in {"generated_first_last_frames", "first_last_frame"}:
        return "strict_first_last"
    if mode in {"generated_first_frame", "first_frame"}:
        return "strict_first_frame"
    return "multimodal_reference"


def _run_id_for_variant(
    variant_id: str | uuid.UUID,
    variants: dict[str, dict[str, Any]],
    slots: dict[str, dict[str, Any]],
) -> uuid.UUID:
    variant = variants[str(variant_id)]
    return uuid.UUID(str(slots[str(variant["daily_slot_id"])]["daily_life_pack_id"]))


def _job_id_for_media(
    media_id: str | uuid.UUID,
    media_rows: list[dict[str, Any]],
) -> uuid.UUID:
    row = next(item for item in media_rows if str(item["id"]) == str(media_id))
    return uuid.UUID(str(row["generation_job_id"]))


def _as_datetime(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
