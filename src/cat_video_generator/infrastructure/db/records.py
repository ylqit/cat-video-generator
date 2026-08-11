"""SQLAlchemy行到Application读模型和HTTP字典的映射。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...application.ports import StoredAsset, StoredEpisode, StoredPrompt, StoredStep
from ...domain.contracts import (
    CURRENT_CONTRACT_VERSION,
    ContractVersionError,
    EpisodePlan,
    EpisodeScript,
    Slot,
)
from ...domain.pipeline import PipelineSettings
from ...domain.rendering import build_render_plan
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
        created_at=row.created_at,
        submitted_at=row.submitted_at,
        error_code=(row.error_json or {}).get("code"),
        error_message=(row.error_json or {}).get("message"),
    )


def ensure_current_contract(row: ProductionRun) -> None:
    """拒绝读取未知导演契约，不在运行时猜测或补齐旧JSON。"""

    if row.contract_version != CURRENT_CONTRACT_VERSION:
        raise ContractVersionError(
            f"Run {row.id}使用契约版本{row.contract_version}，"
            f"当前仅支持版本{CURRENT_CONTRACT_VERSION}"
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
    compatible = row.contract_version == CURRENT_CONTRACT_VERSION
    settings = (
        PipelineSettings.model_validate(row.pipeline_settings_json)
        if compatible
        else None
    )
    project_input = row.planning_json.get("projectInput")
    legacy_brief = row.planning_json.get("dayBrief")
    theme = (
        project_input.get("theme")
        if isinstance(project_input, dict)
        else legacy_brief.get("theme")
        if isinstance(legacy_brief, dict)
        else None
    )
    if not compatible:
        return {
            "id": str(row.id),
            "contentDate": row.content_date.isoformat(),
            "contractVersion": row.contract_version,
            "compatible": False,
            "theme": theme,
            "status": row.status,
            "pipelineSettings": None,
            "createdAt": row.created_at.isoformat(),
            "updatedAt": row.updated_at.isoformat(),
            "availableActions": [],
            "nextAction": "该项目使用旧生产契约，仅可查看历史摘要，不能继续生产",
        }
    available_actions = (
        [{"type": "deliver", "label": "构建交付包", "paid": False}] if row.status == "ready" else []
    )
    return {
        "id": str(row.id),
        "contentDate": row.content_date.isoformat(),
        "contractVersion": row.contract_version,
        "compatible": True,
        "theme": theme,
        "status": row.status,
        "pipelineSettings": settings.model_dump(mode="json", by_alias=True),
        "createdAt": row.created_at.isoformat(),
        "updatedAt": row.updated_at.isoformat(),
        # 交付资格由后端状态机决定，避免Web自行复制Run状态判断。
        "availableActions": available_actions,
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
    render_plan = build_render_plan(plan)
    raw_override = row.prompt_overrides_json or {}
    override_values = (
        raw_override.get("values") if isinstance(raw_override.get("values"), dict) else {}
    )
    override_state = {
        "enabled": bool(raw_override.get("enabled", False)),
        "stale": bool(raw_override.get("stale", False)),
        "sourceScriptSha256": raw_override.get("sourceScriptSha256"),
        "values": override_values,
    }
    return {
        "id": str(row.id),
        "runId": str(row.production_run_id),
        "slot": row.slot,
        "sortOrder": row.sort_order,
        "title": plan.script.title,
        "status": row.status,
        "activityFocus": plan.script.activity_focus.value,
        "relationshipArc": plan.script.relationship_arc,
        "renderPlan": render_plan.model_dump(mode="json"),
        "nextAction": {
            "planned": "生成定妆图与开场锚点",
            "preparing_visuals": "完成视觉锚点审核",
            "video_pending": "提交Seedance视频任务",
            "video_generating": "轮询并下载已有Ark任务",
            "media_qc": "完成媒体技术检查",
            "content_review": "人工观看并批准或拒绝视频",
            "ready": "等待全天其余时段或构建交付包",
            "failed": "查看失败节点后选择重试媒体或局部重规划",
        }.get(row.status),
        "selectedVideoAssetId": (
            None if row.selected_video_asset_id is None else str(row.selected_video_asset_id)
        ),
        "promptOverrides": (
            override_values
            if override_state["enabled"] and not override_state["stale"]
            else {}
        ),
        "promptOverrideState": override_state,
        "script": plan.script.model_dump(mode="json"),
    }


def step_dict(row: WorkflowStep) -> dict[str, Any]:
    operation_key = row.operation_key
    actions: list[dict[str, Any]] = []
    local_recovery = (
        row.status == StepStatus.FAILED.value
        and row.kind == StepKind.VIDEO.value
        and row.provider_task_id is not None
        and (row.error_json or {}).get("code") == "media_qc_failed"
        and row.input_snapshot_json.get("provider_task_status") == "succeeded"
    )
    if local_recovery:
        actions.append(
            {"type": "continue_query", "label": "重新执行本地落盘与QC", "paid": False}
        )
    elif (
        row.status in {StepStatus.QUEUED.value, StepStatus.RUNNING.value}
        and row.kind == StepKind.VIDEO.value
        and row.provider_task_id
    ):
        actions.append({"type": "continue_query", "label": "继续查询", "paid": False})
    elif row.status == StepStatus.SUBMISSION_UNKNOWN.value:
        if row.kind == StepKind.VIDEO.value:
            actions.append({"type": "reconcile", "label": "查询并对账", "paid": False})
        elif row.kind == StepKind.IMAGE.value:
            actions.append(
                {
                    "type": "retry_unknown_image",
                    "label": "接受风险并重新生成",
                    "paid": True,
                    "requiresDuplicateBillingAck": True,
                }
            )
    elif (
        row.status
        in {
            StepStatus.FAILED.value,
            StepStatus.EXPIRED.value,
            StepStatus.CANCELLED.value,
        }
        and row.kind in {StepKind.IMAGE.value, StepKind.VIDEO.value}
        and operation_key
    ):
        actions.append({"type": "retry", "label": "重试该节点", "paid": True})
    elif row.status == StepStatus.AWAITING_REVIEW.value:
        actions.append({"type": "review", "label": "进入审核", "paid": False})
    next_action = actions[0]["label"] if actions else None
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
        "availableActions": actions,
        "createdAt": row.created_at.isoformat(),
        "submittedAt": None if row.submitted_at is None else row.submitted_at.isoformat(),
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
