"""隔离Schema中的非付费生产数据流烟测。

该脚本只使用替身导演、本地生成的PNG/MP4、真实Repository事务和FFmpeg。
它不会构造正式运行时Fake Provider，也不会读取或写入正式``cat_video`` Schema。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

from alembic.config import Config
from PIL import Image
from sqlalchemy import text

from alembic import command
from cat_video_generator.application.event_seeds import EventSeedCatalog
from cat_video_generator.application.planning import PlanningService
from cat_video_generator.application.ports import DirectorResult
from cat_video_generator.application.studio_editing import StudioEditingService
from cat_video_generator.application.video_editing import _replace_clip
from cat_video_generator.config import (
    DatabaseOperation,
    DatabaseSettings,
    RuntimeSettings,
    load_local_env,
)
from cat_video_generator.domain.contracts import (
    ActivityFocus,
    CrossSlotReference,
    CrossSlotReferenceRole,
    CrossSlotReferenceTarget,
    DurationBand,
    DurationMode,
    EpisodePlan,
    EpisodeScript,
    EpisodeSources,
    HardConstraint,
    RunCreativeControls,
    SceneRoute,
    ShotDirection,
    Slot,
    SlotCreativeControl,
    StoryConnection,
    StoryConnectionMode,
    StoryInputMode,
    StoryProjectInput,
)
from cat_video_generator.domain.pipeline import PipelineSettings, PlanningMode
from cat_video_generator.domain.prompts import compile_episode_adaptation_prompt
from cat_video_generator.domain.rendering import (
    ClipOrigin,
    MediaSource,
    RenderOperation,
    SequenceStatus,
    VideoSequenceClip,
    VideoSequencePlan,
    build_video_edit_input_plan,
    build_video_input_plan,
)
from cat_video_generator.domain.snapshots import ImageInputSnapshot, VideoInputSnapshot
from cat_video_generator.domain.visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
)
from cat_video_generator.domain.workflow import (
    EpisodeStatus,
    PromptPurpose,
    RunStatus,
    StepKind,
    StepStatus,
)
from cat_video_generator.infrastructure.db.repositories import SqlAlchemyWorkflowRepository
from cat_video_generator.infrastructure.db.session import (
    ALEMBIC_HEAD,
    create_database_engine,
    create_session_factory,
)
from cat_video_generator.infrastructure.media.qc import FfprobeMediaProbe
from cat_video_generator.infrastructure.media.storage import LocalAssetStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SAFE_SCHEMA = re.compile(r"cat_video_test_[a-f0-9]{12}")


class FixtureDirector:
    """只在本脚本中使用的确定性Episode导演。"""

    model = "local-fixture-director"

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        output_name: str,
    ) -> DirectorResult:
        if output_name != "EpisodeScript":
            raise RuntimeError(f"烟测替身不支持{output_name}")
        payload = _morning_episode().script.model_dump(mode="json")
        request_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        return DirectorResult(
            payload=payload,
            response_id="fixture-episode-morning",
            model=self.model,
            request_hash=request_hash,
        )


def _morning_episode() -> EpisodePlan:
    return EpisodePlan(
        slot=Slot.MORNING,
        script=EpisodeScript(
            title="池塘边的浮标信号",
            event_key="morning_fishing_signal",
            location_key="quiet_pond_bank",
            visual_context="outdoor",
            activity_focus=ActivityFocus.CAT_LEAD,
            duration_seconds=10,
            appearance=(
                "同一个中性短发儿童穿米白短袖、深蓝短裤和防滑便鞋；"
                "同一只灰白猫保持正常四足比例。"
            ),
            story_text=(
                "清晨池塘边，灰白猫伏在人物脚边先盯住轻轻晃动的浮标，耳朵随水声转向；"
                "人物双手稳定控制鱼竿并顺着猫咪视线收紧钓线。浮标再次下沉后人物平稳提竿，"
                "猫咪退到安全位置抬头观察，最后人物放松鱼竿并轻摸猫咪脑袋形成回应。"
            ),
            relationship_arc=(
                "猫咪先发现浮标信号，人物负责鱼竿和钓线操作；猫咪安全退回人物脚边，"
                "人物完成回应并共同看向恢复平静的水面。"
            ),
            shots=[
                ShotDirection(
                    order=1,
                    direction=(
                        "中景平视固定镜头，人物在左侧双手握住鱼竿，灰白猫在右前方四足伏地；"
                        "猫咪先以耳朵和视线提示浮标下沉，人物沿猫咪视线平稳提竿，钓线始终"
                        "从鱼竿尖端直达水面浮标且不经过猫咪，双方站位落稳后切镜。"
                    ),
                ),
                ShotDirection(
                    order=2,
                    direction=(
                        "中近景缓慢推近，猫咪退回人物脚边抬头，人物放松鱼竿后俯身轻摸猫咪"
                        "头顶；浮标在背景水面恢复平稳，人猫都停止移动时结束。"
                    ),
                ),
            ],
            hard_constraints=[
                HardConstraint(
                    shot_orders=[1],
                    text=(
                        "钓线一端始终连接人物控制的鱼竿尖端，另一端连接水面同一个浮标；"
                        "钓线不得连接、经过或缠绕猫咪。"
                    ),
                )
            ],
            sound_design="池水、微风、草叶和鱼线轻响同步，无对白、旁白或歌词。",
            ending="人物放松鱼竿并轻摸猫咪脑袋，浮标在背景水面恢复平稳。",
        ),
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _run(command_line: list[str]) -> str:
    result = subprocess.run(
        command_line,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    return result.stdout.strip()


def _make_png(path: Path, colour: tuple[int, int, int]) -> None:
    Image.new("RGB", (480, 854), colour).save(path, format="PNG")


def _make_video(
    ffmpeg: Path,
    path: Path,
    *,
    duration_seconds: int,
    colour: str,
    frequency: int,
) -> None:
    _run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={colour}:s=480x854:r=30:d={duration_seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000:duration={duration_seconds}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ]
    )


def _audio_md5(ffmpeg: Path, path: Path) -> str:
    return _run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-c",
            "copy",
            "-f",
            "md5",
            "-",
        ]
    )


def _reviewable_image(
    repository: SqlAlchemyWorkflowRepository,
    store: LocalAssetStore,
    *,
    run_id: uuid.UUID,
    episode_id: uuid.UUID,
    source_path: Path,
    target: str,
    role: str,
    semantic_key: str,
):
    prompt = f"本地烟测{target}图片Prompt"
    digest = _sha(prompt)
    step, _ = repository.create_step_with_prompt_intent(
        run_id=run_id,
        episode_id=episode_id,
        parent_step_id=None,
        kind=StepKind.IMAGE,
        attempt=1,
        operation_key=f"image:{target}",
        provider="local-fixture",
        model="local-image-fixture",
        input_hash=digest,
        input_snapshot=ImageInputSnapshot(
            target=target,
            prompt_sha256=digest,
            reference_asset_ids=(),
            reference_sha256=(),
        ).model_dump(mode="json"),
        prompt_purpose=PromptPurpose.IMAGE,
        prompt_model="local-image-fixture",
        prompt_text=prompt,
        parent_prompt_id=None,
    )
    repository.set_step_status(step.id, StepStatus.SUBMITTING)
    repository.set_step_status(step.id, StepStatus.AWAITING_REVIEW)
    asset = repository.save_asset(
        run_id=run_id,
        episode_id=episode_id,
        step_id=step.id,
        role=role,
        semantic_key=semantic_key,
        scope="episode",
        status="candidate",
        media_type="image",
        landed=store.import_local(source_path),
        metadata={"fixture": True, "width": 480, "height": 854},
    )
    repository.commit_asset_review(
        asset_id=asset.id,
        source="human",
        decision="approved",
        reason="本地数据流烟测人工批准",
        warnings=[],
        evidence={"fixture": True},
    )
    return repository.asset_detail(asset.id), step.id


def _create_video_step(
    repository: SqlAlchemyWorkflowRepository,
    *,
    run_id: uuid.UUID,
    episode_id: uuid.UUID,
    operation_key: str,
    prompt: str,
    input_plan,
    sequence_id: uuid.UUID | None = None,
    selection: tuple[int, int] | None = None,
):
    digest = _sha(prompt)
    step, _ = repository.create_step_with_prompt_intent(
        run_id=run_id,
        episode_id=episode_id,
        parent_step_id=None,
        kind=StepKind.VIDEO,
        attempt=1,
        operation_key=operation_key,
        provider="local-fixture",
        model="local-video-fixture",
        input_hash=digest,
        input_snapshot=VideoInputSnapshot(
            prompt_sha256=digest,
            input_plan=input_plan,
            input_asset_ids=tuple(item.asset_id for item in input_plan.bindings),
            render_section_order=0 if input_plan.operation is RenderOperation.EDIT else 1,
            sequence_id=sequence_id,
            selection_start_ms=None if selection is None else selection[0],
            selection_end_ms=None if selection is None else selection[1],
            source_start_ms=None if selection is None else selection[0],
            source_end_ms=None if selection is None else selection[1],
            edit_instruction=(
                None if selection is None else "仅修正选中区间的浮标动作，保持区间外原视频"
            ),
        ).model_dump(mode="json"),
        prompt_purpose=PromptPurpose.VIDEO,
        prompt_model="local-video-fixture",
        prompt_text=prompt,
        parent_prompt_id=None,
    )
    repository.set_step_status(step.id, StepStatus.SUBMITTING)
    repository.set_step_status(
        step.id,
        StepStatus.QUEUED,
        provider_task_id=f"fixture-{operation_key}",
    )
    repository.set_step_status(step.id, StepStatus.RUNNING)
    repository.set_step_status(step.id, StepStatus.AWAITING_REVIEW)
    return repository.get_step(step.id)


def _run_smoke(
    repository: SqlAlchemyWorkflowRepository,
    runtime: RuntimeSettings,
    root: Path,
) -> dict[str, Any]:
    ffmpeg = runtime.ffmpeg_path
    ffprobe = runtime.ffprobe_path
    if ffmpeg is None or ffprobe is None:
        raise RuntimeError("本地数据流烟测要求ffmpeg和ffprobe可用")

    media_root = root / "media"
    store = LocalAssetStore(
        work_root=media_root / "work",
        asset_root=media_root / "assets",
        delivery_root=media_root / "output",
        ffmpeg_path=ffmpeg,
    )
    probe = FfprobeMediaProbe(ffprobe)
    planning = PlanningService(
        repository=repository,
        director=FixtureDirector(),
        provider_name="local-fixture",
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
        event_seed_catalog=EventSeedCatalog(PROJECT_ROOT / "content" / "events"),
    )
    controls = RunCreativeControls(
        slot_controls=[
            SlotCreativeControl(slot=Slot.MORNING, duration_mode=DurationMode.SHORT),
            SlotCreativeControl(slot=Slot.NOON),
            SlotCreativeControl(slot=Slot.EVENING),
        ]
    )
    project = StoryProjectInput(
        theme="池塘边钓鱼的连续生活故事",
        input_mode=StoryInputMode.EPISODE_SCRIPTS,
        scene_route=SceneRoute.PROGRESSIVE_LOCATIONS,
        episode_sources=EpisodeSources(
            morning=(
                "清晨人物在池塘边稳定持竿，灰白猫先观察浮标信号，人物随后回应并"
                "平稳提竿，最后轻摸回到脚边的猫咪。"
            )
        ),
    )
    created = planning.create_project(
        target_date=date(2026, 8, 12),
        project_input=project,
        allow_paid_generation=False,
        pipeline_settings=PipelineSettings(
            planningMode=PlanningMode.GUIDED_SEQUENTIAL,
            allowPaidGeneration=False,
        ),
        creative_controls=controls,
    )
    run_id = created.run_id
    if repository.workflow_graph(run_id)["steps"]:
        raise AssertionError("已有剧本guided项目创建时不应产生收费Step")

    planned = planning.plan_slot(
        run_id,
        slot=Slot.MORNING,
        allow_paid_generation=True,
    )
    morning = repository.get_episode(run_id, Slot.MORNING)
    if planned.episode != morning.plan:
        raise AssertionError("替身导演输出没有成为Morning正式镜头卡")
    if len(repository.list_episodes(run_id)) != 1:
        raise AssertionError("顺序模式不应提前创建Noon或Evening Episode")

    repository.set_run_status(run_id, RunStatus.GENERATING)
    repository.set_episode_status(morning.id, EpisodeStatus.PREPARING_VISUALS)
    look_png = root / "look.png"
    anchor_png = root / "anchor.png"
    _make_png(look_png, (230, 220, 195))
    _make_png(anchor_png, (140, 190, 185))
    look, look_step = _reviewable_image(
        repository,
        store,
        run_id=run_id,
        episode_id=morning.id,
        source_path=look_png,
        target="look",
        role="look_reference",
        semantic_key="look:morning-fixture",
    )
    anchor, anchor_step = _reviewable_image(
        repository,
        store,
        run_id=run_id,
        episode_id=morning.id,
        source_path=anchor_png,
        target="opening_anchor",
        role="opening_anchor",
        semantic_key="opening:morning-fixture",
    )
    repository.set_episode_status(morning.id, EpisodeStatus.VIDEO_PENDING)

    base_path = root / "morning-base.mp4"
    replacement_path = root / "replacement.mp4"
    _make_video(ffmpeg, base_path, duration_seconds=10, colour="0x4f8090", frequency=440)
    _make_video(ffmpeg, replacement_path, duration_seconds=3, colour="0x9b7055", frequency=660)
    anchor_source = MediaSource(
        asset_id=anchor.id,
        semantic_key=anchor.semantic_key or anchor.role,
        media_type="image",
        sha256=anchor.sha256,
        metadata=anchor.metadata,
    )
    initial_input = build_video_input_plan(
        operation=RenderOperation.INITIAL,
        resolution="480p",
        duration_seconds=10,
        source=anchor_source,
    )
    initial_step = _create_video_step(
        repository,
        run_id=run_id,
        episode_id=morning.id,
        operation_key="video:single_pass",
        prompt="本地烟测Morning Seedance实际Prompt",
        input_plan=initial_input,
    )
    repository.set_episode_status(morning.id, EpisodeStatus.VIDEO_GENERATING)
    repository.set_episode_status(morning.id, EpisodeStatus.MEDIA_QC)
    base_qc = probe.inspect_video(
        base_path,
        expected_duration_seconds=10,
        expected_resolution="480p",
    )
    if not base_qc["passed"]:
        raise AssertionError(f"基础视频Fixture未通过QC：{base_qc}")
    base_asset = repository.save_asset(
        run_id=run_id,
        episode_id=morning.id,
        step_id=initial_step.id,
        role="video",
        semantic_key="video:morning-fixture-v1",
        scope="episode",
        status="candidate",
        media_type="video",
        landed=store.import_local(base_path),
        metadata={"fixture": True, "providerTaskId": initial_step.provider_task_id, "qc": base_qc},
    )
    initial_plan = VideoSequencePlan(
        duration_ms=int(base_qc["durationMs"]),
        clips=[
            VideoSequenceClip(
                order=1,
                source_asset_id=base_asset.id,
                source_start_ms=0,
                source_end_ms=int(base_qc["durationMs"]),
                timeline_start_ms=0,
                timeline_end_ms=int(base_qc["durationMs"]),
                origin=ClipOrigin.ORIGINAL,
            )
        ],
    )
    initial_sequence = repository.create_video_sequence(
        episode_id=morning.id,
        parent_sequence_id=None,
        base_asset_id=base_asset.id,
        rendered_asset_id=base_asset.id,
        status=SequenceStatus.CONTENT_REVIEW,
        plan=initial_plan,
    )
    repository.set_episode_status(morning.id, EpisodeStatus.CONTENT_REVIEW)
    repository.commit_asset_review(
        asset_id=base_asset.id,
        source="human",
        decision="approved",
        reason="本地Fixture视频可播放",
        warnings=[],
        evidence={"fixture": True},
    )

    studio = StudioEditingService(
        repository=repository,
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
        video_resolution="480p",
    )
    outcome_summary = "猫咪确认浮标信号后退回人物脚边，人物完成提竿并安抚猫咪。"
    studio.confirm_outcome(
        run_id,
        Slot.MORNING,
        {
            "summary": outcome_summary,
            "carryForward": ["同一套钓具已整理完毕"],
            "doNotCarryForward": ["不要继承模型偶发的多余鱼桶"],
        },
    )

    noon_source = "中午来到河岸继续钓鱼，猫咪观察新的水面信号，人物负责控制同一套钓具。"
    without_connection = compile_episode_adaptation_prompt(
        user_episode_text=noon_source,
        project_theme=project.theme,
        scene_route=project.scene_route,
        slot=Slot.NOON,
        activity_focus=ActivityFocus.CAT_LEAD,
        duration_band=DurationBand.SHORT,
    )
    connection_brief = "只关联上午已经整理完成的同一套钓具；中午场景改到河岸，不复述上午提竿。"
    connection = StoryConnection(
        use_for_director=True,
        mode=StoryConnectionMode.SELECTED_LINK,
        brief=connection_brief,
    )
    with_connection = compile_episode_adaptation_prompt(
        user_episode_text=noon_source,
        project_theme=project.theme,
        scene_route=project.scene_route,
        slot=Slot.NOON,
        activity_focus=ActivityFocus.CAT_LEAD,
        duration_band=DurationBand.SHORT,
        story_connection=connection,
    )
    if connection_brief in without_connection or outcome_summary in without_connection:
        raise AssertionError("未启用关联时Noon导演Prompt不应读取前序事实")
    if connection_brief not in with_connection or outcome_summary in with_connection:
        raise AssertionError("启用关联时只应读取关联卡正文，不应读取完整结果卡")
    studio.update_story_connection(
        run_id,
        Slot.NOON,
        connection.model_dump(mode="json", by_alias=True),
    )
    reference = CrossSlotReference(
        asset_id=anchor.id,
        role=CrossSlotReferenceRole.COMPOSITION,
        apply_to=CrossSlotReferenceTarget.BOTH,
    )
    studio.update_cross_slot_references(
        run_id,
        Slot.NOON,
        [reference.model_dump(mode="json", by_alias=True)],
    )
    hypothetical_noon_anchor = MediaSource(
        asset_id=uuid.uuid4(),
        semantic_key="opening:noon-fixture",
        media_type="image",
        sha256="1" * 64,
        metadata={},
    )
    reference_plan = build_video_input_plan(
        operation=RenderOperation.INITIAL,
        resolution="480p",
        duration_seconds=10,
        source=hypothetical_noon_anchor,
        references=(anchor_source,),
    )
    if [item.prompt_alias for item in reference_plan.bindings] != ["@图片1", "@图片2"]:
        raise AssertionError("显式前序参考没有按Ark素材别名顺序编译")

    before_png = root / "before.png"
    after_png = root / "after.png"
    _make_png(before_png, (70, 100, 120))
    _make_png(after_png, (90, 120, 140))
    before = repository.save_asset(
        run_id=run_id,
        episode_id=morning.id,
        step_id=None,
        role="boundary_frame",
        semantic_key="boundary:before-fixture",
        scope="episode",
        status="approved",
        media_type="image",
        landed=store.import_local(before_png),
        metadata={"timestampMs": 3000, "fixture": True},
    )
    after = repository.save_asset(
        run_id=run_id,
        episode_id=morning.id,
        step_id=None,
        role="boundary_frame",
        semantic_key="boundary:after-fixture",
        scope="episode",
        status="approved",
        media_type="image",
        landed=store.import_local(after_png),
        metadata={"timestampMs": 6000, "fixture": True},
    )
    base_source = MediaSource(
        asset_id=base_asset.id,
        semantic_key=base_asset.semantic_key or base_asset.role,
        media_type="video",
        sha256=base_asset.sha256,
        metadata=base_asset.metadata,
    )
    edit_input = build_video_edit_input_plan(
        resolution="480p",
        duration_seconds=10,
        source_video=base_source,
        before_frame=MediaSource(
            before.id, before.semantic_key or before.role, "image", before.sha256, before.metadata
        ),
        after_frame=MediaSource(
            after.id, after.semantic_key or after.role, "image", after.sha256, after.metadata
        ),
    )
    edit_step = _create_video_step(
        repository,
        run_id=run_id,
        episode_id=morning.id,
        operation_key="video:edit:3000-6000",
        prompt="严格编辑选中区间；区间外沿用原视频素材，允许轻微编码差异。",
        input_plan=edit_input,
        sequence_id=initial_sequence.id,
        selection=(3000, 6000),
    )
    replacement_landed = store.import_local(replacement_path)
    replacement_asset = repository.save_asset(
        run_id=run_id,
        episode_id=morning.id,
        step_id=edit_step.id,
        role="video_edit_segment",
        semantic_key="video-edit-segment:fixture",
        scope="episode",
        status="ready",
        media_type="video",
        landed=replacement_landed,
        metadata={"fixture": True, "durationMs": 3000},
    )
    edited_landed = store.render_range_replacement(
        base_path=base_asset.path,
        replacement_path=replacement_asset.path,
        replacement_duration_ms=3000,
        start_ms=3000,
        end_ms=6000,
    )
    edited_qc = probe.inspect_video(
        edited_landed.path,
        expected_duration_seconds=10,
        expected_resolution="480p",
    )
    if not edited_qc["passed"]:
        raise AssertionError(f"区间替换输出未通过QC：{edited_qc}")
    edited_asset = repository.save_asset(
        run_id=run_id,
        episode_id=morning.id,
        step_id=edit_step.id,
        role="video",
        semantic_key="video:morning-fixture-v2",
        scope="episode",
        status="candidate",
        media_type="video",
        landed=edited_landed,
        metadata={"fixture": True, "qc": edited_qc, "parentSequenceId": str(initial_sequence.id)},
    )
    edited_plan = _replace_clip(
        initial_plan,
        start_ms=3000,
        end_ms=6000,
        replacement_asset_id=replacement_asset.id,
        replacement_step_id=edit_step.id,
    )
    edited_sequence = repository.create_video_sequence(
        episode_id=morning.id,
        parent_sequence_id=initial_sequence.id,
        base_asset_id=base_asset.id,
        rendered_asset_id=edited_asset.id,
        status=SequenceStatus.CONTENT_REVIEW,
        plan=edited_plan,
    )
    repository.commit_asset_review(
        asset_id=edited_asset.id,
        source="human",
        decision="approved",
        reason="本地区间替换版本可播放",
        warnings=[],
        evidence={"fixture": True, "selectionStartMs": 3000, "selectionEndMs": 6000},
    )
    repository.select_video_sequence(
        edited_sequence.id,
        revoke_confirmed_outcome=False,
        keep_confirmed_outcome=True,
    )

    original_audio = _audio_md5(ffmpeg, base_asset.path)
    edited_audio = _audio_md5(ffmpeg, edited_asset.path)
    if original_audio != edited_audio:
        raise AssertionError("区间替换没有完整保留原视频音轨")

    graph = repository.workflow_graph(run_id)
    trace = repository.step_trace(edit_step.id)
    run_projection = graph["run"]
    if (
        run_projection["activeSlot"] != "noon"
        or run_projection["nextSlot"] != "evening"
        or run_projection["slotAvailability"]["noon"] != "ready"
        or run_projection["slotAvailability"]["evening"] != "locked"
    ):
        raise AssertionError("Morning结果卡确认后应只解锁Noon")
    if run_projection["connectionStatus"]["noon"] != "confirmed_enabled":
        raise AssertionError("Noon剧情关联卡状态未进入查询投影")
    if not run_projection["crossSlotReferences"]["noon"]:
        raise AssertionError("显式前序媒体引用未进入查询投影")
    actual_prompts = trace.get("actualPrompts") or []
    if not actual_prompts or not trace.get("inputBindings"):
        raise AssertionError("视频编辑节点Trace缺少实际Prompt或输入绑定")
    real_future_steps = [
        step
        for step in graph["steps"]
        if step.get("episodeId") not in {None, str(morning.id)}
    ]
    if real_future_steps:
        raise AssertionError("未执行的Noon/Evening不应存在收费Step")

    return {
        "runId": str(run_id),
        "morningDirectorAttempt": planned.attempt,
        "morningMediaStepIds": [
            str(look_step),
            str(anchor_step),
            str(initial_step.id),
            str(edit_step.id),
        ],
        "revisionCount": len(repository.list_video_sequences(morning.id)),
        "selectedRevision": edited_sequence.revision,
        "durationMs": edited_qc["durationMs"],
        "audioMd5Match": original_audio == edited_audio,
        "workflowNodeCount": len(graph["workflowNodes"]),
        "stepCount": len(graph["steps"]),
        "assetCount": len(graph["assets"]),
        "reviewCount": len(graph["reviews"]),
        "noonAndEveningPaidSteps": len(real_future_steps),
        "arkCalled": False,
    }


def main() -> None:
    load_local_env(PROJECT_ROOT / ".env")
    runtime = RuntimeSettings.from_env(config_root=PROJECT_ROOT)
    base = DatabaseSettings.from_env()
    schema = f"cat_video_test_{uuid.uuid4().hex[:12]}"
    if _SAFE_SCHEMA.fullmatch(schema) is None:
        raise RuntimeError("拒绝使用不安全的烟测Schema名称")
    settings = replace(base, schema=schema)
    engine = create_database_engine(
        settings,
        DatabaseOperation.TEST,
        pool_size=1,
        max_overflow=0,
    )
    previous_schema = os.environ.get("CAT_VIDEO_DB_SCHEMA")
    os.environ["CAT_VIDEO_DB_SCHEMA"] = schema
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    cleaned = False
    try:
        with engine.connect() as connection:
            quoted = connection.dialect.identifier_preparer.quote_schema(schema)
            connection.execute(text(f"CREATE SCHEMA {quoted}"))
            connection.commit()
            config.attributes["connection"] = connection
            config.attributes["schema"] = schema
            command.upgrade(config, "head")
            revision = connection.execute(
                text(f"SELECT version_num FROM {quoted}.alembic_version")
            ).scalar_one()
            if revision != ALEMBIC_HEAD:
                raise AssertionError(f"烟测Schema迁移为{revision}，期望{ALEMBIC_HEAD}")
        repository = SqlAlchemyWorkflowRepository(create_session_factory(engine))
        with tempfile.TemporaryDirectory(prefix="cat-video-local-smoke-") as temp:
            summary = _run_smoke(repository, runtime, Path(temp))
    finally:
        config.attributes.pop("connection", None)
        config.attributes.pop("schema", None)
        engine.dispose()
        cleanup_engine = create_database_engine(
            settings,
            DatabaseOperation.TEST,
            pool_size=1,
            max_overflow=0,
        )
        try:
            with cleanup_engine.begin() as connection:
                quoted = connection.dialect.identifier_preparer.quote_schema(schema)
                connection.execute(text(f"DROP SCHEMA {quoted} CASCADE"))
            cleaned = True
        finally:
            cleanup_engine.dispose()
            if previous_schema is None:
                os.environ.pop("CAT_VIDEO_DB_SCHEMA", None)
            else:
                os.environ["CAT_VIDEO_DB_SCHEMA"] = previous_schema
    summary["schemaCleaned"] = cleaned
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
