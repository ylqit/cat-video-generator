"""本地前端的写操作与后台任务HTTP端点。

这里只做参数解析、错误映射和任务登记，业务判断仍由Application Service
完成；付费许可按请求逐次透传，与CLI的--allow-paid-generation语义一致。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..application.ports import GatewayError
from ..domain.contracts import Slot
from .api_schemas import (
    CANON_ROLES as _CANON_ROLES,
    DEFAULT_PLANNING_CONTEXT as _DEFAULT_PLANNING_CONTEXT,
    IMAGE_SUFFIXES as _IMAGE_SUFFIXES,
    REFERENCE_ROLES as _REFERENCE_ROLES,
    REFERENCE_SUFFIXES as _REFERENCE_SUFFIXES,
    CompareResolutionRequest,
    DeriveCropRequest,
    GenerateKeyframesRequest,
    GenerateRequest,
    PaidRequest,
    PlanRequest,
    PromptOverridesRequest,
    ReplanRequest,
    RetryStepRequest,
    ReviewRequest,
)
from .jobs import JobConflictError, JobRecord, JobRegistry


def create_write_router(
    *,
    planning: Any,
    production: Any,
    assets: Any,
    delivery: Any,
    queries: Any,
    retry: Any,
    resolution_compare: Any,
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

        def task() -> dict[str, Any]:
            result = planning.plan_day(
                target_date=request.target_date,
                planning_context=(
                    request.planning_context or _DEFAULT_PLANNING_CONTEXT
                ),
                candidate_count=(
                    request.candidate_count
                    if request.candidate_count is not None
                    else default_candidate_count
                ),
                allow_paid_generation=True,
            )
            payload: dict[str, Any] = {
                "runId": str(result.run_id),
                "selectedCandidate": result.selected_candidate,
                "candidateCount": result.candidate_count,
                "plan": result.plan.model_dump(mode="json"),
            }
            if request.auto_generate_keyframes:
                # 同一付费串行门内链式生成三集首末帧；创作台默认路径。
                payload["keyframes"] = production.prepare_keyframes_only(
                    result.run_id,
                    allow_paid_generation=True,
                    allow_unverified_keyframes=(
                        request.allow_unverified_keyframes
                    ),
                )
            return payload

        record = _submit(
            job_registry,
            kind="plan_day",
            dedup_key=f"plan:{request.target_date.isoformat()}",
            fn=task,
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
                allow_unverified_keyframes=request.allow_unverified_keyframes,
                allow_multi_clip=request.allow_multi_clip,
            )

        record = _submit(
            job_registry,
            kind="run_day",
            dedup_key=f"run:{run_id}:{slot_label}",
            fn=task,
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
        )
        return _accepted(record)

    @router.post("/assets/{asset_id}/review")
    async def review(asset_id: uuid.UUID, request: ReviewRequest) -> dict:
        return await _run_sync(
            lambda: assets.review_asset(
                asset_id,
                approve=request.approve,
                reason=request.reason,
            )
        )

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

        def task() -> dict[str, Any]:
            result = retry.retry_step(
                step_id,
                reason=request.reason,
                allow_paid_generation=True,
                allow_unverified_keyframes=request.allow_unverified_keyframes,
            )
            return _jsonable(result)

        record = _submit(
            job_registry,
            kind="retry_step",
            dedup_key=f"retry:{step_id}",
            fn=task,
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
            )
            return _jsonable(result)

        record = _submit(
            job_registry,
            kind="replan_episode",
            dedup_key=f"replan:{run_id}:{slot.value}",
            fn=task,
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
                detail="参考资产role必须是element、scene、motion或atmosphere",
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

    @router.post("/runs/{run_id}/compare-resolution", status_code=202)
    def compare_resolution(
        run_id: uuid.UUID,
        request: CompareResolutionRequest,
    ) -> dict[str, Any]:
        if not request.allow_paid_generation:
            raise HTTPException(
                status_code=422,
                detail="分辨率对比调用付费模型，必须显式确认allowPaidGeneration",
            )

        def task() -> dict[str, Any]:
            result = resolution_compare.compare(
                run_id,
                resolution=request.resolution,
                allow_paid_generation=True,
                allow_multi_clip=request.allow_multi_clip,
            )
            return _jsonable(result)

        record = _submit(
            job_registry,
            kind="compare_resolution",
            dedup_key=f"compare:{run_id}:{request.resolution}",
            fn=task,
        )
        return _accepted(record)

    @router.put("/episodes/{episode_id}/prompt-overrides")
    async def save_prompt_overrides(
        episode_id: uuid.UUID,
        request: PromptOverridesRequest,
    ) -> dict[str, Any]:
        await _run_sync(
            lambda: production.save_prompt_overrides(
                episode_id,
                overrides=request.overrides,
            )
        )
        return {"episodeId": str(episode_id), "saved": True}

    @router.post("/episodes/{episode_id}/keyframes", status_code=202)
    def generate_keyframes(
        episode_id: uuid.UUID,
        request: GenerateKeyframesRequest,
    ) -> dict[str, Any]:
        if not request.allow_paid_generation:
            raise HTTPException(
                status_code=422,
                detail="关键帧生成调用付费模型，必须显式确认allowPaidGeneration",
            )
        try:
            episode = queries.episode(episode_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        run_id = uuid.UUID(episode["runId"])
        slot = Slot(episode["slot"])
        overrides = request.overrides

        def task() -> dict[str, Any]:
            return production.prepare_keyframes_only(
                run_id,
                slot=slot,
                prompt_overrides=(
                    None if overrides is None else {slot.value: overrides}
                ),
                allow_paid_generation=True,
                allow_unverified_keyframes=request.allow_unverified_keyframes,
            )

        record = _submit(
            job_registry,
            kind="prepare_keyframes",
            dedup_key=f"keyframes:{episode_id}",
            fn=task,
        )
        return _accepted(record)

    return router


def _submit(
    registry: JobRegistry,
    *,
    kind: str,
    dedup_key: str,
    fn: Callable[[], Any],
) -> JobRecord:
    """登记后台任务并把去重冲突映射为409。"""

    try:
        return registry.submit(kind=kind, dedup_key=dedup_key, fn=fn)
    except JobConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "jobId": exc.job_id},
        ) from exc


def _accepted(record: JobRecord) -> dict[str, Any]:
    return {
        "jobId": record.job_id,
        "kind": record.kind,
        "dedupKey": record.dedup_key,
        "status": record.status,
    }


def _jsonable(result: Any) -> Any:
    """把Service返回值规整为可JSON序列化结构。"""

    if result is None or isinstance(result, (str, int, float, bool)):
        return result
    if isinstance(result, uuid.UUID):
        return str(result)
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if hasattr(result, "_asdict"):
        return _jsonable(result._asdict())
    if isinstance(result, dict):
        return {str(key): _jsonable(value) for key, value in result.items()}
    if isinstance(result, (list, tuple)):
        return [_jsonable(item) for item in result]
    return str(result)


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