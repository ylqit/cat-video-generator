"""分阶段流水线：断点规划、人工编辑、审核钩子与HTTP端点。"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_planning_application import (
    Director,
    EmptySeeds,
    PlanningRepository,
)

from cat_video_generator.application.planning import (
    DayBriefPause,
    PlanningResult,
    PlanningReviewRequired,
    PlanningService,
)
from cat_video_generator.application.ports import StoredAsset, StoredRun
from cat_video_generator.application.studio_editing import StudioEditingService
from cat_video_generator.domain.contracts import DailyProductionPlan, DayBrief, Slot
from cat_video_generator.domain.pipeline import PipelineSettings, StageMode
from cat_video_generator.domain.visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
)
from cat_video_generator.domain.workflow import RunStatus
from cat_video_generator.interfaces.api_studio import (
    create_studio_router,
    maybe_continue_video,
)
from cat_video_generator.interfaces.api_write import create_write_router
from cat_video_generator.interfaces.jobs import JobRegistry


def _planning_service(repository, director) -> PlanningService:
    return PlanningService(
        repository=repository,
        director=director,
        provider_name="volcengine-ark-standard",
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
        event_seed_catalog=EmptySeeds(),
    )


def test_job_error_keeps_planning_review_run_identity() -> None:
    registry = JobRegistry(inline=True)
    record = registry.submit(
        kind="plan_day",
        dedup_key="plan:review",
        fn=lambda: (_ for _ in ()).throw(
            PlanningReviewRequired(
                run_id=RUN_ID,
                slot=Slot.NOON,
                errors=("视频Prompt超过预算",),
            )
        ),
    )

    assert record.status == "failed"
    assert record.error == {
        "code": "internal",
        "message": (
            f"Run {RUN_ID} 的 noon 时段自动修复后仍不自洽：视频Prompt超过预算"
        ),
        "runId": str(RUN_ID),
        "slot": "noon",
        "details": ["视频Prompt超过预算"],
    }


def test_plan_day_pauses_after_day_brief_then_resume(daily_plan) -> None:
    payloads = [
        daily_plan.day_brief.model_dump(mode="json"),
        *(item.script.model_dump(mode="json") for item in daily_plan.episodes),
    ]
    director = Director(payloads)
    repository = PlanningRepository()
    service = _planning_service(repository, director)

    paused = service.plan_day(
        target_date=daily_plan.content_date,
        planning_context="设计普通生活中的小发现",
        candidate_count=1,
        allow_paid_generation=True,
        stop_after_day_brief=True,
        pipeline_settings=PipelineSettings(allow_paid_generation=True),
    )

    assert isinstance(paused, DayBriefPause)
    assert repository.plan is None
    assert repository.run_status == RunStatus.DRAFT.value
    assert len(director.prompts) == 1
    assert repository.context["episodeDrafts"] == {}
    assert repository.pipeline_settings.allow_paid_generation is True

    result = service.resume_planning(
        repository.run_id,
        allow_paid_generation=True,
    )
    assert isinstance(result, PlanningResult)
    assert len(director.prompts) == 4
    assert repository.plan is not None


def test_pipeline_settings_legacy_default() -> None:
    settings = PipelineSettings.legacy_default()
    assert settings.allow_paid_generation is False
    assert all(
        settings.stage(name) is StageMode.AUTO
        for name in ("dayBrief", "script", "keyframes", "video")
    )
    with pytest.raises(ValueError, match="未知流水线阶段"):
        settings.stage("thumbnail")


class StudioRepository:
    """记录编辑落库调用的内存StudioStore。"""

    def __init__(self, plan: DailyProductionPlan, *, finalized: bool = True) -> None:
        self.plan = plan if finalized else None
        self.day_brief = plan.day_brief
        self.run_id = uuid.uuid4()
        self.status = "planned"
        self.run_status = (
            RunStatus.PLANNED.value if finalized else RunStatus.DRAFT.value
        )
        self.replaced: list = []
        self.brief_updates: list[DayBrief] = []
        self.settings: PipelineSettings | None = None

    def episode_detail(self, episode_id):
        episode = (self.plan or self._unfinalized_plan()).episodes[0]
        return {
            "id": str(episode_id),
            "runId": str(self.run_id),
            "slot": episode.slot.value,
            "status": self.status,
            "script": episode.script.model_dump(mode="json"),
            "promptOverrides": {"first_frame": "旧编辑"},
        }

    def _unfinalized_plan(self):
        raise AssertionError("未定稿Run没有Episode行，不应读取episode_detail")

    def get_run(self, run_id):
        return StoredRun(
            id=self.run_id,
            content_date=date(2026, 8, 2),
            status=self.run_status,
            plan=self.plan,
        )

    def get_planning_context(self, run_id):
        return {"dayBrief": self.day_brief.model_dump(mode="json")}

    def replace_episode_plan(self, *, run_id, episode):
        self.replaced.append(episode)

    def update_day_brief(self, *, run_id, day_brief):
        self.brief_updates.append(day_brief)

    def save_pipeline_settings(self, *, run_id, settings):
        self.settings = settings

    def get_pipeline_settings(self, run_id):
        return self.settings or PipelineSettings.legacy_default()


def _editing_service(repository) -> StudioEditingService:
    return StudioEditingService(
        repository=repository,
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
    )


def test_update_episode_script_replaces_after_validation(daily_plan) -> None:
    repository = StudioRepository(daily_plan)
    service = _editing_service(repository)
    payload = daily_plan.episodes[0].script.model_dump(mode="json")
    payload["title"] = "早晨纸风车人工修订版"

    result = service.update_episode_script(uuid.uuid4(), payload)

    assert result["saved"] is True
    assert result["promptOverridesKept"] is True
    assert repository.replaced[0].script.title == "早晨纸风车人工修订版"


def test_update_episode_script_rejects_invalid_and_conflicting(daily_plan) -> None:
    repository = StudioRepository(daily_plan)
    service = _editing_service(repository)
    with pytest.raises(ValidationError):
        service.update_episode_script(uuid.uuid4(), {"title": "x"})
    assert repository.replaced == []

    conflicting = daily_plan.episodes[0].script.model_dump(mode="json")
    conflicting["title"] = daily_plan.episodes[1].script.title
    with pytest.raises(ValueError):
        service.update_episode_script(uuid.uuid4(), conflicting)
    assert repository.replaced == []

    repository.status = "preparing_visuals"
    valid = daily_plan.episodes[0].script.model_dump(mode="json")
    with pytest.raises(ValueError, match="已进入媒体生产"):
        service.update_episode_script(uuid.uuid4(), valid)


def test_update_day_brief_rules(daily_plan) -> None:
    finalized = StudioRepository(daily_plan)
    service = _editing_service(finalized)
    payload = daily_plan.day_brief.model_dump(mode="json")
    with pytest.raises(ValueError, match="已定稿"):
        service.update_day_brief(finalized.run_id, payload)

    draft = StudioRepository(daily_plan, finalized=False)
    service = _editing_service(draft)
    payload["theme"] = "人工修订的日主题"
    result = service.update_day_brief(draft.run_id, payload)
    assert result["episodeDraftsCleared"] is True
    assert draft.brief_updates[0].theme == "人工修订的日主题"


def test_update_pipeline_settings_roundtrip(daily_plan) -> None:
    repository = StudioRepository(daily_plan)
    service = _editing_service(repository)
    result = service.update_pipeline_settings(
        repository.run_id,
        {"video": "manual", "allowPaidGeneration": True},
    )
    assert repository.settings is not None
    assert repository.settings.video is StageMode.MANUAL
    assert result["pipelineSettings"]["video"] == "manual"
    assert result["pipelineSettings"]["keyframes"] == "auto"


RUN_ID = uuid.uuid4()
EPISODE_ID = uuid.uuid4()


def _frame(role: str, status: str) -> StoredAsset:
    return StoredAsset(
        id=uuid.uuid4(),
        run_id=RUN_ID,
        episode_id=EPISODE_ID,
        step_id=None,
        role=role,
        media_type="image",
        scope="episode",
        status=status,
        path=Path("frame.png"),
        sha256="a" * 64,
        metadata={},
    )


def _queries_stub(*, role: str, settings: PipelineSettings, frames=()):
    approved = _frame(role, "candidate")
    return SimpleNamespace(
        asset=lambda asset_id: approved,
        episode=lambda episode_id: {"runId": str(RUN_ID), "slot": "morning"},
        pipeline_settings=lambda run_id: settings,
        episode_assets=lambda episode_id: tuple(frames),
    )


def _hook_calls(settings: PipelineSettings, *, role="last_frame", frames=()):
    calls: list[dict] = []
    production = SimpleNamespace(
        run_day=lambda run_id, *, slot, allow_paid_generation: calls.append(
            {"slot": slot.value, "paid": allow_paid_generation}
        )
        or {"runId": str(run_id)}
    )
    registry = JobRegistry(inline=True)
    maybe_continue_video(
        queries=_queries_stub(role=role, settings=settings, frames=frames),
        production=production,
        job_registry=registry,
        asset_id=uuid.uuid4(),
    )
    return calls, registry


def test_review_hook_submits_run_day_when_frames_ready() -> None:
    settings = PipelineSettings(allow_paid_generation=True)
    frames = (_frame("first_frame", "approved"), _frame("last_frame", "approved"))
    calls, registry = _hook_calls(settings, frames=frames)
    assert calls == [{"slot": "morning", "paid": True}]
    records = registry.list()
    assert [record.kind for record in records] == ["run_day"]
    assert records[0].status == "succeeded"


def test_review_hook_stays_silent_without_full_conditions() -> None:
    frames = (_frame("first_frame", "approved"), _frame("last_frame", "approved"))
    # video=manual：帧就绪也必须人工确认。
    calls, _ = _hook_calls(
        PipelineSettings(allow_paid_generation=True, video=StageMode.MANUAL),
        frames=frames,
    )
    assert calls == []
    # 未持久化付费授权：绝不自动扣费（含历史Run的legacy_default）。
    calls, _ = _hook_calls(PipelineSettings.legacy_default(), frames=frames)
    assert calls == []
    # 只有首帧就绪。
    calls, _ = _hook_calls(
        PipelineSettings(allow_paid_generation=True),
        frames=(_frame("first_frame", "approved"), _frame("last_frame", "candidate")),
    )
    assert calls == []
    # 批准的是视频资产，与关键帧钩子无关。
    calls, _ = _hook_calls(
        PipelineSettings(allow_paid_generation=True),
        role="video",
        frames=frames,
    )
    assert calls == []


class StudioProduction:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.keyframe_calls = 0
        self.video_calls: list[dict] = []

    def prepare_keyframes_only(self, run_id, *, allow_paid_generation):
        self.keyframe_calls += 1
        return {
            "runId": str(run_id),
            "episodes": [
                {"episodeId": str(uuid.uuid4()), "slot": slot, "keyframesReady": self.ready}
                for slot in ("morning", "noon", "evening")
            ],
        }

    def run_day(self, run_id, *, slot, allow_paid_generation):
        self.video_calls.append({"slot": None if slot is None else slot.value})
        return {"runId": str(run_id), "episodes": []}


def _studio_client(*, settings, production, studio_editing, status="planned", planning=None):
    queries = SimpleNamespace(
        pipeline_settings=lambda run_id: settings,
        run_graph=lambda run_id: {"run": {"status": status}},
    )
    app = FastAPI()
    app.include_router(
        create_studio_router(
            planning=planning or SimpleNamespace(),
            production=production,
            queries=queries,
            studio_editing=studio_editing,
            job_registry=JobRegistry(inline=True),
        )
    )
    return TestClient(app)


def test_continue_accepts_failed_status_and_resumes(daily_plan) -> None:
    repository = StudioRepository(daily_plan)
    production = StudioProduction()
    resume_calls: list[dict] = []
    planning = SimpleNamespace(
        resume_planning=lambda run_id, *, allow_paid_generation: (
            resume_calls.append({"paid": allow_paid_generation}) or {"ok": True}
        )
    )
    client = _studio_client(
        settings=PipelineSettings(
            allow_paid_generation=True,
            video=StageMode.MANUAL,
        ),
        production=production,
        studio_editing=_editing_service(repository),
        status="failed",
        planning=planning,
    )
    accepted = client.post(f"/api/v1/runs/{uuid.uuid4()}/continue")
    assert accepted.status_code == 202
    assert resume_calls == [{"paid": True}]
    assert production.keyframe_calls == 1


def _write_client(*, tmp_path, settings, production, planning, status):
    queries = SimpleNamespace(
        pipeline_settings=lambda run_id: settings,
        run_graph=lambda run_id: {"run": {"status": status}},
        episode=lambda episode_id: {"runId": str(RUN_ID), "slot": "morning"},
    )
    app = FastAPI()
    app.include_router(
        create_write_router(
            planning=planning,
            production=production,
            assets=SimpleNamespace(),
            delivery=SimpleNamespace(),
            queries=queries,
            retry=SimpleNamespace(),
            job_registry=JobRegistry(inline=True),
            default_candidate_count=1,
            upload_dir=tmp_path,
            delivery_root=tmp_path,
        )
    )
    return TestClient(app)


def test_replan_endpoint_chains_only_when_finalized(tmp_path) -> None:
    planning = SimpleNamespace(
        replan_episode=lambda run_id, *, slot, reason, allow_paid_generation: {
            "slot": slot.value
        }
    )
    finalized = StudioProduction()
    client = _write_client(
        tmp_path=tmp_path,
        settings=PipelineSettings(allow_paid_generation=True),
        production=finalized,
        planning=planning,
        status="planned",
    )
    accepted = client.post(
        f"/api/v1/runs/{uuid.uuid4()}/episodes/morning/replan",
        json={"reason": "共享元素声明与其他时段保持一致", "allowPaidGeneration": True},
    )
    assert accepted.status_code == 202
    assert finalized.video_calls == [{"slot": None}]

    unfinalized = StudioProduction()
    client_draft = _write_client(
        tmp_path=tmp_path,
        settings=PipelineSettings(allow_paid_generation=True),
        production=unfinalized,
        planning=planning,
        status="draft",
    )
    accepted = client_draft.post(
        f"/api/v1/runs/{uuid.uuid4()}/episodes/morning/replan",
        json={"reason": "共享元素声明与其他时段保持一致", "allowPaidGeneration": True},
    )
    assert accepted.status_code == 202
    assert unfinalized.video_calls == []
    assert unfinalized.keyframe_calls == 0


def test_continue_endpoint_chains_by_settings(daily_plan) -> None:
    repository = StudioRepository(daily_plan)
    production = StudioProduction()
    client = _studio_client(
        settings=PipelineSettings(
            allow_paid_generation=True,
            video=StageMode.MANUAL,
        ),
        production=production,
        studio_editing=_editing_service(repository),
    )
    accepted = client.post(f"/api/v1/runs/{uuid.uuid4()}/continue")
    assert accepted.status_code == 202
    assert production.keyframe_calls == 1
    assert production.video_calls == []

    production_auto = StudioProduction()
    client_auto = _studio_client(
        settings=PipelineSettings(allow_paid_generation=True),
        production=production_auto,
        studio_editing=_editing_service(repository),
    )
    accepted = client_auto.post(f"/api/v1/runs/{uuid.uuid4()}/continue")
    assert accepted.status_code == 202
    assert production_auto.video_calls == [{"slot": None}]


def test_script_edit_endpoint_returns_structured_errors(daily_plan) -> None:
    repository = StudioRepository(daily_plan)
    client = _studio_client(
        settings=PipelineSettings.legacy_default(),
        production=StudioProduction(),
        studio_editing=_editing_service(repository),
    )
    response = client.put(
        f"/api/v1/episodes/{uuid.uuid4()}/script",
        json={"title": "x"},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["message"] == "编辑内容不符合契约"
    assert detail["errors"]

    valid = daily_plan.episodes[0].script.model_dump(mode="json")
    valid["title"] = "端点编辑后的早晨标题"
    saved = client.put(f"/api/v1/episodes/{uuid.uuid4()}/script", json=valid)
    assert saved.status_code == 200
    assert repository.replaced[0].script.title == "端点编辑后的早晨标题"


def test_pipeline_settings_endpoint(daily_plan) -> None:
    repository = StudioRepository(daily_plan)
    client = _studio_client(
        settings=PipelineSettings.legacy_default(),
        production=StudioProduction(),
        studio_editing=_editing_service(repository),
    )
    response = client.put(
        f"/api/v1/runs/{uuid.uuid4()}/pipeline-settings",
        json={"dayBrief": "manual", "allowPaidGeneration": True},
    )
    assert response.status_code == 200
    assert response.json()["pipelineSettings"]["dayBrief"] == "manual"
    assert repository.settings is not None
    assert repository.settings.day_brief is StageMode.MANUAL
