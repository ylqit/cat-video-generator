"""FastAPI application for the Creator-only product."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..bootstrap import RuntimeContainer
from ..infrastructure.db.creator_repository import RecordNotFoundError, WorkflowConflictError
from .creator_api import install_creator_routes


class _SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: dict[str, Any]):  # type: ignore[override]
        response = await super().get_response(path, scope)
        if response.status_code == 404 and "." not in Path(path).name:
            return await super().get_response("index.html", scope)
        return response


def create_app(
    container: RuntimeContainer,
    *,
    static_dir: Path | None = None,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    app = FastAPI(
        title="One Child One Cat Creator",
        version="1.0",
        lifespan=lifespan,
    )

    @app.exception_handler(RecordNotFoundError)
    async def not_found(_request: Request, error: RecordNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @app.exception_handler(WorkflowConflictError)
    async def conflict(_request: Request, error: WorkflowConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.exception_handler(ValueError)
    async def invalid(_request: Request, error: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @app.get("/api/v2/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "applicationVersion": "creator-only-1",
            "alembicRevision": container.alembic_revision,
            "apiFeatures": [
                "creator_projects",
                "immutable_generation_snapshots",
                "provider_task_cancellation",
                "creator_timelines",
            ],
        }

    @app.get("/api/v2/runtime-settings")
    def runtime_settings() -> dict[str, Any]:
        return {
            "planningModel": container.runtime_settings.ark_planning_model,
            "imageModel": container.runtime_settings.ark_image_model,
            "videoModel": container.runtime_settings.ark_video_model,
            "videoResolution": container.runtime_settings.ark_video_resolution,
            "preflight": container.runtime_settings.preflight_report(),
        }

    install_creator_routes(
        app,
        container.creator,
        container.creator_repository,
        container.task_queue,
    )

    if static_dir is not None:
        app.mount(
            "/",
            _SPAStaticFiles(directory=str(static_dir), html=True, check_dir=True),
            name="web",
        )
    return app
