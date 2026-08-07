"""主题创作台流水线的HTTP端点与自动续跑编排。

阶段串联（``chain_after_planning``）与关键帧批准后的视频续跑钩子
（``maybe_continue_video``）必须放在接口层：Application Service不允许
依赖任务登记器，而自动续跑本身是"登记一个新的付费任务"。
所有自动续跑的唯一付费依据是Run级持久化的
``PipelineSettings.allow_paid_generation``（提交主题时人工一次收齐），
video阶段为manual或授权为假时绝不自动扣费。
"""

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
    """按流水线开关在规划之后串联关键帧与视频阶段。

    所有调用都在同一个后台任务、同一把付费串行门内顺序执行；
    任一阶段为manual或等待人工审核时记录pausedAt并停住，
    之后由POST /runs/{id}/continue或审核钩子继续。

    局部重规划必须把slot传入，只推进被修改的Episode。否则重新评估全天
    会让已经批准的其他时段因Prompt哈希变化再次产生图片费用。
    """

    stages = payload.setdefault("stages", {})
    if settings.script is StageMode.MANUAL:
        payload["pausedAt"] = "script"
        return payload
    if settings.storyboard is StageMode.MANUAL:
        payload["pausedAt"] = "storyboard"
        return payload
    storyboard = production.prepare_storyboards_only(
        run_id,
        slot=slot,
        allow_paid_generation=True,
    )
    stages["storyboard"] = storyboard
    if not all(item["storyboardReady"] for item in storyboard["episodes"]):
        # 故事板等待人工语义审核；整组批准后由maybe_continue_video钩子续跑。
        payload["pausedAt"] = "storyboard"
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


def maybe_continue_video(
    *,
    queries: QueryService,
    production: Any,
    job_registry: JobRegistry,
    asset_id: uuid.UUID,
) -> None:
    """人工批准故事板或镜头片段后按流水线开关自动续跑该集视频。

    三个条件缺一不可：video阶段为auto、Run级付费授权为真、触发资产属于
    该集。审核人的"批准"点击即显式人工确认；逐镜头模式下批准某镜头片段
    会续跑下一镜头（run_day→execute→_generate_per_shot幂等复用已批准镜头）。
    """

    try:
        asset = queries.asset(asset_id)
    except LookupError:
        return
    if asset.role not in {"storyboard_panel", "video_shot"} or asset.episode_id is None:
        return
    episode = queries.episode(asset.episode_id)
    run_id = uuid.UUID(str(episode["runId"]))
    settings = queries.pipeline_settings(run_id)
    if settings.video is not StageMode.AUTO or not settings.allow_paid_generation:
        return
    slot = Slot(str(episode["slot"]))

    def task() -> dict[str, Any]:
        # 故事板采用整组原子审核：本钩子被调用时，同组面板已经一次性提交为批准状态。
        # 这里继续整个全天流水线，让其他已自动批准的时段也能顺序进入视频生成；
        # ProductionService 会幂等跳过已完成Episode，因此不会重复创建收费任务。
        return production.run_day(run_id, slot=None, allow_paid_generation=True)

    # 同一Run只允许一个全天续跑任务；数据库幂等仍负责最终防止重复收费。
    with suppress(JobConflictError):
        job_registry.submit(
            kind="run_day",
            dedup_key=f"run:{run_id}:all",
            fn=task,
            context={
                "runId": run_id,
                "episodeId": asset.episode_id,
                "slot": slot.value,
                "operationKey": (
                    "video:single_pass"
                    if asset.role == "storyboard_panel"
                    else "video:shot:continue"
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
    """创作台流水线端点：阶段续跑与人工编辑。"""

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
                # resume_planning本就接受draft/failed：瞬时失败可一键恢复，
                # 确定性校验失败会再次失败并给出同样错误，无副作用。
                result = planning.resume_planning(
                    run_id,
                    allow_paid_generation=True,
                )
                payload: dict[str, Any] = {
                    "runId": str(run_id),
                    "stages": {"planning": _jsonable(result)},
                }
            elif status in {"planned", "generating", "reviewing"}:
                payload = {"runId": str(run_id), "stages": {}}
            else:
                raise ValueError(f"Run状态{status}不支持流水线续跑")
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


def build_plan_payload(
    result: PlanningResult | DayBriefPause,
) -> dict[str, Any]:
    """把规划结果规整为任务payload；DayBriefPause表示dayBrief阶段停顿。"""

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
    """契约校验失败时返回结构化errors；不能复用_run_sync（它先吞ValueError）。"""

    try:
        return await asyncio.to_thread(operation)
    except ValidationError as exc:
        # Pydantic 的原始 ``errors()`` 可能把 EpisodeScript 等对象放入 input，
        # FastAPI 会在序列化错误响应时再次异常，将本应为 422 的契约错误变成 500。
        # 编辑接口只需向 Web 暴露定位、类型和消息，不回传整个业务对象。
        errors = [
            {
                "loc": list(item.get("loc", ())),
                "type": str(item.get("type", "validation_error")),
                "msg": str(item.get("msg", "编辑内容不符合契约")),
            }
            for item in exc.errors(include_context=False, include_url=False, include_input=False)
        ]
        raise HTTPException(
            status_code=422,
            detail={
                "message": "编辑内容不符合契约",
                "errors": errors,
            },
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
