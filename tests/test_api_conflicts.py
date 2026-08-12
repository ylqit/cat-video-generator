from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from cat_video_generator.infrastructure.db.repositories import WorkflowConflictError
from cat_video_generator.interfaces.api import create_app
from cat_video_generator.interfaces.jobs import JobRegistry


def test_accept_suggestions_returns_409_when_generation_history_blocks_replacement(
    tmp_path: Path,
) -> None:
    class Editing:
        def accept_suggestions(self, *args: object, **kwargs: object) -> tuple[object, ...]:
            del args, kwargs
            raise WorkflowConflictError("已有图片或视频生成历史，不能整批覆盖视频片段")

    container = SimpleNamespace(
        repository=object(),
        editing=Editing(),
        runtime_settings=SimpleNamespace(work_root=tmp_path, asset_root=tmp_path),
    )
    app = create_app(
        container,  # type: ignore[arg-type]
        job_registry=JobRegistry(inline=True),
    )

    response = TestClient(app).post(
        f"/api/v1/steps/{uuid.uuid4()}/accept-suggestions",
        json={
            "lookPlan": None,
            "shots": [
                {
                    "title": "不可覆盖",
                    "direction": "1. 固定中景，猫咪观察人物，稳定收尾。",
                    "suggestedDurationSeconds": 8,
                }
            ],
        },
    )

    assert response.status_code == 409
    assert "不能整批覆盖" in response.json()["detail"]
