"""SQLAlchemy行到Application读模型和HTTP字典的映射。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...application.ports import StoredAsset, StoredEpisode, StoredPrompt, StoredStep
from ...domain.continuity import validate_continuity
from ...domain.contracts import EpisodePlan, EpisodeScript, Slot
from ...domain.pipeline import PipelineSettings
from ...domain.workflow import EpisodeStatus, PromptPurpose, StepKind, StepStatus
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
        operation_key=row.operation_key,
        input_snapshot=row.input_snapshot_json,
    )


def stored_prompt(row: PromptRecord) -> StoredPrompt:
    return StoredPrompt(
        id=row.id,
        step_id=row.step_id,
        purpose=PromptPurpose(row.purpose),
        model=row.model,
        text=row.prompt_text,
        sha256=row.sha256,
    )


def stored_episode(row: Episode) -> StoredEpisode:
    script = EpisodeScript.model_validate(row.script_json)
    return StoredEpisode(
        id=row.id,
        run_id=row.production_run_id,
        plan=EpisodePlan(
            slot=Slot(row.slot),
            script=script,
        ),
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
        semantic_key=row.semantic_key,
    )


def run_dict(row: ProductionRun) -> dict[str, Any]:
    settings = PipelineSettings.model_validate(row.pipeline_settings_json)
    return {
        "id": str(row.id),
        "contentDate": row.content_date.isoformat(),
        "theme": row.planning_json.get("dayBrief", {}).get("theme"),
        "status": row.status,
        "pipelineSettings": settings.model_dump(mode="json", by_alias=True),
        "createdAt": row.created_at.isoformat(),
        "updatedAt": row.updated_at.isoformat(),
        "nextAction": {
            "draft": "继续完成总导演和三个时段导演",
            "planning_review": "查看导演候选矛盾并执行replan-episode",
            "planned": "运行全天或指定时段媒体生产",
            "generating": "等待或恢复现有Ark任务",
            "reviewing": "完成人工媒体审核",
            "ready": "构建本地交付包",
            "delivered": "已完成交付",
            "failed": "检查失败步骤后决定恢复或重规划",
        }.get(row.status),
    }


def episode_dict(row: Episode) -> dict[str, Any]:
    plan = EpisodePlan(
        slot=Slot(row.slot),
        script=EpisodeScript.model_validate(row.script_json),
    )
    report = validate_continuity(plan.script.continuity)
    return {
        "id": str(row.id),
        "runId": str(row.production_run_id),
        "slot": row.slot,
        "sortOrder": row.sort_order,
        "title": plan.script.title,
        "status": row.status,
        "videoInputMode": (
            "strict_first_last" if plan.script.ending.visual_critical else "storyboard_reference"
        ),
        "worldConsistencyStatus": "valid" if report.valid else "contradiction",
        "contradictions": [item.message for item in report.issues],
        "nextAction": {
            "planned": "生成整组故事板",
            "preparing_visuals": "完成故事板语义审核",
            "video_pending": "提交Seedance视频任务",
            "video_generating": "轮询并下载已有Ark任务",
            "media_qc": "完成媒体技术检查",
            "content_review": "人工观看并批准或拒绝视频",
            "ready": "等待全天其余时段或构建交付包",
            "failed": "人工检查失败原因后局部重规划",
        }.get(row.status),
        "selectedVideoAssetId": (
            None if row.selected_video_asset_id is None else str(row.selected_video_asset_id)
        ),
        "promptOverrides": row.prompt_overrides_json or {},
        "script": plan.script.model_dump(mode="json"),
    }


def step_dict(row: WorkflowStep) -> dict[str, Any]:
    operation_key = row.operation_key
    next_action = None
    if row.status == StepStatus.SUBMISSION_UNKNOWN.value:
        next_action = "先对账Ark任务列表，禁止重复POST"
    elif row.status in {
        StepStatus.FAILED.value,
        StepStatus.EXPIRED.value,
        StepStatus.CANCELLED.value,
    }:
        next_action = (
            f"cvg retry-step {row.id} --reason <原因>"
            if operation_key
            else "查看失败详情；旧步骤不支持自动重试"
        )
    return {
        "id": str(row.id),
        "runId": str(row.production_run_id),
        "episodeId": None if row.episode_id is None else str(row.episode_id),
        "parentStepId": (None if row.parent_step_id is None else str(row.parent_step_id)),
        "kind": row.kind,
        "status": row.status,
        "attempt": row.attempt,
        "provider": row.provider,
        "providerTaskId": row.provider_task_id,
        "model": row.model,
        "inputHash": row.input_hash,
        "operationKey": operation_key,
        "inputSnapshot": row.input_snapshot_json,
        "error": row.error_json,
        "nextAction": next_action,
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
        "parentPromptId": (None if row.parent_prompt_id is None else str(row.parent_prompt_id)),
        "purpose": row.purpose,
        "model": row.model,
        "sha256": row.sha256,
        "charCount": len(row.prompt_text),
        "utf8Bytes": len(row.prompt_text.encode("utf-8")),
        "createdAt": row.created_at.isoformat(),
    }
    if full:
        value["text"] = row.prompt_text
    return value


def asset_dict(row: Asset) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "episodeId": None if row.episode_id is None else str(row.episode_id),
        "stepId": (None if row.producing_step_id is None else str(row.producing_step_id)),
        "role": row.role,
        "semanticKey": row.semantic_key,
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
