"""Windows PowerShell主入口。

CLI只解析参数、调用Application Service并输出JSON，不直接访问SQLAlchemy
模型、不调用Ark SDK，也不修改业务状态。
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import typer

from ..application.planning import PlanningReviewRequired
from ..bootstrap import (
    build_diagnostic_container,
    build_local_container,
    build_query_container,
    build_runtime_container,
)
from ..config import RuntimeSettings, load_local_env
from ..domain.contracts import (
    EpisodeSources,
    SceneRoute,
    Slot,
    StoryInputMode,
    StoryProjectInput,
)
from ..domain.pipeline import PipelineSettings, PlanningMode
from .api import create_app, create_full_app
from .api_helpers import _jsonable
from .jobs import JobRegistry

app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)
canon_app = typer.Typer(no_args_is_help=True)
reference_app = typer.Typer(no_args_is_help=True)


@app.command()
def doctor() -> None:
    """检查PostgreSQL、Ark配置和本地媒体工具。"""

    load_local_env()
    container = build_diagnostic_container()
    try:
        _echo(
            {
                "database": container.queries.health(),
                "runtime": RuntimeSettings.from_env().preflight_report(),
            }
        )
    finally:
        container.close()


@canon_app.command("import")
def canon_import(
    role: str = typer.Option(..., "--role"),
    semantic_key: str = typer.Option(..., "--semantic-key"),
    view: str | None = typer.Option(None, "--view"),
    file: Path = typer.Option(..., "--file"),  # noqa: B008
) -> None:
    """导入批准的人物、猫咪或画风Canon。"""

    container = build_local_container()
    try:
        _echo(
            container.assets.import_canon(
                role=role,
                path=file,
                semantic_key=semantic_key,
                view=view,
            )
        )
    finally:
        container.close()


@canon_app.command("derive-crop")
def canon_derive_crop(
    source_asset_id: uuid.UUID = typer.Option(..., "--source-asset-id"),
    role: str = typer.Option(..., "--role"),
    box: str | None = typer.Option(
        None,
        "--box",
        help="left,top,right,bottom；person/cat留空时自动裁左侧正面视图",
    ),
    subject_free: bool = typer.Option(False, "--subject-free"),
    semantic_key: str = typer.Option(..., "--semantic-key"),
    view: str | None = typer.Option(None, "--view"),
) -> None:
    """从批准Canon确定性裁出单视图，不调用Ark。"""

    parsed_box: tuple[int, int, int, int] | None = None
    if box is not None:
        try:
            values = tuple(int(item.strip()) for item in box.split(","))
        except ValueError as exc:
            raise typer.BadParameter("--box必须包含四个整数") from exc
        if len(values) != 4:
            raise typer.BadParameter("--box必须是left,top,right,bottom")
        parsed_box = values
    container = build_local_container()
    try:
        _echo(
            container.assets.derive_canon_crop(
                source_asset_id=source_asset_id,
                role=role,
                box=parsed_box,
                subject_free=subject_free,
                semantic_key=semantic_key,
                view=view,
            )
        )
    finally:
        container.close()


@reference_app.command("import")
def reference_import(
    episode_id: uuid.UUID = typer.Option(..., "--episode-id"),
    role: str = typer.Option(..., "--role"),
    semantic_key: str = typer.Option(..., "--semantic-key"),
    file: Path = typer.Option(..., "--file"),  # noqa: B008
) -> None:
    """导入Episode专用的场景或关键道具图片。"""

    container = build_local_container()
    try:
        _echo(
            container.assets.import_episode_reference(
                episode_id=episode_id,
                role=role,
                path=file,
                semantic_key=semantic_key,
            )
        )
    finally:
        container.close()


@app.command("plan-day")
def plan_day(
    target_date: str = typer.Option(..., "--target-date"),
    theme: str = typer.Option(..., "--theme"),
    input_mode: StoryInputMode = typer.Option(
        StoryInputMode.THEME_EXPAND,
        "--input-mode",
    ),
    scene_route: SceneRoute = typer.Option(
        SceneRoute.ADAPTIVE,
        "--scene-route",
    ),
    morning_script: str | None = typer.Option(None, "--morning-script"),
    noon_script: str | None = typer.Option(None, "--noon-script"),
    evening_script: str | None = typer.Option(None, "--evening-script"),
    allow_paid_generation: bool = typer.Option(
        False,
        "--allow-paid-generation",
    ),
    planning_mode: PlanningMode = typer.Option(
        PlanningMode.GUIDED_SEQUENTIAL,
        "--planning-mode",
    ),
) -> None:
    """创建生活故事项目；已有剧本模式不会调用总导演。"""

    try:
        parsed_date = date.fromisoformat(target_date)
    except ValueError as exc:
        raise typer.BadParameter("--target-date 必须使用 YYYY-MM-DD") from exc
    sources = EpisodeSources(
        morning=morning_script,
        noon=noon_script,
        evening=evening_script,
    )
    try:
        project_input = StoryProjectInput(
            theme=theme,
            input_mode=input_mode,
            scene_route=scene_route,
            episode_sources=sources,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if (
        input_mode is StoryInputMode.EPISODE_SCRIPTS
        and planning_mode is PlanningMode.AUTO_DAY
        and len(sources.populated_slots) != len(Slot)
    ):
        raise typer.BadParameter(
            "episode_scripts配合auto_day时必须提供上午、中午、傍晚三段剧本"
        )
    director_required = input_mode is StoryInputMode.THEME_EXPAND or (
        input_mode is StoryInputMode.EPISODE_SCRIPTS
        and planning_mode is PlanningMode.AUTO_DAY
    )
    if director_required and not allow_paid_generation:
        raise typer.BadParameter(
            "主题扩写或auto_day镜头化会调用付费导演，必须提供--allow-paid-generation"
        )
    container = build_runtime_container(
        allow_paid_generation=allow_paid_generation,
        require_paid_permission=director_required,
    )
    try:
        try:
            result = container.planning.create_project(
                target_date=parsed_date,
                project_input=project_input,
                allow_paid_generation=allow_paid_generation,
                pipeline_settings=PipelineSettings(
                    planningMode=planning_mode,
                    allowPaidGeneration=allow_paid_generation,
                ),
            )
        except PlanningReviewRequired as exc:
            _emit_planning_review(exc)
        _echo(_jsonable(result))
    finally:
        container.close()


@app.command("replan-episode")
def replan_episode(
    run_id: uuid.UUID = typer.Argument(...),
    slot: Slot = typer.Option(..., "--slot"),
    reason: str = typer.Option(..., "--reason"),
    allow_paid_generation: bool = typer.Option(
        False,
        "--allow-paid-generation",
    ),
) -> None:
    """只重新镜头化一个时段，保留项目输入和其他时段。"""

    container = build_runtime_container(allow_paid_generation=allow_paid_generation)
    try:
        try:
            result = container.planning.replan_episode(
                run_id,
                slot=slot,
                reason=reason,
                allow_paid_generation=allow_paid_generation,
            )
        except PlanningReviewRequired as exc:
            _emit_planning_review(exc)
        _echo(_jsonable(result))
    finally:
        container.close()


@app.command("resume-planning")
def resume_planning(
    run_id: uuid.UUID = typer.Argument(...),
    allow_paid_generation: bool = typer.Option(
        False,
        "--allow-paid-generation",
    ),
) -> None:
    """复用已确认项目大纲或用户原始剧本，继续未完成的时段导演。"""

    container = build_runtime_container(allow_paid_generation=allow_paid_generation)
    try:
        try:
            result = container.planning.resume_planning(
                run_id,
                allow_paid_generation=allow_paid_generation,
            )
        except PlanningReviewRequired as exc:
            _emit_planning_review(exc)
        _echo(_jsonable(result))
    finally:
        container.close()


@app.command("run-day")
def run_day(
    run_id: uuid.UUID = typer.Argument(...),
    slot: Slot | None = typer.Option(None, "--slot"),
    allow_paid_generation: bool = typer.Option(
        False,
        "--allow-paid-generation",
    ),
) -> None:
    """生成全天或指定时段。"""

    container = build_runtime_container(allow_paid_generation=allow_paid_generation)
    try:
        _echo(
            container.production.run_day(
                run_id,
                slot=slot,
                allow_paid_generation=allow_paid_generation,
            )
        )
    finally:
        container.close()


@app.command()
def resume(
    run_id: uuid.UUID | None = typer.Argument(None),
) -> None:
    """仅恢复已有Ark任务，不创建新的收费任务。"""

    container = build_runtime_container(
        allow_paid_generation=False,
        require_paid_permission=False,
    )
    try:
        _echo(container.production.resume(run_id))
    finally:
        container.close()


@app.command("retry-step")
def retry_step(
    step_id: uuid.UUID = typer.Argument(...),
    reason: str = typer.Option(..., "--reason"),
    allow_paid_generation: bool = typer.Option(
        False,
        "--allow-paid-generation",
    ),
    restart_from_beginning: bool = typer.Option(
        False,
        "--restart-from-beginning",
        help="延展成片的错误已存在于前段时，从批准的开场锚点重建全部区段。",
    ),
) -> None:
    """显式重试一个终态步骤；run-day永远不会替代本命令自动重试。"""

    container = build_runtime_container(
        allow_paid_generation=allow_paid_generation,
        require_paid_permission=allow_paid_generation,
    )
    try:
        _echo(
            container.retry.retry_step(
                step_id,
                reason=reason,
                allow_paid_generation=allow_paid_generation,
                restart_from_beginning=restart_from_beginning,
            )
        )
    finally:
        container.close()


@app.command()
def status(run_id: uuid.UUID | None = typer.Argument(None)) -> None:
    """查询工作流图或最近Run。"""

    container = build_query_container()
    try:
        _echo(
            container.queries.list_runs() if run_id is None else container.queries.run_graph(run_id)
        )
    finally:
        container.close()


@app.command()
def review(
    asset_id: uuid.UUID = typer.Argument(...),
    approve: bool = typer.Option(False, "--approve"),
    reject: bool = typer.Option(False, "--reject"),
    reason: str = typer.Option(..., "--reason"),
) -> None:
    """人工批准或拒绝图片/视频资产。"""

    if approve == reject:
        raise typer.BadParameter("必须且只能选择--approve或--reject")
    container = build_local_container()
    try:
        _echo(
            container.assets.review_asset(
                asset_id,
                approve=approve,
                reason=reason,
            )
        )
    finally:
        container.close()


@app.command("show-prompt")
def show_prompt(
    prompt_id: uuid.UUID = typer.Argument(...),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    """显示数据库中实际使用的完整Prompt。"""

    container = build_query_container()
    try:
        detail = container.queries.prompt(prompt_id)
        if output is not None:
            destination = output.expanduser().resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(str(detail["text"]), encoding="utf-8")
            _echo(
                {
                    "promptId": str(prompt_id),
                    "output": str(destination),
                    "sha256": detail["sha256"],
                    "charCount": detail["charCount"],
                    "promptAliases": detail.get("promptAliases", {}),
                    "inputPlan": detail.get("inputPlan"),
                }
            )
        else:
            _echo(detail)
    finally:
        container.close()


@app.command()
def deliver(run_id: uuid.UUID = typer.Argument(...)) -> None:
    """构建三条视频的本地原子交付包。"""

    container = build_local_container()
    try:
        _echo(container.delivery.deliver(run_id))
    finally:
        container.close()


@app.command("api")
def serve_api(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port", min=1, max=65535),
    read_only: bool = typer.Option(False, "--read-only"),
    static_dir: Path | None = typer.Option(None, "--static-dir"),
) -> None:
    """启动HTTP接口；本机默认监听回环地址，容器可显式监听所有网卡。"""

    from concurrent.futures import ThreadPoolExecutor

    import uvicorn

    if static_dir is not None and not (static_dir / "index.html").is_file():
        raise typer.BadParameter(
            "静态目录必须包含index.html",
            param_hint="--static-dir",
        )
    load_local_env()
    runtime = RuntimeSettings.from_env()
    media_roots = (runtime.asset_root, runtime.delivery_root)
    if read_only:
        container = build_query_container()
        api = create_app(
            container.queries,
            allowed_media_roots=media_roots,
            runtime_report=runtime.preflight_report(),
        )
    else:
        container = build_runtime_container(
            allow_paid_generation=False,
            require_paid_permission=False,
            pool_size=5,
            max_overflow=5,
        )
        api = create_full_app(
            container,
            allowed_media_roots=media_roots,
            job_registry=JobRegistry(executor=ThreadPoolExecutor(max_workers=2)),
            static_dir=static_dir,
        )
    try:
        uvicorn.run(api, host=host, port=port)
    finally:
        container.close()


def _echo(value: Any) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _emit_planning_review(exc: PlanningReviewRequired) -> None:
    """把预期的人工规划状态输出为稳定JSON，而不是暴露Python调用栈。"""

    _echo(
        {
            "runId": str(exc.run_id),
            "status": "planning_review",
            "slot": exc.slot.value,
            "contradictions": list(exc.errors),
            "nextAction": (
                f"cvg replan-episode {exc.run_id} --slot {exc.slot.value} "
                "--reason <人工修改理由> --allow-paid-generation"
            ),
        }
    )
    raise typer.Exit(code=2)


app.add_typer(canon_app, name="canon")
app.add_typer(reference_app, name="reference")


def main() -> None:
    load_local_env()
    app()


if __name__ == "__main__":
    main()
