"""统一生产工作台的阶段续跑与人工编辑端点。"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import ValidationError

from ..application.planning import DayBriefPause, PlanningResult
from ..application.ports import GatewayError
from ..application.queries import QueryService
from ..domain.contracts import Slot
from ..domain.pipeline import PipelineSettings, StageMode
from .api_helpers import _accepted, _jsonable, _submit
from .jobs import JobConflictError, JobRegistry


def chain_after_planning(
    production: Any,
    run_id: uuid.UUID,
    settings: PipelineSettings,
    payload: dict[str, Any],
    *,
    slot: Slot | None = None,
) -> dict[str, Any]:
    """按持久化阶段开关顺序推进视觉锚点和视频。"""

    stages = payload.setdefault("stages", {})
    if settings.script is StageMode.MANUAL:
        payload["pausedAt"] = "script"
        return payload
    if settings.visual is StageMode.MANUAL:
        payload["pausedAt"] = "visual"
        return payload
    visual = production.prepare_visuals_only(
        run_id,
        slot=slot,
        allow_paid_generation=True,
    )
    stages["visual"] = visual
    if not all(item["visualReady"] for item in visual["episodes"]):
        payload["pausedAt"] = "visual"
        return payload
    if settings.video is StageMode.MANUAL or not settings.allow_paid_generation:
        payload["pausedAt"] = "video"
        return payload
    stages["video"] = production.run_day(
        run_id,
        slot=slot,
        allow_paid_generation=True,
    )
    return payload


def maybe_continue_media(
    *,
    queries: QueryService,
    production: Any,
    job_registry: JobRegistry,
    asset_id: uuid.UUID,
) -> None:
    """图片批准后按 Run 级授权继续同一 Episode，不替人工批准最终视频。"""

    try:
        asset = queries.asset(asset_id)
    except LookupError:
        return
    if asset.role not in {"look_reference", "opening_anchor"} or asset.episode_id is None:
        return
    episode = queries.episode(asset.episode_id)
    run_id = uuid.UUID(episode["runId"])
    settings = queries.pipeline_settings(run_id)
    if settings.visual is not StageMode.AUTO or not settings.allow_paid_generation:
        return
    slot = Slot(episode["slot"])

    def task() -> dict[str, Any]:
        if asset.role == "look_reference":
            return production.prepare_visuals_only(
                run_id,
                slot=slot,
                allow_paid_generation=True,
            )
        if settings.video is StageMode.AUTO:
            return production.run_day(run_id, slot=slot, allow_paid_generation=True)
        return {"runId": str(run_id), "pausedAt": "video"}

    with suppress(JobConflictError):
        job_registry.submit(
            kind="continue_media",
            dedup_key=f"continue-media:{asset.id}",
            fn=task,
            context={
                "runId": run_id,
                "episodeId": asset.episode_id,
                "slot": slot.value,
                "operationKey": (
                    "image:opening_anchor"
                    if asset.role == "look_reference"
                    else "video:single_pass"
                ),
            },
        )


def create_studio_router(
    *,
    planning: Any,
    production: Any,
    queries: QueryService,
    studio_editing: Any,
    job_registry: JobRegistry,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.post("/runs/{run_id}/continue", status_code=202)
    def continue_pipeline(run_id: uuid.UUID) -> dict[str, Any]:
        try:
            settings = queries.pipeline_settings(run_id)
            status = str(queries.run_graph(run_id)["run"]["status"])
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        def task() -> dict[str, Any]:
            if status in {"draft", "failed"}:
                result = planning.resume_planning(run_id, allow_paid_generation=True)
                payload: dict[str, Any] = {
                    "runId": str(run_id),
                    "stages": {"planning": _jsonable(result)},
                }
            elif status in {"planned", "generating", "reviewing"}:
                payload = {"runId": str(run_id), "stages": {}}
            else:
                raise ValueError(f"Run 状态 {status} 不支持流水线续跑")
            return chain_after_planning(production, run_id, settings, payload)

        record = _submit(
            job_registry,
            kind="continue_pipeline",
            dedup_key=f"continue:{run_id}",
            fn=task,
            context={"runId": run_id},
        )
        return _accepted(record)

    @router.put("/episodes/{episode_id}/script")
    async def update_episode_script(
        episode_id: uuid.UUID,
        payload: dict[str, Any] = Body(...),  # noqa: B008
    ) -> dict[str, Any]:
        return await _run_validated(
            lambda: studio_editing.update_episode_script(episode_id, payload)
        )

    @router.post("/episodes/{episode_id}/prompt-preview")
    async def preview_episode_prompt(
        episode_id: uuid.UUID,
        payload: dict[str, Any] = Body(...),  # noqa: B008
    ) -> dict[str, Any]:
        """用尚未保存的结构化脚本编译实时Prompt；不落库、不调用Ark。"""

        return await _run_validated(
            lambda: queries.prompt_preview(episode_id, script_override=payload)
        )

    @router.put("/runs/{run_id}/day-brief")
    async def update_day_brief(
        run_id: uuid.UUID,
        payload: dict[str, Any] = Body(...),  # noqa: B008
    ) -> dict[str, Any]:
        return await _run_validated(lambda: studio_editing.update_day_brief(run_id, payload))

    @router.put("/runs/{run_id}/pipeline-settings")
    async def update_pipeline_settings(
        run_id: uuid.UUID,
        payload: dict[str, Any] = Body(...),  # noqa: B008
    ) -> dict[str, Any]:
        return await _run_validated(
            lambda: studio_editing.update_pipeline_settings(run_id, payload)
        )

    return router


def build_plan_payload(result: PlanningResult | DayBriefPause) -> dict[str, Any]:
    if isinstance(result, DayBriefPause):
        return {
            "runId": str(result.run_id),
            "stages": {
                "planning": {
                    "status": "paused",
                    "dayBrief": result.day_brief.model_dump(mode="json"),
                }
            },
            "pausedAt": "dayBrief",
        }
    return {
        "runId": str(result.run_id),
        "selectedCandidate": result.selected_candidate,
        "candidateCount": result.candidate_count,
        "plan": result.plan.model_dump(mode="json"),
        "stages": {"planning": {"status": "completed"}},
    }


async def _run_validated(operation: Callable[[], Any]) -> Any:
    try:
        return await asyncio.to_thread(operation)
    except ValidationError as exc:
        errors = [
            {
                "loc": list(item.get("loc", ())),
                "type": str(item.get("type", "validation_error")),
                "msg": str(item.get("msg", "编辑内容不符合契约")),
            }
            for item in exc.errors(
                include_context=False,
                include_url=False,
                include_input=False,
            )
        ]
        raise HTTPException(
            status_code=422,
            detail={"message": "编辑内容不符合契约", "errors": errors},
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GatewayError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
