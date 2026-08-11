"""PostgreSQL 只读投影。

本模块把核心表转换为 CLI 与 Web 共用结果，不推进状态、不编译 Prompt，
也不调用 Ark。流程图只展示当前生产内核：导演、定妆、开场锚点、视频与审核。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from ...application.ports import StoredAsset, StoredPrompt
from ...domain.contracts import (
    CURRENT_CONTRACT_VERSION,
    EpisodePlan,
    EpisodeScript,
    RecentContentSummary,
    Slot,
)
from ...domain.pipeline import PipelineSettings
from ...domain.rendering import build_render_plan
from ...domain.workflow import EpisodeStatus, PromptPurpose, RunStatus
from .models import (
    Asset,
    DeliveryItem,
    DeliveryPackage,
    Episode,
    ProductionRun,
    PromptRecord,
    Review,
    VideoSequence,
    WorkflowStep,
)
from .records import (
    asset_dict,
    delivery_package_dict,
    ensure_current_contract,
    episode_dict,
    prompt_dict,
    review_dict,
    run_dict,
    step_dict,
    stored_asset,
    stored_prompt,
)
from .session import ALEMBIC_HEAD


class RecordNotFoundError(LookupError):
    """请求的工作流记录不存在。"""


def _slot_planning_state(
    episodes: tuple[Episode, ...],
    accepted_outcomes: object,
    *,
    guided: bool,
    day_brief_confirmed: bool = True,
) -> list[dict[str, Any]]:
    """投影逐时段解锁状态；只依据已落库Episode和人工确认结果。"""

    planned = {item.slot for item in episodes}
    outcomes = accepted_outcomes if isinstance(accepted_outcomes, dict) else {}
    result: list[dict[str, Any]] = []
    for slot in Slot:
        previous = [item.value for item in Slot if item.sort_order < slot.sort_order]
        missing_outcomes = [item for item in previous if item not in outcomes]
        is_planned = slot.value in planned
        if not guided:
            unlocked, reason = not is_planned, None
        elif is_planned:
            unlocked, reason = False, "该时段已经规划"
        elif not day_brief_confirmed:
            unlocked, reason = False, "请先保存并确认全天总导演边界"
        elif any(
            item.value not in planned
            for item in Slot
            if item.sort_order < slot.sort_order
        ):
            unlocked, reason = False, "前序时段尚未规划"
        elif missing_outcomes:
            unlocked, reason = False, "请先批准并确认" + "、".join(missing_outcomes) + "结果卡"
        else:
            unlocked, reason = True, None
        result.append(
            {
                "slot": slot.value,
                "planned": is_planned,
                "unlocked": unlocked,
                "blockReason": reason,
                "outcomeConfirmed": slot.value in outcomes,
            }
        )
    return result


def _trace_input_bindings(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """把三种Step快照投影为统一素材顺序，不在查询层猜测业务用途。"""

    input_plan = snapshot.get("input_plan")
    if isinstance(input_plan, dict) and isinstance(input_plan.get("bindings"), list):
        return [
            {
                "assetId": str(item.get("asset_id")),
                "semanticKey": item.get("semantic_key"),
                "modality": item.get("modality"),
                "providerRole": item.get("provider_role"),
                "ordinal": item.get("ordinal"),
                "sha256": item.get("sha256"),
            }
            for item in input_plan["bindings"]
            if isinstance(item, dict) and item.get("asset_id") is not None
        ]
    asset_ids = snapshot.get("reference_asset_ids")
    hashes = snapshot.get("reference_sha256")
    if not isinstance(asset_ids, list | tuple):
        return []
    hash_values = hashes if isinstance(hashes, list | tuple) else ()
    return [
        {
            "assetId": str(asset_id),
            "ordinal": index,
            "sha256": str(hash_values[index - 1]) if index <= len(hash_values) else None,
        }
        for index, asset_id in enumerate(asset_ids, 1)
    ]


def _video_sequence_dict(item: VideoSequence) -> dict[str, Any]:
    """把单轨EDL版本投影给Web；JSON只描述剪辑决定，不复制媒体或Step详情。"""

    return {
        "id": str(item.id),
        "episodeId": str(item.episode_id),
        "revision": item.revision,
        "parentSequenceId": (
            None if item.parent_sequence_id is None else str(item.parent_sequence_id)
        ),
        "baseAssetId": str(item.base_asset_id),
        "renderedAssetId": (
            None if item.rendered_asset_id is None else str(item.rendered_asset_id)
        ),
        "status": item.status,
        "durationMs": item.duration_ms,
        "audioPolicy": item.audio_policy,
        "clips": list(item.clips_json or []),
        "createdAt": item.created_at.isoformat(),
        "updatedAt": item.updated_at.isoformat(),
    }


def _current_stage(
    run: ProductionRun,
    episodes: tuple[Episode, ...],
    steps: tuple[WorkflowStep, ...],
) -> str:
    if "dayBrief" not in run.planning_json:
        return "dayBrief"
    settings = PipelineSettings.model_validate(run.pipeline_settings_json or {})
    if settings.planning_mode.value == "guided_sequential":
        if "dayBriefConfirmedAt" not in run.planning_json:
            return "dayBrief"
        outcomes = run.planning_json.get("acceptedOutcomes", {}) or {}
        by_slot = {item.slot: item for item in episodes}
        for slot in Slot:
            episode = by_slot.get(slot.value)
            if episode is None:
                return "script"
            status = EpisodeStatus(episode.status)
            if status in {EpisodeStatus.PLANNED, EpisodeStatus.PREPARING_VISUALS}:
                return "visual"
            if status in {
                EpisodeStatus.VIDEO_PENDING,
                EpisodeStatus.VIDEO_GENERATING,
                EpisodeStatus.MEDIA_QC,
            }:
                return "video"
            if status in {EpisodeStatus.CONTENT_REVIEW, EpisodeStatus.READY} and (
                status is not EpisodeStatus.READY or slot.value not in outcomes
            ):
                return "review"
            if status is EpisodeStatus.FAILED:
                return "script"
        return "review"
    if run.status == RunStatus.DRAFT.value:
        return "dayBrief"
    if run.status == RunStatus.PLANNING_REVIEW.value:
        return "script"
    statuses = {item.status for item in episodes}
    if statuses and statuses <= {"ready"}:
        return "review"
    if statuses & {"video_pending", "video_generating", "media_qc", "content_review"}:
        return "video"
    if any(item.kind == "video" for item in steps):
        return "video"
    if any(item.operation_key in {"image:look", "image:opening_anchor"} for item in steps):
        return "visual"
    return "script"


def _guided_next_action(
    episodes: tuple[Episode, ...],
    accepted_outcomes: object,
    *,
    day_brief_confirmed: bool,
) -> str:
    """返回顺序工作台唯一应执行的下一步，不用Run粗粒度状态猜测。"""

    if not day_brief_confirmed:
        return "保存并确认全天总导演边界"
    outcomes = accepted_outcomes if isinstance(accepted_outcomes, dict) else {}
    by_slot = {item.slot: item for item in episodes}
    for slot in Slot:
        episode = by_slot.get(slot.value)
        if episode is None:
            return f"规划{slot.value}时段导演"
        status = EpisodeStatus(episode.status)
        if status is EpisodeStatus.PLANNED:
            return f"生成并审核{slot.value}视觉锚点"
        if status is EpisodeStatus.PREPARING_VISUALS:
            return f"完成{slot.value}视觉锚点审核"
        if status is EpisodeStatus.VIDEO_PENDING:
            return f"生成{slot.value}视频"
        if status in {
            EpisodeStatus.VIDEO_GENERATING,
            EpisodeStatus.MEDIA_QC,
        }:
            return f"等待或恢复{slot.value}视频任务"
        if status is EpisodeStatus.CONTENT_REVIEW:
            return f"人工观看并审核{slot.value}视频"
        if status is EpisodeStatus.FAILED:
            return f"查看{slot.value}失败节点并选择重试或重规划"
        if slot.value not in outcomes:
            return f"编辑并确认{slot.value}实际结果卡"
    return "构建本地1/2/3交付包"


def _workflow_nodes(
    episodes: tuple[Episode, ...],
    steps: tuple[WorkflowStep, ...],
    prompts: tuple[PromptRecord, ...],
    assets: tuple[Asset, ...],
    reviews: tuple[Review, ...],
    accepted_outcomes: dict[str, Any] | None = None,
    *,
    guided: bool,
    day_brief_confirmed: bool,
    stale_slots: tuple[str, ...] = (),
    stale_nodes: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    accepted_outcomes = accepted_outcomes or {}
    prompt_ids: dict[uuid.UUID, list[str]] = {}
    review_ids: dict[uuid.UUID, list[str]] = {}
    for prompt in prompts:
        prompt_ids.setdefault(prompt.step_id, []).append(str(prompt.id))
    for review in reviews:
        review_ids.setdefault(review.step_id, []).append(str(review.id))

    def latest(operation_key: str, episode_id: uuid.UUID | None = None) -> WorkflowStep | None:
        matches = [
            item
            for item in steps
            if item.operation_key == operation_key
            and (episode_id is None or item.episode_id == episode_id)
        ]
        return max(matches, key=lambda item: item.created_at, default=None)

    active_statuses = {"pending", "submitting", "queued", "running", "awaiting_review"}

    def node(
        *,
        node_id: str,
        node_type: str,
        label: str,
        step: WorkflowStep | None,
        slot: str | None = None,
        node_assets: tuple[Asset, ...] = (),
        status: str | None = None,
        contract_status: str = "not_applicable",
        semantic_status: str = "not_applicable",
        availability: str | None = None,
        lock_reason: str | None = None,
        unlock_requirements: tuple[str, ...] = (),
        completed: bool = False,
        extra_steps: tuple[WorkflowStep, ...] = (),
    ) -> dict[str, Any]:
        step_payload = None if step is None else step_dict(step)
        attempt_source = extra_steps or (
            ()
            if step is None
            else tuple(
                item
                for item in steps
                if item.operation_key == step.operation_key
                and item.episode_id == step.episode_id
            )
        )
        attempts = [
            step_dict(item)
            for item in sorted(attempt_source, key=lambda item: item.created_at)
        ]
        execution_status = "not_created" if step is None else step.status
        resolved_availability = availability or (
            "completed"
            if completed
            else "active"
            if execution_status in active_statuses
            else "ready"
        )
        actions = [] if step_payload is None else list(step_payload["availableActions"])
        if (
            step is not None
            and step.kind in {"image", "video"}
            and step.status
            not in {"pending", "submitting", "queued", "running", "submission_unknown"}
        ):
            actions.append({"type": "regenerate", "label": "重新生成新版本", "paid": True})
        return {
            "semanticNodeId": node_id,
            "type": node_type,
            "slot": slot,
            "label": label,
            "status": status or ("pending" if step is None else step.status),
            "availability": resolved_availability,
            "executionStatus": execution_status,
            "lockReason": lock_reason,
            "unlockRequirements": list(unlock_requirements),
            "providerStatus": "pending" if step is None else step.status,
            "contractStatus": contract_status,
            "semanticReviewStatus": semantic_status,
            "stepId": None if step is None else str(step.id),
            "promptIds": [] if step is None else prompt_ids.get(step.id, []),
            "assetIds": [str(item.id) for item in node_assets],
            "reviewIds": [] if step is None else review_ids.get(step.id, []),
            "error": None if step_payload is None else step_payload["error"],
            "nextAction": (
                lock_reason
                if resolved_availability == "locked"
                else None if step_payload is None else step_payload["nextAction"]
            ),
            "allowedActions": actions,
            "currentAttemptId": None if step is None else str(step.id),
            "attemptIds": [item["id"] for item in attempts],
            "attempts": attempts,
            "stale": node_id in stale_nodes or (slot is not None and slot in stale_slots),
        }

    def director_state(step: WorkflowStep | None) -> tuple[str, str, str]:
        if step is None:
            return "pending", "pending", "pending"
        if (step.error_json or {}).get("code") == "invalid_director_output":
            return "succeeded", "rejected", "rejected"
        semantic_rejected = any(
            item.step_id == step.id
            and item.decision == "rejected"
            and (item.evidence_json or {}).get("phase") == "episode_contract"
            for item in reviews
        )
        return (
            step.status,
            "parsed" if step.status == "succeeded" else "not_available",
            "rejected"
            if semantic_rejected
            else "approved"
            if step.status == "succeeded"
            else "pending",
        )

    day_step = latest("director:day")
    provider, contract, semantic = director_state(day_step)
    nodes = [
        {
            **node(
                node_id="run:day-director",
                node_type="director",
                label="全天总导演",
                step=day_step,
                contract_status=contract,
                completed=day_step is not None and day_step.status == "succeeded",
            ),
            "providerStatus": provider,
            "semanticReviewStatus": semantic,
        }
    ]
    if day_step is not None and day_step.status == "failed":
        nodes[0]["allowedActions"] = [
            {
                "type": "regenerate",
                "label": "修复并重执行总导演",
                "paid": True,
            }
        ]
        nodes[0]["nextAction"] = "查看契约错误后重执行总导演"
    day_confirm_unlocked = day_step is not None and day_step.status == "succeeded"
    nodes.append(
        {
            **node(
                node_id="run:day-confirmation",
                node_type="day_confirmation",
                label="DayBrief人工确认",
                step=None,
                status="confirmed" if day_brief_confirmed else "pending",
                availability=(
                    "completed"
                    if day_brief_confirmed
                    else "ready"
                    if day_confirm_unlocked
                    else "locked"
                ),
                lock_reason=None if day_confirm_unlocked else "等待总导演完成",
                unlock_requirements=("总导演完成",),
                completed=day_brief_confirmed,
            ),
            "providerStatus": "not_applicable",
        }
    )
    episodes_by_slot = {item.slot: item for item in episodes}
    for slot_item in Slot:
        slot = slot_item.value
        episode = episodes_by_slot.get(slot)
        episode_id = None if episode is None else episode.id
        episode_assets = tuple(
            item for item in assets if episode_id is not None and item.episode_id == episode_id
        )
        director = latest(f"director:episode:{slot}")
        provider, contract, semantic = director_state(director)
        previous_slots = [item.value for item in Slot if item.sort_order < slot_item.sort_order]
        slot_unlocked = day_brief_confirmed and (
            not guided or all(item in accepted_outcomes for item in previous_slots)
        )
        slot_requirement = (
            "确认DayBrief"
            if not day_brief_confirmed
            else f"确认{previous_slots[-1]}结果卡"
            if guided and previous_slots and previous_slots[-1] not in accepted_outcomes
            else None
        )
        director_node = node(
            node_id=f"{slot}:director",
            node_type="director",
            slot=slot,
            label=f"{slot}导演",
            step=director,
            contract_status=contract,
            semantic_status=("approved" if semantic == "approved" and episode else semantic),
            availability=(
                "completed"
                if episode is not None
                else "ready"
                if slot_unlocked
                else "locked"
            ),
            lock_reason=slot_requirement,
            unlock_requirements=(() if slot_requirement is None else (slot_requirement,)),
            completed=episode is not None,
        )
        director_node["providerStatus"] = provider
        if episode is not None:
            director_node["allowedActions"] = [
                {
                    "type": "replan",
                    "label": "重新规划该时段",
                    "paid": True,
                }
            ]
        if contract == "rejected" or semantic == "rejected":
            director_node["status"] = "planning_rejected"
            director_node["nextAction"] = "查看拒绝原因后显式重规划本时段"
        nodes.append(director_node)
        if episode is None:
            nodes.extend(
                (
                    node(
                        node_id=f"{slot}:look",
                        node_type="look",
                        slot=slot,
                        label=f"{slot}定妆图",
                        step=None,
                        availability="locked",
                        lock_reason=f"等待{slot}导演完成",
                        unlock_requirements=(f"{slot}导演完成",),
                    ),
                    node(
                        node_id=f"{slot}:opening-anchor",
                        node_type="opening_anchor",
                        slot=slot,
                        label=f"{slot}开场锚点",
                        step=None,
                        availability="locked",
                        lock_reason=f"等待{slot}定妆图批准",
                        unlock_requirements=(f"{slot}定妆图批准",),
                    ),
                    node(
                        node_id=f"{slot}:video",
                        node_type="video",
                        slot=slot,
                        label=f"{slot}视频",
                        step=None,
                        availability="locked",
                        lock_reason=f"等待{slot}开场锚点批准",
                        unlock_requirements=(f"{slot}开场锚点批准",),
                    ),
                    node(
                        node_id=f"{slot}:review",
                        node_type="content_review",
                        slot=slot,
                        label=f"{slot}内容审核",
                        step=None,
                        availability="locked",
                        lock_reason=f"等待{slot}视频生成",
                        unlock_requirements=(f"{slot}视频生成",),
                    ),
                    {
                        **node(
                            node_id=f"{slot}:outcome",
                            node_type="accepted_outcome",
                            slot=slot,
                            label=f"{slot}结果卡",
                            step=None,
                            status="locked",
                            semantic_status="pending",
                            availability="locked",
                            lock_reason=f"等待{slot}视频批准",
                            unlock_requirements=(f"{slot}视频批准",),
                        ),
                        "providerStatus": "not_applicable",
                    },
                )
            )
            continue
        for operation_key, role, node_type, label in (
            ("image:look", "look_reference", "look", f"{slot}定妆图"),
            ("image:opening_anchor", "opening_anchor", "opening_anchor", f"{slot}开场锚点"),
        ):
            operation_steps = tuple(
                item
                for item in steps
                if item.operation_key == operation_key and item.episode_id == episode.id
            )
            approved_assets = tuple(
                item
                for item in episode_assets
                if item.role == role and item.status in {"approved", "ready"}
            )
            # 当前节点优先展示真正可供生产使用的批准资产；较晚的失败尝试仍完整
            # 出现在 attempts 中，但不能把一个已经可继续的视频节点伪装成 rejected。
            effective_asset = max(
                approved_assets,
                key=lambda item: item.created_at,
                default=None,
            )
            active_attempt = max(
                (item for item in operation_steps if item.status in active_statuses),
                key=lambda item: item.created_at,
                default=None,
            )
            step = (
                active_attempt
                or next(
                    (
                        item
                        for item in operation_steps
                        if effective_asset is not None
                        and item.id == effective_asset.producing_step_id
                    ),
                    None,
                )
                or latest(operation_key, episode.id)
            )
            node_assets = tuple(
                item
                for item in episode_assets
                if item.role == role and step is not None and item.producing_step_id == step.id
            )
            semantic = (
                "approved"
                if any(item.status in {"approved", "ready"} for item in node_assets)
                else "rejected"
                if any(item.status == "rejected" for item in node_assets)
                else "pending"
            )
            nodes.append(
                node(
                    node_id=f"{slot}:{node_type.replace('_', '-')}",
                    node_type=node_type,
                    slot=slot,
                    label=label,
                    step=step,
                    node_assets=node_assets,
                    status=None if semantic == "pending" else semantic,
                    semantic_status=semantic,
                    availability=(
                        "completed"
                        if semantic == "approved"
                        else "active"
                        if step is not None and step.status in active_statuses
                        else "ready"
                    ),
                    completed=semantic == "approved",
                )
            )
        render_plan = build_render_plan(
            EpisodePlan(slot=slot_item, script=EpisodeScript.model_validate(episode.script_json))
        )
        final_video_assets: tuple[Asset, ...] = ()
        final_video_step: WorkflowStep | None = None
        video_steps = tuple(
            item
            for item in steps
            if item.episode_id == episode.id and item.operation_key.startswith("video:")
        )
        for section in render_plan.sections:
            operation_key = (
                "video:single_pass" if section.order == 1 else f"video:extend:{section.order}"
            )
            step = latest(operation_key, episode.id)
            node_assets = tuple(
                item
                for item in episode_assets
                if step is not None
                and item.producing_step_id == step.id
                and item.media_type == "video"
            )
            if section.order == len(render_plan.sections):
                final_video_assets = tuple(item for item in node_assets if item.role == "video")
                final_video_step = step
        approved_anchor = any(
            item.role == "opening_anchor" and item.status in {"approved", "ready"}
            for item in episode_assets
        )
        selected_video = next(
            (
                item
                for item in episode_assets
                if episode.selected_video_asset_id is not None
                and item.id == episode.selected_video_asset_id
            ),
            None,
        )
        candidate_video = max(
            (
                item
                for item in episode_assets
                if item.role == "video" and item.status == "candidate"
            ),
            key=lambda item: item.created_at,
            default=None,
        )
        active_video_step = max(
            (item for item in video_steps if item.status in active_statuses),
            key=lambda item: item.created_at,
            default=None,
        )
        # 语义画布展示的是当前正式采用的视频；未批准的重生成与局部编辑
        # 只属于版本/尝试历史，不能反向把已经完成的主流程节点降级为待审核。
        if selected_video is not None and selected_video.producing_step_id is not None:
            final_video_assets = (selected_video,)
            final_video_step = next(
                (item for item in video_steps if item.id == selected_video.producing_step_id),
                final_video_step,
            )
        elif candidate_video is not None:
            final_video_assets = (candidate_video,)
            if candidate_video.producing_step_id is not None:
                final_video_step = next(
                    (
                        item
                        for item in video_steps
                        if item.id == candidate_video.producing_step_id
                    ),
                    final_video_step,
                )
        elif active_video_step is not None:
            final_video_step = active_video_step
        nodes.append(
            node(
                node_id=f"{slot}:video",
                node_type="video",
                slot=slot,
                label=f"{slot}视频与版本",
                step=final_video_step,
                node_assets=tuple(item for item in episode_assets if item.media_type == "video"),
                availability=(
                    "completed"
                    if selected_video is not None
                    else "active"
                    if candidate_video is not None or active_video_step is not None
                    else "active"
                    if final_video_step is not None and final_video_step.status in active_statuses
                    else "ready"
                    if approved_anchor
                    else "locked"
                ),
                lock_reason=None if approved_anchor else f"等待{slot}开场锚点批准",
                unlock_requirements=(
                    () if approved_anchor else (f"{slot}开场锚点批准",)
                ),
                completed=selected_video is not None,
                extra_steps=video_steps,
            )
        )
        content_status = (
            "approved"
            if any(item.status == "ready" for item in final_video_assets)
            else "rejected"
            if any(item.status == "rejected" for item in final_video_assets)
            else "pending"
        )
        nodes.append(
            node(
                node_id=f"{slot}:review",
                node_type="content_review",
                slot=slot,
                label=f"{slot}内容审核",
                step=final_video_step,
                node_assets=final_video_assets,
                status=None if content_status == "pending" else content_status,
                semantic_status=content_status,
                availability=(
                    "completed"
                    if content_status == "approved"
                    else "ready"
                    if final_video_assets
                    else "locked"
                ),
                lock_reason=None if final_video_assets else f"等待{slot}视频生成",
                unlock_requirements=(
                    () if final_video_assets else (f"{slot}视频生成",)
                ),
                completed=content_status == "approved",
            )
        )
        outcome = accepted_outcomes.get(slot)
        nodes.append(
            {
                **node(
                    node_id=f"{slot}:outcome",
                    node_type="accepted_outcome",
                    slot=slot,
                    label=f"{slot}结果卡",
                    step=None,
                    status=(
                        "confirmed"
                        if isinstance(outcome, dict)
                        else "pending"
                        if content_status == "approved"
                        else "locked"
                    ),
                    semantic_status=(
                        "approved" if isinstance(outcome, dict) else "pending"
                    ),
                    availability=(
                        "completed"
                        if isinstance(outcome, dict)
                        else "ready"
                        if content_status == "approved"
                        else "locked"
                    ),
                    lock_reason=(
                        None
                        if isinstance(outcome, dict) or content_status == "approved"
                        else f"等待{slot}视频批准"
                    ),
                    unlock_requirements=(
                        ()
                        if isinstance(outcome, dict) or content_status == "approved"
                        else (f"{slot}视频批准",)
                    ),
                    completed=isinstance(outcome, dict),
                ),
                "providerStatus": "not_applicable",
                "nextAction": (
                    None
                    if isinstance(outcome, dict)
                    else "人工批准视频后编辑并确认实际结果"
                ),
            }
        )
    delivery_ready = (
        all(item.value in accepted_outcomes for item in Slot)
        if guided
        else len(episodes) == 3
        and all(item.status == EpisodeStatus.READY.value for item in episodes)
    )
    nodes.append(
        {
            **node(
                node_id="run:delivery",
                node_type="delivery",
                label="审核交付",
                step=None,
                status="ready" if delivery_ready else "locked",
                availability="ready" if delivery_ready else "locked",
                lock_reason=None if delivery_ready else "等待三个时段完成审核与结果确认",
                unlock_requirements=("三个时段完成审核与结果确认",),
            ),
            "providerStatus": "not_applicable",
            "allowedActions": (
                [{"type": "deliver", "label": "构建交付包", "paid": False}]
                if delivery_ready
                else []
            ),
        }
    )
    return nodes


def required_record(
    session: Session,
    model: type[Any],
    record_id: uuid.UUID,
) -> Any:
    row = session.get(model, record_id)
    if row is None:
        raise RecordNotFoundError(f"{model.__name__} {record_id} 不存在")
    return row


class SqlAlchemyReadRepository:
    """为 QueryService 提供统一只读投影。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def workflow_graph(self, run_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            run = required_record(session, ProductionRun, run_id)
            episodes = tuple(
                session.execute(
                    select(Episode)
                    .where(Episode.production_run_id == run_id)
                    .order_by(Episode.sort_order)
                ).scalars()
            )
            steps = tuple(
                session.execute(
                    select(WorkflowStep)
                    .where(WorkflowStep.production_run_id == run_id)
                    .order_by(WorkflowStep.created_at)
                ).scalars()
            )
            step_ids = tuple(item.id for item in steps)
            prompts = self._rows_for_steps(session, PromptRecord, step_ids)
            reviews = self._rows_for_steps(session, Review, step_ids)
            assets = tuple(
                session.execute(select(Asset).where(Asset.production_run_id == run_id)).scalars()
            )
            episode_ids = tuple(item.id for item in episodes)
            sequences = tuple(
                session.execute(
                    select(VideoSequence)
                    .where(VideoSequence.episode_id.in_(episode_ids))
                    .order_by(VideoSequence.episode_id, VideoSequence.revision)
                ).scalars()
            ) if episode_ids else ()
            payload = run_dict(run)
            settings = PipelineSettings.model_validate(run.pipeline_settings_json or {})
            day_brief_confirmed = "dayBriefConfirmedAt" in run.planning_json
            # auto_day由规划用例在同一链路中接受DayBrief并继续三个时段；只有
            # guided_sequential需要额外的人工确认时间戳才能解锁Morning。
            day_brief_ready = day_brief_confirmed or (
                settings.planning_mode.value == "auto_day"
                and isinstance(run.planning_json.get("dayBrief"), dict)
            )
            payload.update(
                {
                    "dayBrief": run.planning_json.get("dayBrief"),
                    "episodeDrafts": run.planning_json.get("episodeDrafts", {}),
                    "planningMetadata": run.planning_json.get("planningMetadata", {}),
                    "acceptedOutcomes": run.planning_json.get("acceptedOutcomes", {}),
                    "dayBriefConfirmed": day_brief_ready,
                    "currentStage": _current_stage(run, episodes, steps),
                }
            )
            payload["planningMode"] = settings.planning_mode.value
            payload["slotPlanning"] = _slot_planning_state(
                episodes,
                run.planning_json.get("acceptedOutcomes", {}),
                guided=settings.planning_mode.value == "guided_sequential",
                day_brief_confirmed=day_brief_ready,
            )
            if settings.planning_mode.value == "guided_sequential":
                payload["nextAction"] = _guided_next_action(
                    episodes,
                    run.planning_json.get("acceptedOutcomes", {}),
                    day_brief_confirmed=day_brief_ready,
                )
            return {
                "run": payload,
                "episodes": [episode_dict(item) for item in episodes],
                "steps": [step_dict(item) for item in steps],
                "prompts": [prompt_dict(item) for item in prompts],
                "assets": [asset_dict(item) for item in assets],
                "reviews": [review_dict(item) for item in reviews],
                "videoSequences": [_video_sequence_dict(item) for item in sequences],
                "workflowNodes": _workflow_nodes(
                    episodes,
                    steps,
                    prompts,
                    assets,
                    reviews,
                    run.planning_json.get("acceptedOutcomes", {}) or {},
                    guided=settings.planning_mode.value == "guided_sequential",
                    day_brief_confirmed=day_brief_ready,
                    stale_slots=tuple(run.planning_json.get("staleSlots", []) or ()),
                    stale_nodes=tuple(run.planning_json.get("staleNodes", []) or ()),
                ),
            }

    def get_outcome_source(self, run_id: uuid.UUID, slot: Slot) -> dict[str, Any]:
        """读取结果卡草稿所需的已批准视频及最新诊断，不解释剧情。"""

        with self._sessions() as session:
            run = required_record(session, ProductionRun, run_id)
            ensure_current_contract(run)
            episode = session.execute(
                select(Episode).where(
                    Episode.production_run_id == run_id,
                    Episode.slot == slot.value,
                )
            ).scalar_one_or_none()
            if episode is None:
                raise RecordNotFoundError(f"Run {run_id}没有{slot.value} Episode")
            diagnostic: dict[str, Any] = {}
            if episode.selected_video_asset_id is not None:
                review = session.execute(
                    select(Review)
                    .where(
                        Review.asset_id == episode.selected_video_asset_id,
                        Review.source == "ark_visual",
                    )
                    .order_by(Review.created_at.desc())
                ).scalars().first()
                if review is not None:
                    diagnostic = dict(review.evidence_json or {})
            return {
                "episodeStatus": episode.status,
                "script": EpisodeScript.model_validate(episode.script_json).model_dump(
                    mode="json"
                ),
                "diagnostic": diagnostic,
                "acceptedOutcome": (run.planning_json.get("acceptedOutcomes", {}) or {}).get(
                    slot.value
                ),
            }

    def get_planning_context(self, run_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            run = required_record(session, ProductionRun, run_id)
            ensure_current_contract(run)
            return dict(run.planning_json or {})

    @staticmethod
    def _rows_for_steps(
        session: Session,
        model: type[PromptRecord] | type[Review],
        step_ids: tuple[uuid.UUID, ...],
    ) -> tuple[Any, ...]:
        if not step_ids:
            return ()
        return tuple(
            session.execute(
                select(model).where(model.step_id.in_(step_ids)).order_by(model.created_at)
            ).scalars()
        )

    def list_run_summaries(self, limit: int, offset: int) -> list[dict[str, Any]]:
        with self._sessions() as session:
            rows = session.execute(
                select(ProductionRun)
                .where(ProductionRun.contract_version == CURRENT_CONTRACT_VERSION)
                .order_by(ProductionRun.content_date.desc(), ProductionRun.created_at.desc())
                .limit(limit)
                .offset(offset)
            ).scalars()
            return [run_dict(row) for row in rows]

    def list_recent_completed_summaries(
        self,
        *,
        limit: int,
    ) -> tuple[RecentContentSummary, ...]:
        if not 1 <= limit <= 6:
            raise ValueError("近期内容摘要 limit 必须在 1 至 6 之间")
        with self._sessions() as session:
            runs = tuple(
                session.execute(
                    select(ProductionRun)
                    .where(
                        ProductionRun.contract_version == CURRENT_CONTRACT_VERSION,
                        ProductionRun.status.in_((RunStatus.READY.value, RunStatus.DELIVERED.value))
                    )
                    .order_by(ProductionRun.content_date.desc(), ProductionRun.created_at.desc())
                    .limit(limit)
                ).scalars()
            )
            result: list[RecentContentSummary] = []
            for run in runs:
                rows = tuple(
                    session.execute(
                        select(Episode)
                        .where(Episode.production_run_id == run.id)
                        .order_by(Episode.sort_order)
                    ).scalars()
                )
                if len(rows) != 3:
                    continue
                scripts = [EpisodeScript.model_validate(row.script_json) for row in rows]
                result.append(
                    RecentContentSummary(
                        content_date=run.content_date,
                        event_keys=tuple(item.event_key for item in scripts),
                        location_keys=tuple(item.location_key for item in scripts),
                        summary_text=(
                            f"{run.content_date.isoformat()}主题="
                            f"{run.planning_json.get('dayBrief', {}).get('theme', '')}；"
                            f"剧情={'、'.join(item.story_text for item in scripts)}"
                        ),
                    )
                )
            return tuple(result)

    def prompt_detail(self, prompt_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            prompt = required_record(session, PromptRecord, prompt_id)
            step = required_record(session, WorkflowStep, prompt.step_id)
            ensure_current_contract(
                required_record(session, ProductionRun, step.production_run_id)
            )
            result = prompt_dict(prompt, full=True)
            result["inputSnapshot"] = step.input_snapshot_json
            return result

    def get_prompt_for_step(
        self,
        step_id: uuid.UUID,
        *,
        purpose: PromptPurpose,
    ) -> StoredPrompt:
        with self._sessions() as session:
            step = required_record(session, WorkflowStep, step_id)
            ensure_current_contract(
                required_record(session, ProductionRun, step.production_run_id)
            )
            row = session.execute(
                select(PromptRecord)
                .where(PromptRecord.step_id == step_id, PromptRecord.purpose == purpose.value)
                .order_by(PromptRecord.created_at.desc())
            ).scalar_one_or_none()
            if row is None:
                raise RecordNotFoundError(
                    f"Step {step_id} 不存在 purpose={purpose.value!r} 的 Prompt"
                )
            return stored_prompt(row)

    def asset_detail(self, asset_id: uuid.UUID) -> StoredAsset:
        with self._sessions() as session:
            asset = required_record(session, Asset, asset_id)
            if asset.production_run_id is not None:
                ensure_current_contract(
                    required_record(session, ProductionRun, asset.production_run_id)
                )
            return stored_asset(asset)

    def episode_detail(self, episode_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            episode = required_record(session, Episode, episode_id)
            ensure_current_contract(
                required_record(session, ProductionRun, episode.production_run_id)
            )
            return episode_dict(episode)

    def step_detail(self, step_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            step = required_record(session, WorkflowStep, step_id)
            ensure_current_contract(
                required_record(session, ProductionRun, step.production_run_id)
            )
            return step_dict(step)

    def step_trace(self, step_id: uuid.UUID) -> dict[str, Any]:
        """返回节点追踪投影；大正文只在打开抽屉时读取，不膨胀Run Graph。"""

        with self._sessions() as session:
            current = required_record(session, WorkflowStep, step_id)
            ensure_current_contract(
                required_record(session, ProductionRun, current.production_run_id)
            )
            attempt_query = select(WorkflowStep).where(
                WorkflowStep.production_run_id == current.production_run_id,
                WorkflowStep.operation_key == current.operation_key,
            )
            if current.episode_id is None:
                attempt_query = attempt_query.where(WorkflowStep.episode_id.is_(None))
            else:
                attempt_query = attempt_query.where(WorkflowStep.episode_id == current.episode_id)
            attempts = tuple(
                session.execute(attempt_query.order_by(WorkflowStep.attempt)).scalars()
            )
            attempt_ids = tuple(item.id for item in attempts)
            prompts = self._rows_for_steps(session, PromptRecord, attempt_ids)
            reviews = self._rows_for_steps(session, Review, attempt_ids)
            assets = (
                ()
                if not attempt_ids
                else tuple(
                    session.execute(
                        select(Asset)
                        .where(Asset.producing_step_id.in_(attempt_ids))
                        .order_by(Asset.created_at)
                    ).scalars()
                )
            )
            snapshot = dict(current.input_snapshot_json or {})
            provider_output = snapshot.get("provider_output")
            normalized_output = snapshot.get("normalized_output")
            input_summary = {
                key: value
                for key, value in snapshot.items()
                if key
                not in {
                    "provider_output",
                    "normalized_output",
                    "normalization_warnings",
                    "response_id",
                    "request_hash",
                }
            }
            return {
                "step": step_dict(current),
                "inputSummary": input_summary,
                "actualPrompts": [prompt_dict(item, full=True) for item in prompts],
                "providerOutput": provider_output,
                "normalizedOutput": normalized_output,
                "effectiveOutput": normalized_output or provider_output,
                "normalizationWarnings": snapshot.get("normalization_warnings", []),
                "inputBindings": _trace_input_bindings(snapshot),
                "assets": [asset_dict(item) for item in assets],
                "reviews": [review_dict(item) for item in reviews],
                "attempts": [step_dict(item) for item in attempts],
            }

    def list_delivery_packages(self, run_id: uuid.UUID) -> list[dict[str, Any]]:
        with self._sessions() as session:
            rows = tuple(
                session.execute(
                    select(DeliveryPackage)
                    .where(DeliveryPackage.production_run_id == run_id)
                    .order_by(DeliveryPackage.revision.desc())
                ).scalars()
            )
            return [self._delivery_dict(session, row) for row in rows]

    def delivery_package_detail(self, package_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            return self._delivery_dict(
                session,
                required_record(session, DeliveryPackage, package_id),
            )

    @staticmethod
    def _delivery_dict(session: Session, package: DeliveryPackage) -> dict[str, Any]:
        items = tuple(
            session.execute(
                select(DeliveryItem)
                .where(DeliveryItem.delivery_package_id == package.id)
                .order_by(DeliveryItem.sort_order)
            ).scalars()
        )
        return delivery_package_dict(package, items)

    def health(self) -> dict[str, Any]:
        with self._sessions() as session:
            database = session.execute(text("SELECT current_database()")).scalar_one()
            user = session.execute(text("SELECT current_user")).scalar_one()
            revision = session.execute(
                text("SELECT version_num FROM cat_video.alembic_version")
            ).scalar_one_or_none()
            return {
                "database": database,
                "user": user,
                "alembicRevision": revision,
                "expectedAlembicRevision": ALEMBIC_HEAD,
                "ready": revision == ALEMBIC_HEAD,
            }
