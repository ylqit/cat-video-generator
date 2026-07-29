"""SQLAlchemy行到Application读模型和HTTP字典的映射。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...application.ports import StoredAsset, StoredEpisode, StoredStep
from ...domain.contracts import EpisodePlan
from ...domain.workflow import EpisodeStatus, StepKind, StepStatus
from .models import (
    Asset,
    DeliveryItem,
    DeliveryPackage,
    Episode,
    ProductionRun,
    PromptRecord,
    Review,
    WorkflowStep,
)


def stored_step(row: WorkflowStep) -> StoredStep:
    return StoredStep(
        id=row.id,
        run_id=row.production_run_id,
        episode_id=row.episode_id,
        kind=StepKind(row.kind),
        status=StepStatus(row.status),
        attempt=row.attempt,
        provider_task_id=row.provider_task_id,
        model=row.model,
        request_summary=row.request_summary_json,
    )


def stored_episode(row: Episode) -> StoredEpisode:
    return StoredEpisode(
        id=row.id,
        run_id=row.production_run_id,
        plan=EpisodePlan.model_validate(row.script_json),
        status=EpisodeStatus(row.status),
        selected_video_asset_id=row.selected_video_asset_id,
    )


def stored_asset(row: Asset) -> StoredAsset:
    return StoredAsset(
        id=row.id,
        run_id=row.production_run_id,
        episode_id=row.episode_id,
        step_id=row.producing_step_id,
        role=row.role,
        media_type=row.media_type,
        scope=row.scope,
        status=row.status,
        path=Path(row.local_path),
        sha256=row.sha256,
        metadata=row.metadata_json,
    )


def run_dict(row: ProductionRun) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "contentDate": row.content_date.isoformat(),
        "theme": row.theme,
        "status": row.status,
        "selectedCandidate": row.selected_candidate,
        "archivedSource": row.archived_source,
        "createdAt": row.created_at.isoformat(),
        "updatedAt": row.updated_at.isoformat(),
    }


def episode_dict(row: Episode) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "runId": str(row.production_run_id),
        "slot": row.slot,
        "sortOrder": row.sort_order,
        "title": row.title,
        "status": row.status,
        "videoInputMode": row.video_input_mode,
        "selectedVideoAssetId": (
            None
            if row.selected_video_asset_id is None
            else str(row.selected_video_asset_id)
        ),
        "script": row.script_json,
    }


def step_dict(row: WorkflowStep) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "runId": str(row.production_run_id),
        "episodeId": None if row.episode_id is None else str(row.episode_id),
        "parentStepId": (
            None if row.parent_step_id is None else str(row.parent_step_id)
        ),
        "kind": row.kind,
        "status": row.status,
        "attempt": row.attempt,
        "provider": row.provider,
        "providerTaskId": row.provider_task_id,
        "model": row.model,
        "inputHash": row.input_hash,
        "requestSummary": row.request_summary_json,
        "error": row.error_json,
        "createdAt": row.created_at.isoformat(),
    }


def prompt_dict(
    row: PromptRecord,
    *,
    full: bool = False,
) -> dict[str, Any]:
    value = {
        "id": str(row.id),
        "stepId": str(row.step_id),
        "parentPromptId": (
            None if row.parent_prompt_id is None else str(row.parent_prompt_id)
        ),
        "purpose": row.purpose,
        "model": row.model,
        "sha256": row.sha256,
        "charCount": row.char_count,
        "utf8Bytes": row.utf8_bytes,
        "createdAt": row.created_at.isoformat(),
    }
    if full:
        value["text"] = row.prompt_text
    return value


def asset_dict(row: Asset) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "episodeId": None if row.episode_id is None else str(row.episode_id),
        "stepId": (
            None if row.producing_step_id is None else str(row.producing_step_id)
        ),
        "role": row.role,
        "scope": row.scope,
        "status": row.status,
        "mediaType": row.media_type,
        "localPath": row.local_path,
        "sha256": row.sha256,
        "metadata": row.metadata_json,
    }


def review_dict(row: Review) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "stepId": str(row.step_id),
        "assetId": None if row.asset_id is None else str(row.asset_id),
        "source": row.source,
        "decision": row.decision,
        "reason": row.reason,
        "warnings": row.warnings_json,
        "evidence": row.evidence_json,
    }


def delivery_package_dict(
    row: DeliveryPackage,
    items: tuple[DeliveryItem, ...] | list[DeliveryItem],
) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "runId": str(row.production_run_id),
        "revision": row.revision,
        "status": row.status,
        "localPath": row.local_path,
        "manifestSha256": row.manifest_sha256,
        "createdAt": row.created_at.isoformat(),
        "items": [
            {
                "id": str(item.id),
                "episodeId": str(item.episode_id),
                "assetId": str(item.asset_id),
                "slot": item.slot,
                "sortOrder": item.sort_order,
                "filename": item.filename,
                "sha256": item.sha256,
            }
            for item in items
        ],
    }
