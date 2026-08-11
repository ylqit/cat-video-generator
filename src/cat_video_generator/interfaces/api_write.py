"""本地前端的写操作与后台任务HTTP端点。

这里只做参数解析、错误映射和任务登记，业务判断仍由Application Service
完成；付费许可按请求逐次透传，与CLI的--allow-paid-generation语义一致。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..application.planning import DayBriefPause
from ..application.ports import GatewayError
from ..domain.contracts import Slot
from ..domain.pipeline import PipelineSettings, StageMode
from ..domain.rendering import BoundaryMode
from .api_helpers import _accepted, _jsonable, _submit
from .api_schemas import (
    CANON_ROLES as _CANON_ROLES,
)
from .api_schemas import (
    DEFAULT_PLANNING_CONTEXT as _DEFAULT_PLANNING_CONTEXT,
)
from .api_schemas import (
    IMAGE_SUFFIXES as _IMAGE_SUFFIXES,
)
from .api_schemas import (
    REFERENCE_ROLES as _REFERENCE_ROLES,
)
from .api_schemas import (
    REFERENCE_SUFFIXES as _REFERENCE_SUFFIXES,
)
from .api_schemas import (
    DeriveCropRequest,
    GenerateRequest,
    PaidRequest,
    PlanRequest,
    PromptOverridesRequest,
    RangeEditRequest,
    ReconcileStepRequest,
    RegenerateStepRequest,
    ReplanRequest,
    RetryStepRequest,
    ReviewRequest,
    SelectVideoSequenceRequest,
)
from .api_studio import (
    build_plan_payload,
    chain_after_planning,
    maybe_continue_media,
)
from .jobs import JobRegistry


def create_write_router(
    *,
    planning: Any,
    production: Any,
    assets: Any,
    delivery: Any,
    queries: Any,
    retry: Any,
    regeneration: Any,
    video_editing: Any,
    job_registry: JobRegistry,
    default_candidate_count: int,
    upload_dir: Path,
    delivery_root: Path,
) -> APIRouter:
    """创建写操作Router；依赖按Application Service注入便于测试。"""

    router = APIRouter(prefix="/api/v1")
    resolved_delivery_root = delivery_root.expanduser().resolve()

    @router.post("/plans", status_code=202)
    def create_plan(request: PlanRequest) -> dict[str, Any]:
        if not request.allow_paid_generation:
            raise HTTPException(
                status_code=422,
                detail="规划调用付费模型，必须显式确认allowPaidGeneration",
            )
        settings = (
            PipelineSettings.model_validate(request.pipeline_settings)
            if request.pipeline_settings
            # 未显式给流水线开关时，视觉锚点自动推进，视频等待人工确认。
            else PipelineSettings(
                allow_paid_generation=True,
                visual=StageMode.AUTO,
                video=StageMode.MANUAL,
            )
        )

        def task() -> dict[str, Any]:
            result = planning.plan_day(
                target_date=request.target_date,
                planning_context=(request.planning_context or _DEFAULT_PLANNING_CONTEXT),
                candidate_count=(
                    request.candidate_count
                    if request.candidate_count is not None
                    else default_candidate_count
                ),
                allow_paid_generation=True,
                creative_profile=(
                    request.creative_profile.to_domain()
                    if request.creative_profile is not None
                    else None
                ),
                stop_after_day_brief=settings.day_brief is StageMode.MANUAL,
                pipeline_settings=settings,
                story_mode=request.story_mode,
                creative_controls=request.creative_controls,
            )
            payload = build_plan_payload(result)
            if isinstance(result, DayBriefPause):
                return payload
            # 同一付费串行门内按流水线开关链式推进；创作台默认路径。
            return chain_after_planning(production, result.run_id, settings, payload)

        record = _submit(
            job_registry,
            kind="plan_day",
            dedup_key=f"plan:{request.target_date.isoformat()}",
            fn=task,
            context={"operationKey": "director:day"},
        )
        return _accepted(record)

    @router.post("/runs/{run_id}/generate", status_code=202)
    def generate(run_id: uuid.UUID, request: GenerateRequest) -> dict[str, Any]:
        if not request.allow_paid_generation:
            raise HTTPException(
                status_code=422,
                detail="媒体生成必须显式确认allowPaidGeneration",
            )
        slot_label = request.slot.value if request.slot is not None else "all"

        def task() -> dict[str, Any]:
            return production.run_day(
                run_id,
                slot=request.slot,
                allow_paid_generation=True,
            )

        record = _submit(
            job_registry,
            kind="run_day",
            dedup_key=f"run:{run_id}:{slot_label}",
            fn=task,
            context={"runId": run_id, "slot": slot_label},
        )
        return _accepted(record)

    @router.post("/runs/{run_id}/slots/{slot}/plan", status_code=202)
    def plan_slot(
        run_id: uuid.UUID,
        slot: Slot,
        request: PaidRequest,
    ) -> dict[str, Any]:
        """顺序人工模式只调用当前已解锁时段的导演。"""

        if not request.allow_paid_generation:
            raise HTTPException(
                status_code=422,
                detail="时段导演调用付费模型，必须显式确认allowPaidGeneration",
            )

        def task() -> dict[str, Any]:
            return _jsonable(
                planning.plan_slot(
                    run_id,
                    slot=slot,
                    allow_paid_generation=True,
                )
            )

        record = _submit(
            job_registry,
            kind="plan_slot",
            dedup_key=f"plan-slot:{run_id}:{slot.value}",
            fn=task,
            context={
                "runId": run_id,
                "slot": slot.value,
                "operationKey": f"director:episode:{slot.value}",
            },
        )
        return _accepted(record)

    @router.post("/runs/{run_id}/resume", status_code=202)
    def resume(run_id: uuid.UUID) -> dict[str, Any]:
        def task() -> list[dict[str, Any]]:
            return production.resume(run_id)

        record = _submit(
            job_registry,
            kind="resume",
            dedup_key=f"resume:{run_id}",
            fn=task,
            context={"runId": run_id},
        )
        return _accepted(record)

    @router.post("/assets/{asset_id}/review")
    async def review(asset_id: uuid.UUID, request: ReviewRequest) -> dict:
        result = await _run_sync(
            lambda: assets.review_asset(
                asset_id,
                approve=request.approve,
                reason=request.reason,
            )
        )
        if request.approve:
            # 定妆图或开场锚点批准后按流水线设置推进；最终视频仍由人工审核。
            await asyncio.to_thread(
                lambda: maybe_continue_media(
                    queries=queries,
                    production=production,
                    job_registry=job_registry,
                    asset_id=asset_id,
                )
            )
        return result

    @router.post("/canon", status_code=201)
    async def import_canon(
        role: str = Form(...),  # noqa: B008
        semantic_key: str = Form(...),  # noqa: B008
        view: str | None = Form(None),  # noqa: B008
        file: UploadFile = File(...),  # noqa: B008
    ) -> dict:
        if role not in _CANON_ROLES:
            raise HTTPException(
                status_code=422,
                detail="Canon role必须是person、cat或style",
            )
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in _IMAGE_SUFFIXES:
            raise HTTPException(
                status_code=422,
                detail="Canon文件必须是png、jpg、jpeg或webp图片",
            )
        upload_dir.mkdir(parents=True, exist_ok=True)
        temporary = upload_dir / f"{uuid.uuid4().hex}{suffix}"
        try:
            temporary.write_bytes(await file.read())
            return await _run_sync(
                lambda: assets.import_canon(
                    role=role,
                    path=temporary,
                    semantic_key=semantic_key,
                    view=view,
                )
            )
        finally:
            temporary.unlink(missing_ok=True)

    @router.get("/canon")
    def list_canon() -> list[dict[str, Any]]:
        return [
            {**asset, "contentUrl": f"/api/v1/assets/{asset['id']}/content"}
            for asset in queries.list_canon()
        ]

    @router.post("/runs/{run_id}/deliver")
    async def deliver(run_id: uuid.UUID) -> dict:
        return await _run_sync(lambda: delivery.deliver(run_id))

    @router.get("/runs/{run_id}/deliveries")
    def list_deliveries(run_id: uuid.UUID) -> list[dict[str, Any]]:
        return queries.list_deliveries(run_id)

    @router.get("/deliveries/{package_id}/manifest")
    def delivery_manifest(package_id: uuid.UUID) -> Any:
        detail = _not_found(lambda: queries.delivery_detail(package_id))
        manifest = Path(detail["localPath"]).expanduser().resolve() / ("manifest.json")
        if not manifest.is_relative_to(resolved_delivery_root):
            raise HTTPException(
                status_code=403,
                detail="交付包路径不属于允许的交付目录。",
            )
        if not manifest.is_file():
            raise HTTPException(status_code=404, detail="manifest.json不存在。")
        return json.loads(manifest.read_text(encoding="utf-8"))

    @router.get("/jobs")
    def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
        return [record.to_dict() for record in job_registry.list(limit=limit)]

    @router.get("/jobs/{job_id}")
    def job_detail(job_id: str) -> dict[str, Any]:
        return _not_found(lambda: job_registry.get(job_id).to_dict())

    @router.post("/steps/{step_id}/retry", status_code=202)
    def retry_step(step_id: uuid.UUID, request: RetryStepRequest) -> dict[str, Any]:
        if not request.allow_paid_generation:
            raise HTTPException(
                status_code=422,
                detail="重试可能调用付费模型，必须显式确认allowPaidGeneration",
            )

        try:
            retry_target = queries.step(step_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        def task() -> dict[str, Any]:
            result = retry.retry_step(
                step_id,
                reason=request.reason,
                allow_paid_generation=True,
                acknowledge_duplicate_billing=request.acknowledge_duplicate_billing,
                restart_from_beginning=request.restart_from_beginning,
            )
            return _jsonable(result)

        record = _submit(
            job_registry,
            kind="retry_step",
            dedup_key=f"retry:{step_id}",
            fn=task,
            context={
                "runId": retry_target.get("runId"),
                "episodeId": retry_target.get("episodeId"),
                "operationKey": retry_target.get("operationKey"),
            },
        )
        return _accepted(record)

    @router.post("/steps/{step_id}/regenerate", status_code=202)
    def regenerate_step(
        step_id: uuid.UUID,
        request: RegenerateStepRequest,
    ) -> dict[str, Any]:
        """保留旧attempt并创建节点的新版本；不覆盖既有正式媒体。"""

        if not request.allow_paid_generation:
            raise HTTPException(
                status_code=422,
                detail="节点重生成必须显式确认allowPaidGeneration",
            )
        try:
            target = queries.step(step_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        def task() -> dict[str, Any]:
            return _jsonable(
                regeneration.regenerate_step(
                    step_id,
                    reason=request.reason,
                    prompt_override=request.prompt_override,
                    allow_paid_generation=True,
                    acknowledge_downstream_replacement=(
                        request.acknowledge_downstream_replacement
                    ),
                )
            )

        record = _submit(
            job_registry,
            kind="regenerate_step",
            dedup_key=f"regenerate:{step_id}",
            fn=task,
            context={
                "runId": target.get("runId"),
                "episodeId": target.get("episodeId"),
                "operationKey": target.get("operationKey"),
            },
        )
        return _accepted(record)

    @router.post(
        "/episodes/{episode_id}/video-sequences/{sequence_id}/range-edits",
        status_code=202,
    )
    def range_edit(
        episode_id: uuid.UUID,
        sequence_id: uuid.UUID,
        request: RangeEditRequest,
    ) -> dict[str, Any]:
        if not request.allow_paid_generation:
            raise HTTPException(
                status_code=422,
                detail="视频区间重生成必须显式确认allowPaidGeneration",
            )
        try:
            episode = queries.episode(episode_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        def task() -> dict[str, Any]:
            return _jsonable(
                video_editing.range_edit(
                    episode_id,
                    sequence_id,
                    start_ms=request.start_ms,
                    end_ms=request.end_ms,
                    boundary_mode=BoundaryMode(request.boundary_mode),
                    instruction=request.instruction,
                    allow_paid_generation=True,
                )
            )

        record = _submit(
            job_registry,
            kind="video_range_edit",
            dedup_key=(
                f"range-edit:{sequence_id}:{request.start_ms}:{request.end_ms}:"
                f"{request.boundary_mode}:"
                f"{hashlib.sha256(request.instruction.strip().encode('utf-8')).hexdigest()[:12]}"
            ),
            fn=task,
            context={
                "runId": episode.get("runId"),
                "episodeId": episode_id,
                "operationKey": "video:range_edit",
            },
        )
        return _accepted(record)

    @router.post("/video-sequences/{sequence_id}/select")
    def select_video_sequence(
        sequence_id: uuid.UUID,
        request: SelectVideoSequenceRequest,
    ) -> dict[str, Any]:
        try:
            return video_editing.select_sequence(
                sequence_id,
                revoke_confirmed_outcome=request.revoke_confirmed_outcome,
                keep_confirmed_outcome=request.keep_confirmed_outcome,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/steps/{step_id}/resume", status_code=202)
    def resume_step(step_id: uuid.UUID) -> dict[str, Any]:
        """继续查询已有Ark Task ID；不创建新的供应商生成请求。"""

        try:
            target = queries.step(step_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        def task() -> dict[str, Any]:
            return _jsonable(retry.resume_step(step_id))

        record = _submit(
            job_registry,
            kind="resume_step",
            dedup_key=f"resume-step:{step_id}",
            fn=task,
            context={
                "runId": target.get("runId"),
                "episodeId": target.get("episodeId"),
                "operationKey": target.get("operationKey"),
            },
        )
        return _accepted(record)

    @router.get("/steps/{step_id}/reconciliation-candidates")
    def reconciliation_candidates(step_id: uuid.UUID) -> list[dict[str, Any]]:
        try:
            return list(retry.reconciliation_candidates(step_id))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except GatewayError as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc

    @router.post("/steps/{step_id}/reconcile", status_code=202)
    def reconcile_step(
        step_id: uuid.UUID,
        request: ReconcileStepRequest,
    ) -> dict[str, Any]:
        try:
            target = queries.step(step_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        def task() -> dict[str, Any]:
            return _jsonable(
                retry.reconcile_step(
                    step_id,
                    provider_task_id=request.provider_task_id,
                )
            )

        record = _submit(
            job_registry,
            kind="reconcile_step",
            dedup_key=f"reconcile:{step_id}:{request.provider_task_id}",
            fn=task,
            context={
                "runId": target.get("runId"),
                "episodeId": target.get("episodeId"),
                "operationKey": target.get("operationKey"),
            },
        )
        return _accepted(record)

    @router.post("/runs/{run_id}/resume-planning", status_code=202)
    def resume_planning(
        run_id: uuid.UUID,
        request: PaidRequest,
    ) -> dict[str, Any]:
        if not request.allow_paid_generation:
            raise HTTPException(
                status_code=422,
                detail="恢复规划可能调用付费模型，必须显式确认allowPaidGeneration",
            )

        def task() -> dict[str, Any]:
            result = planning.resume_planning(
                run_id,
                allow_paid_generation=True,
            )
            return _jsonable(result)

        record = _submit(
            job_registry,
            kind="resume_planning",
            dedup_key=f"resume-planning:{run_id}",
            fn=task,
            context={"runId": run_id, "operationKey": "director:resume"},
        )
        return _accepted(record)

    @router.post("/runs/{run_id}/episodes/{slot}/replan", status_code=202)
    def replan_episode(
        run_id: uuid.UUID,
        slot: Slot,
        request: ReplanRequest,
    ) -> dict[str, Any]:
        if not request.allow_paid_generation:
            raise HTTPException(
                status_code=422,
                detail="局部重规划调用付费模型，必须显式确认allowPaidGeneration",
            )

        def task() -> dict[str, Any]:
            result = planning.replan_episode(
                run_id,
                slot=slot,
                reason=request.reason,
                allow_paid_generation=True,
                acknowledge_downstream_replacement=(
                    request.acknowledge_downstream_replacement
                ),
            )
            payload: dict[str, Any] = {"replan": _jsonable(result)}
            status = str(queries.run_graph(run_id)["run"]["status"])
            if status == "draft":
                # 局部重规划修复了当前时段后，继续补齐尚未生成的其余时段。
                # 复用PlanningService的恢复入口，避免接口层复制导演循环。
                payload["planning"] = _jsonable(
                    planning.resume_planning(
                        run_id,
                        allow_paid_generation=True,
                    )
                )
                status = "planned"
            # 方案已定稿时按流水线开关自动推进后续节点，与plan job行为一致；
            # 未定稿（回draft等剩余时段）只返回重规划结果。
            if status in {"planned", "generating", "reviewing"}:
                settings = queries.pipeline_settings(run_id)
                payload["runId"] = str(run_id)
                return chain_after_planning(
                    production,
                    run_id,
                    settings,
                    payload,
                    slot=slot,
                )
            return payload

        record = _submit(
            job_registry,
            kind="replan_episode",
            dedup_key=f"replan:{run_id}:{slot.value}",
            fn=task,
            context={
                "runId": run_id,
                "slot": slot.value,
                "operationKey": f"director:episode:{slot.value}",
            },
        )
        return _accepted(record)

    @router.post("/episodes/{episode_id}/references", status_code=201)
    async def import_episode_reference(
        episode_id: uuid.UUID,
        role: str = Form(...),  # noqa: B008
        semantic_key: str = Form(...),  # noqa: B008
        file: UploadFile = File(...),  # noqa: B008
    ) -> dict:
        if role not in _REFERENCE_ROLES:
            raise HTTPException(
                status_code=422,
                detail="参考资产role必须是element或scene",
            )
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in _REFERENCE_SUFFIXES[role]:
            raise HTTPException(
                status_code=422,
                detail=f"{role}参考资产不允许{suffix or '无扩展名'}格式",
            )
        upload_dir.mkdir(parents=True, exist_ok=True)
        temporary = upload_dir / f"{uuid.uuid4().hex}{suffix}"
        try:
            temporary.write_bytes(await file.read())
            return await _run_sync(
                lambda: assets.import_episode_reference(
                    episode_id=episode_id,
                    role=role,
                    path=temporary,
                    semantic_key=semantic_key,
                )
            )
        finally:
            temporary.unlink(missing_ok=True)

    @router.post("/canon/{asset_id}/derive-crop", status_code=201)
    async def derive_crop(
        asset_id: uuid.UUID,
        request: DeriveCropRequest,
    ) -> dict:
        return await _run_sync(
            lambda: assets.derive_canon_crop(
                source_asset_id=asset_id,
                role=request.role,
                box=request.box,
                subject_free=request.subject_free,
                semantic_key=request.semantic_key,
                view=request.view,
            )
        )

    @router.put("/episodes/{episode_id}/prompt-overrides")
    async def save_prompt_overrides(
        episode_id: uuid.UUID,
        request: PromptOverridesRequest,
    ) -> dict[str, Any]:
        await _run_sync(
            lambda: production.save_prompt_overrides(
                episode_id,
                overrides=request.overrides,
                enabled=request.enabled,
            )
        )
        return {"episodeId": str(episode_id), "saved": True}

    @router.post("/episodes/{episode_id}/visuals", status_code=202)
    def generate_visuals(
        episode_id: uuid.UUID,
        request: PaidRequest,
    ) -> dict[str, Any]:
        if not request.allow_paid_generation:
            raise HTTPException(
                status_code=422,
                detail="视觉锚点生成调用付费模型，必须显式确认allowPaidGeneration",
            )
        try:
            episode = queries.episode(episode_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        run_id = uuid.UUID(episode["runId"])
        slot = Slot(episode["slot"])

        def task() -> dict[str, Any]:
            return production.prepare_visuals_only(
                run_id,
                slot=slot,
                allow_paid_generation=True,
            )

        record = _submit(
            job_registry,
            kind="prepare_visuals",
            dedup_key=f"visuals:{episode_id}",
            fn=task,
            context={
                "runId": run_id,
                "episodeId": episode_id,
                "slot": slot.value,
                "operationKey": "image:look",
            },
        )
        return _accepted(record)

    return router


async def _run_sync(operation: Callable[[], Any]) -> Any:
    """把短事务同步用例放进线程，避免阻塞事件循环。"""

    try:
        return await asyncio.to_thread(operation)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GatewayError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


def _not_found(operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
