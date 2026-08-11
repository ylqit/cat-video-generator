"""本地前端使用的FastAPI接口：只读查询与完整生产控制。"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..application.queries import QueryService
from ..domain.contracts import ContractVersionError
from .api_studio import create_studio_router
from .api_write import create_write_router
from .jobs import JobRegistry

if TYPE_CHECKING:
    from ..bootstrap import RuntimeContainer


class _SPAStaticFiles(StaticFiles):
    """托管Vue产物，并只为真正的前端History路由回退到index.html。"""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            normalized_path = path.replace("\\", "/").lstrip("/")
            # 缺失API和带扩展名的静态文件必须保留404；只有浏览器页面路由
            # 才交给Vue Router，避免把资源部署错误隐藏成200 HTML。
            if (
                exc.status_code != 404
                or scope["method"] not in {"GET", "HEAD"}
                or normalized_path.startswith("api/")
                or Path(normalized_path).suffix
            ):
                raise
            return await super().get_response("index.html", scope)


def create_app(
    query_service: QueryService,
    *,
    allowed_media_roots: tuple[Path, ...],
    runtime_report: dict[str, object] | None = None,
) -> FastAPI:
    """创建只读API；业务状态由共享QueryService提供。"""

    app = FastAPI(
        title="Cat Video Generator",
        version="1.0.0",
        docs_url="/docs",
        redoc_url=None,
    )
    roots = tuple(root.expanduser().resolve() for root in allowed_media_roots)

    @app.exception_handler(ContractVersionError)
    async def incompatible_contract(_request: Request, exc: ContractVersionError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/api/v1/health")
    def health() -> dict:
        # 运行超时属于只读运维事实。与数据库健康状态合并返回，前端无需读取
        # .env，也不会接触API Key、密码或连接串。
        return {**query_service.health(), **(runtime_report or {})}

    @app.get("/api/v1/runs")
    def list_runs(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> list[dict]:
        return query_service.list_runs(limit=limit, offset=offset)

    @app.get("/api/v1/runs/{run_id}")
    def run_detail(run_id: uuid.UUID) -> dict:
        return _not_found(lambda: query_service.run_graph(run_id)["run"])

    @app.get("/api/v1/runs/{run_id}/graph")
    def run_graph(run_id: uuid.UUID) -> dict:
        return _not_found(lambda: query_service.run_graph(run_id))

    @app.get("/api/v1/episodes/{episode_id}")
    def episode_detail(episode_id: uuid.UUID) -> dict:
        return _not_found(lambda: query_service.episode(episode_id))

    @app.get("/api/v1/episodes/{episode_id}/video-sequences")
    def video_sequences(episode_id: uuid.UUID) -> list[dict]:
        return _not_found(lambda: query_service.video_sequences(episode_id))

    @app.get("/api/v1/episodes/{episode_id}/prompt-preview")
    def episode_prompt_preview(episode_id: uuid.UUID) -> dict:
        return _not_found(lambda: query_service.prompt_preview(episode_id))

    @app.get("/api/v1/steps/{step_id}")
    def step_detail(step_id: uuid.UUID) -> dict:
        return _not_found(lambda: query_service.step(step_id))

    @app.get("/api/v1/steps/{step_id}/trace")
    def step_trace(step_id: uuid.UUID) -> dict:
        return _not_found(lambda: query_service.step_trace(step_id))

    @app.get("/api/v1/prompts/{prompt_id}")
    def prompt_detail(prompt_id: uuid.UUID) -> dict:
        return _not_found(lambda: query_service.prompt(prompt_id))

    @app.get("/api/v1/assets/{asset_id}")
    def asset_detail(asset_id: uuid.UUID) -> dict:
        asset = _not_found(lambda: query_service.asset(asset_id))
        return {
            "id": str(asset.id),
            "runId": None if asset.run_id is None else str(asset.run_id),
            "episodeId": (None if asset.episode_id is None else str(asset.episode_id)),
            "role": asset.role,
            "scope": asset.scope,
            "status": asset.status,
            "localPath": str(asset.path),
            "sha256": asset.sha256,
            "metadata": asset.metadata,
        }

    @app.get("/api/v1/assets/{asset_id}/content")
    def asset_content(asset_id: uuid.UUID) -> FileResponse:
        asset = _not_found(lambda: query_service.asset(asset_id))
        resolved = asset.path.expanduser().resolve()
        if not any(resolved.is_relative_to(root) for root in roots):
            raise HTTPException(
                status_code=403,
                detail="资产路径不属于允许的媒体目录。",
            )
        if not resolved.is_file():
            raise HTTPException(status_code=404, detail="资产文件不存在。")
        return FileResponse(resolved)

    return app


def create_full_app(
    container: RuntimeContainer,
    *,
    allowed_media_roots: tuple[Path, ...],
    job_registry: JobRegistry,
    static_dir: Path | None = None,
) -> FastAPI:
    """在只读接口上叠加写端点、可选Bearer令牌与前端静态托管。

    付费许可由每个请求体逐次透传；容器在启动时只校验Ark配置与ffprobe，
    不要求一次性付费授权。
    """

    app = create_app(
        container.queries,
        allowed_media_roots=allowed_media_roots,
        runtime_report=container.runtime_settings.preflight_report(),
    )
    runtime = container.runtime_settings
    app.include_router(
        create_write_router(
            planning=container.planning,
            production=container.production,
            assets=container.assets,
            delivery=container.delivery,
            queries=container.queries,
            retry=container.retry,
            regeneration=container.regeneration,
            video_editing=container.video_editing,
            job_registry=job_registry,
            upload_dir=runtime.work_root / "uploads",
            delivery_root=runtime.delivery_root,
        )
    )
    app.include_router(
        create_studio_router(
            planning=container.planning,
            production=container.production,
            queries=container.queries,
            studio_editing=container.studio_editing,
            job_registry=job_registry,
        )
    )
    token = os.environ.get("CAT_VIDEO_API_TOKEN")
    if token:
        _install_token_middleware(app, token)
    if static_dir is not None and (static_dir / "index.html").is_file():
        app.mount(
            "/",
            _SPAStaticFiles(directory=static_dir, html=True),
            name="web",
        )
    return app


def _install_token_middleware(app: FastAPI, token: str) -> None:
    """可选的本机Bearer令牌；仅在显式配置环境变量时启用。"""

    @app.middleware("http")
    async def verify_token(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            expected = f"Bearer {token}"
            if request.headers.get("authorization") != expected:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "缺少或错误的访问令牌"},
                )
        return await call_next(request)


def _not_found(operation):
    try:
        return operation()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
