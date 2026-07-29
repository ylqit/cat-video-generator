"""本地前端的写操作与后台任务HTTP端点。

这里只做参数解析、错误映射和任务登记，业务判断仍由Application Service
完成；付费许可按请求逐次透传，与CLI的--allow-paid-generation语义一致。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from ..application.ports import GatewayError
from ..domain.contracts import Slot
from .jobs import JobConflictError, JobRecord, JobRegistry

_CANON_ROLES = frozenset({"person", "cat", "style"})
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_DEFAULT_PLANNING_CONTEXT = "根据日期、天气和角色习惯设计自然的一天。"


class PlanRequest(BaseModel):
    """触发全天规划的请求体；付费许可缺省为拒绝。"""

    model_config = ConfigDict(populate_by_name=True)

    target_date: date = Field(alias="targetDate")
    planning_context: str | None = Field(None, alias="planningContext")
    candidate_count: int | None = Field(None, alias="candidateCount", ge=1, le=5)
    allow_paid_generation: bool = Field(False, alias="allowPaidGeneration")


class GenerateRequest(BaseModel):
    """按时段或全天生成的请求体；slot为空表示推进全天。"""

    model_config = ConfigDict(populate_by_name=True)

    slot: Slot | None = None
    allow_paid_generation: bool = Field(False, alias="allowPaidGeneration")


class ReviewRequest(BaseModel):
    """人工审核决定；理由必填以保留审计线索。"""

    approve: bool
    reason: str = Field(min_length=1, max_length=500)


def create_write_router(
    *,
    planning: Any,
    production: Any,
    assets: Any,
    delivery: Any,
    queries: Any,
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
            return {
                "runId": str(result.run_id),
                "selectedCandidate": result.selected_candidate,
                "candidateCount": result.candidate_count,
                "plan": result.plan.model_dump(mode="json"),
            }

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
                lambda: assets.import_canon(role=role, path=temporary)
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
