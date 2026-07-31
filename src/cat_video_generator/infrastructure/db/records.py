"""SQLAlchemy行到Application读模型和HTTP字典的映射。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...application.ports import StoredAsset, StoredEpisode, StoredPrompt, StoredStep
from ...domain.continuity import assess_visible_world
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


def stored_prompt(row: PromptRecord) -> StoredPrompt:
    return StoredPrompt(
        id=row.id,
        step_id=row.step_id,
        purpose=row.purpose,
        model=row.model,
        text=row.prompt_text,
        sha256=row.sha256,
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
        semantic_key=row.semantic_key,
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
        "nextAction": {
            "draft": "继续完成总导演和三个时段导演",
            "planning_review": "查看导演候选矛盾并执行replan-episode",
            "planned": "运行全天或指定时段媒体生产",
            "generating": "等待或恢复现有Ark任务",
            "reviewing": "完成人工媒体审核",
            "ready": "构建本地交付包",
            "delivered": "已完成交付",
            "failed": "检查失败步骤后决定恢复或重规划",
            "archived": "只读归档",
        }.get(row.status),
    }


def episode_dict(row: Episode) -> dict[str, Any]:
    plan = EpisodePlan.model_validate(row.script_json)
    report = None if plan.visible_world is None else assess_visible_world(plan.visible_world)
    return {
        "id": str(row.id),
        "runId": str(row.production_run_id),
        "slot": row.slot,
        "sortOrder": row.sort_order,
        "title": row.title,
        "status": row.status,
        "videoInputMode": row.video_input_mode,
        "generationStrategy": plan.generation_strategy.value,
        "worldConsistencyStatus": (
            "not_available" if report is None else report.world_consistency_status
        ),
        "contradictions": [] if report is None else list(report.contradictions),
        "renderRiskLevel": ("unknown" if report is None else report.render_risk_level.value),
        "renderRiskReasons": ([] if report is None else list(report.render_risk_reasons)),
        "multiClipRecommended": (False if report is None else report.multi_clip_recommended),
        "nextAction": {
            "planned": "准备精确参考素材或关键帧",
            "preparing_visuals": "完成关键帧语义审核",
            "video_pending": "提交Seedance视频任务",
            "video_generating": "轮询并下载已有Ark任务",
            "media_qc": "完成媒体技术检查",
            "content_review": "人工观看并批准或拒绝视频",
            "ready": "等待全天其余时段或构建交付包",
            "failed": "人工检查失败原因后局部重规划",
            "archived": "只读归档",
        }.get(row.status),
        "selectedVideoAssetId": (
            None if row.selected_video_asset_id is None else str(row.selected_video_asset_id)
        ),
        "promptOverrides": row.prompt_overrides_json or {},
        "script": plan.model_dump(mode="json"),
    }


def step_dict(row: WorkflowStep) -> dict[str, Any]:
    operation_key = row.request_summary_json.get("operationKey")
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
        "requestSummary": row.request_summary_json,
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
