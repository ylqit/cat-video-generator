"""主题创作台后端：关键帧独立编排、Prompt覆盖与预览的单元测试。"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from cat_video_generator.application.ports import (
    StoredAsset,
    StoredEpisode,
    StoredRun,
)
from cat_video_generator.application.production import ProductionService
from cat_video_generator.application.queries import QueryService
from cat_video_generator.application.visual_preparation import (
    ReferenceSelectionPlan,
    VisualPreparationService,
)
from cat_video_generator.domain.contracts import DailyProductionPlan, VideoInputMode
from cat_video_generator.domain.visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
)
from cat_video_generator.domain.workflow import EpisodeStatus, RunStatus
from test_keyframe_review import (
    ImageGateway,
    ImageProbe,
    ImageRepository,
    ImageStore,
    ReviewGateway,
)


def _episodes(plan: DailyProductionPlan) -> tuple[StoredEpisode, ...]:
    return tuple(
        StoredEpisode(
            id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            plan=episode,
            status=EpisodeStatus.PLANNED,
            selected_video_asset_id=None,
        )
        for episode in plan.episodes
    )


class StudioRepository:
    """记录方案覆盖与Prompt覆盖读写的内存实现。"""

    def __init__(self, plan: DailyProductionPlan) -> None:
        self.plan = plan
        self.episodes = _episodes(plan)
        self.run_id = self.episodes[0].run_id
        self.replaced: list[VideoInputMode] = []
        self.overrides: dict[uuid.UUID, dict[str, str]] = {}

    def get_run(self, run_id):
        return StoredRun(
            id=run_id,
            content_date=self.plan.content_date,
            status=RunStatus.PLANNED.value,
            plan=self.plan,
        )

    def get_episode(self, run_id, slot):
        episode = next(item for item in self.episodes if item.plan.slot is slot)
        if self.replaced:
            episode = StoredEpisode(
                id=episode.id,
                run_id=episode.run_id,
                plan=episode.plan.model_copy(
                    update={"video_input_mode": self.replaced[-1]}
                ),
                status=episode.status,
                selected_video_asset_id=None,
            )
        return episode

    def list_episodes(self, run_id):
        return self.episodes

    def replace_episode_plan(self, *, run_id, episode):
        self.replaced.append(episode.video_input_mode)

    def get_prompt_overrides(self, episode_id):
        return dict(self.overrides.get(episode_id, {}))

    def save_prompt_overrides(self, *, episode_id, overrides):
        if overrides is None:
            self.overrides.pop(episode_id, None)
        else:
            self.overrides[episode_id] = dict(overrides)


class RecordingVisualPreparation:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def prepare(self, episode, *, allow_unverified_keyframes, prompt_overrides):
        self.calls.append(
            {
                "episodeId": episode.id,
                "slot": episode.plan.slot.value,
                "videoInputMode": episode.plan.video_input_mode,
                "promptOverrides": prompt_overrides,
            }
        )
        return ()


class ForbiddenVideoExecution:
    def execute(self, *_args, **_kwargs):  # pragma: no cover - 防御性断言
        raise AssertionError("关键帧编排绝不提交Seedance视频任务")


def test_prepare_keyframes_only_overrides_mode_and_never_executes_video(
    daily_plan: DailyProductionPlan,
) -> None:
    repository = StudioRepository(daily_plan)
    preparation = RecordingVisualPreparation()
    service = ProductionService(
        repository=repository,
        visual_preparation=preparation,
        video_execution=ForbiddenVideoExecution(),
    )
    noon = next(item for item in repository.episodes if item.plan.slot.value == "noon")
    repository.overrides[noon.id] = {"first_frame": "编辑后的首帧"}

    result = service.prepare_keyframes_only(
        repository.run_id,
        allow_paid_generation=True,
    )

    # fixture的evening已是STRICT_FIRST_LAST，只有morning与noon需要覆盖落库。
    assert repository.replaced == [VideoInputMode.STRICT_FIRST_LAST] * 2
    assert len(preparation.calls) == 3
    assert all(
        call["videoInputMode"] is VideoInputMode.STRICT_FIRST_LAST
        for call in preparation.calls
    )
    noon_call = next(call for call in preparation.calls if call["slot"] == "noon")
    assert noon_call["promptOverrides"] == {"first_frame": "编辑后的首帧"}
    assert all(item["keyframesReady"] for item in result["episodes"])


def test_prepare_keyframes_only_merges_explicit_overrides(
    daily_plan: DailyProductionPlan,
) -> None:
    repository = StudioRepository(daily_plan)
    preparation = RecordingVisualPreparation()
    service = ProductionService(
        repository=repository,
        visual_preparation=preparation,
        video_execution=ForbiddenVideoExecution(),
    )
    morning = next(
        item for item in repository.episodes if item.plan.slot.value == "morning"
    )
    repository.overrides[morning.id] = {"first_frame": "旧编辑", "last_frame": "尾帧"}

    service.prepare_keyframes_only(
        repository.run_id,
        slot=morning.plan.slot,
        prompt_overrides={"morning": {"first_frame": "新编辑"}},
        allow_paid_generation=True,
    )

    call = preparation.calls[0]
    assert call["promptOverrides"] == {
        "first_frame": "新编辑",
        "last_frame": "尾帧",
    }
    assert len(repository.replaced) == 1


def test_prepare_keyframes_only_requires_paid(daily_plan: DailyProductionPlan) -> None:
    repository = StudioRepository(daily_plan)
    service = ProductionService(
        repository=repository,
        visual_preparation=RecordingVisualPreparation(),
        video_execution=ForbiddenVideoExecution(),
    )
    with pytest.raises(ValueError, match="allow-paid-generation"):
        service.prepare_keyframes_only(
            repository.run_id,
            allow_paid_generation=False,
        )


def test_save_prompt_overrides_validates_keys(
    daily_plan: DailyProductionPlan,
) -> None:
    repository = StudioRepository(daily_plan)
    service = ProductionService(
        repository=repository,
        visual_preparation=RecordingVisualPreparation(),
        video_execution=ForbiddenVideoExecution(),
    )
    episode_id = repository.episodes[0].id
    with pytest.raises(ValueError, match="不支持的Prompt覆盖键"):
        service.save_prompt_overrides(episode_id, overrides={"thumbnail": "x"})
    service.save_prompt_overrides(
        episode_id,
        overrides={"first_frame": "  编辑后的首帧  ", "video": ""},
    )
    assert repository.get_prompt_overrides(episode_id) == {
        "first_frame": "编辑后的首帧"
    }
    service.save_prompt_overrides(episode_id, overrides={})
    assert repository.get_prompt_overrides(episode_id) == {}


class OverrideRepository(ImageRepository):
    """记录Prompt文本与复用查询哈希的关键帧仓储。"""

    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self.saved_texts: list[str] = []
        self.reuse_queries: list[str] = []
        self.reusable: StoredAsset | None = None

    def find_reusable_asset(self, **kwargs):
        self.reuse_queries.append(kwargs["input_hash"])
        return self.reusable

    def save_prompt(self, **kwargs):
        self.saved_texts.append(kwargs["text"])
        return uuid.uuid4()


def _override_service(
    repository: OverrideRepository,
    frame: Path,
) -> VisualPreparationService:
    return VisualPreparationService(
        repository=repository,
        media_gateway=ImageGateway(),
        visual_review_gateway=ReviewGateway(),
        asset_store=ImageStore(frame),
        media_probe=ImageProbe(),
        provider_name="test",
        keyframe_review_mode="semantic_auto",
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
    )


def _reference(frame: Path) -> ReferenceSelectionPlan:
    asset = StoredAsset(
        id=uuid.uuid4(),
        run_id=None,
        episode_id=None,
        step_id=None,
        role="person",
        media_type="image",
        scope="canon",
        status="approved",
        path=frame,
        sha256="b" * 64,
        metadata={},
        semantic_key="person:front",
    )
    return ReferenceSelectionPlan(("person:front",), (asset,))


def test_prompt_override_text_reaches_seedream_and_prompt_audit(
    tmp_path: Path,
    daily_plan: DailyProductionPlan,
) -> None:
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"not-read-by-fake")
    repository = OverrideRepository(tmp_path)
    episode = StoredEpisode(
        id=repository.step.episode_id,
        run_id=repository.step.run_id,
        plan=daily_plan.episodes[1],
        status=EpisodeStatus.PREPARING_VISUALS,
        selected_video_asset_id=None,
    )
    service = _override_service(repository, frame)

    service._ensure_image(  # noqa: SLF001
        episode,
        _reference(frame),
        target="first_frame",
        allow_unverified_keyframes=False,
        prompt_override="页面编辑后的首帧Prompt",
    )

    assert "页面编辑后的首帧Prompt" in repository.saved_texts


def test_prompt_override_reuse_is_hash_idempotent(
    tmp_path: Path,
    daily_plan: DailyProductionPlan,
) -> None:
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"not-read-by-fake")
    repository = OverrideRepository(tmp_path)
    episode = StoredEpisode(
        id=repository.step.episode_id,
        run_id=repository.step.run_id,
        plan=daily_plan.episodes[1],
        status=EpisodeStatus.PREPARING_VISUALS,
        selected_video_asset_id=None,
    )
    service = _override_service(repository, frame)
    reusable = StoredAsset(
        id=uuid.uuid4(),
        run_id=episode.run_id,
        episode_id=episode.id,
        step_id=uuid.uuid4(),
        role="first_frame",
        media_type="image",
        scope="episode",
        status="approved",
        path=frame,
        sha256="f" * 64,
        metadata={"width": 720, "height": 1280},
    )
    repository.reusable = reusable

    returned = service._ensure_image(  # noqa: SLF001
        episode,
        _reference(frame),
        target="first_frame",
        allow_unverified_keyframes=False,
        prompt_override="编辑版A",
    )
    assert returned is reusable
    assert repository.saved_texts == []
    same_query_hash = repository.reuse_queries[-1]

    # 不同编辑文本产生新哈希；仓储按新哈希查不到既有帧时自然重新生成。
    repository.reusable = None
    service._ensure_image(  # noqa: SLF001
        episode,
        _reference(frame),
        target="first_frame",
        allow_unverified_keyframes=False,
        prompt_override="编辑版B",
    )
    assert repository.reuse_queries[-1] != same_query_hash
    assert "编辑版B" in repository.saved_texts


class PreviewRepository:
    def __init__(self, plan: DailyProductionPlan) -> None:
        self.plan = plan
        self.episode_id = uuid.uuid4()

    def episode_detail(self, episode_id):
        return {
            "id": str(self.episode_id),
            "slot": self.plan.episodes[0].slot.value,
            "script": self.plan.episodes[0].model_dump(mode="json"),
        }

    def get_prompt_overrides(self, episode_id):
        return {"first_frame": "编辑后的首帧"}


def test_prompt_preview_compiles_three_prompts_without_side_effects(
    daily_plan: DailyProductionPlan,
) -> None:
    service = QueryService(PreviewRepository(daily_plan))
    preview = service.prompt_preview(uuid.uuid4())
    assert preview["firstFrame"]
    assert preview["lastFrame"]
    assert preview["video"]
    assert preview["overrides"] == {"first_frame": "编辑后的首帧"}
    assert preview["firstFrame"] != preview["lastFrame"]
