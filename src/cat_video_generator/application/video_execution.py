"""Seedance single-pass提交、轮询、下载、技术QC与语义诊断。

本服务拥有完整视频任务的一条生命周期。它不选择剧情、不生成关键帧，也不自动重试
收费请求；失败后只能由 ``retry-step`` 创建新attempt。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..domain.continuity import EntityKind, EntityLifecycle
from ..domain.prompts import (
    CompiledPrompt,
    compile_shot_video_prompt,
    compile_video_diagnostic_prompt,
    compile_video_prompt,
)
from ..domain.rendering import MediaSource, VideoInputMode, build_video_input_plan
from ..domain.snapshots import VideoInputSnapshot
from ..domain.visual_profiles import SeriesVisualProfile, StyleProfile
from ..domain.workflow import EpisodeStatus, PromptPurpose, StepKind, StepStatus
from .errors import StepRetryRequired
from .ports import (
    AssetStore,
    GatewayError,
    LandedAsset,
    MediaGenerationGateway,
    MediaProbe,
    ProductionStore,
    ReviewFrameExtractor,
    StoredAsset,
    StoredEpisode,
    StoredStep,
    VideoTaskResult,
    VisualReviewGateway,
)


class VideoExecutionService:
    """把一个已准备好视觉输入的Episode推进到人工内容审核。"""

    def __init__(
        self,
        *,
        repository: ProductionStore,
        media_gateway: MediaGenerationGateway,
        asset_store: AssetStore,
        media_probe: MediaProbe,
        provider_name: str,
        resolution: str,
        series_profile: SeriesVisualProfile,
        style_profile: StyleProfile,
        review_gateway: VisualReviewGateway | None = None,
        frame_extractor: ReviewFrameExtractor | None = None,
        diagnostic_mode: str = "off",
        generation_mode: str = "per_shot",
        shot_minimum_seconds: int = 4,
        api_timeout_seconds: float = 120,
        poll_interval_seconds: float = 10,
        task_timeout_seconds: float = 1800,
    ) -> None:
        if diagnostic_mode not in {"off", "diagnostic"}:
            raise ValueError("视频语义诊断模式必须是off或diagnostic")
        if diagnostic_mode == "diagnostic" and (review_gateway is None or frame_extractor is None):
            raise ValueError("diagnostic模式需要审核网关和抽帧能力")
        if generation_mode not in {"per_shot", "single_pass"}:
            raise ValueError("视频生成模式必须是per_shot或single_pass")
        if generation_mode == "per_shot" and frame_extractor is None:
            raise ValueError("per_shot逐镜头生成需要ffmpeg尾帧提取与拼接能力")
        if not 3 <= shot_minimum_seconds <= 8:
            raise ValueError("单镜头最短时长必须在3至8秒之间")
        self._repository = repository
        self._gateway = media_gateway
        self._asset_store = asset_store
        self._probe = media_probe
        self._provider_name = provider_name
        self._resolution = resolution
        self._series_profile = series_profile
        self._style_profile = style_profile
        self._review_gateway = review_gateway
        self._frame_extractor = frame_extractor
        self._diagnostic_mode = diagnostic_mode
        self._generation_mode = generation_mode
        self._shot_minimum_seconds = shot_minimum_seconds
        self._api_timeout = api_timeout_seconds
        self._poll_interval = poll_interval_seconds
        self._task_timeout = task_timeout_seconds

    def execute(
        self,
        episode: StoredEpisode,
        inputs: tuple[StoredAsset, ...],
        *,
        prompt_override: str | None = None,
    ) -> dict[str, Any]:
        """创建或复用视频收费意图；默认逐镜头生成，身份锚定+首帧链接。

        逐镜头模式下每个镜头一段短片：上一镜真实尾帧作为下一镜首帧，
        全部镜头批准后本地拼接为成片进入人工终审。单镜头时长不足或
        结尾精确锁定集回退single-pass。显式Prompt覆盖只作用于single-pass。
        """

        if prompt_override is None and self._use_per_shot(episode):
            return self._generate_per_shot(episode, inputs)
        return self._generate(
            episode,
            inputs,
            prompt_override=prompt_override,
            attempt=1,
        )

    def resume_step(self, episode: StoredEpisode, step: StoredStep) -> dict[str, Any]:
        """只轮询已有task ID；submission_unknown不进入本方法。"""

        if step.kind is not StepKind.VIDEO or step.status not in {
            StepStatus.QUEUED,
            StepStatus.RUNNING,
        }:
            raise ValueError("continue-query只接受已有task ID的queued/running视频步骤")
        episode = self._restore_episode_for_existing_task(episode, step)
        if step.operation_key.startswith("video:shot:"):
            shot_order = int(step.operation_key.rsplit(":", 1)[1])
            duration = self._shot_durations(episode)[shot_order]
            return self._finish_shot(episode, step, shot_order, duration)
        return self._finish(episode, step)

    def reconciliation_candidates(
        self,
        step: StoredStep,
    ) -> tuple[dict[str, Any], ...]:
        """为创建响应丢失的视频Step筛选近期Ark任务，不自动绑定歧义候选。"""

        if step.kind is not StepKind.VIDEO or step.status is not StepStatus.SUBMISSION_UNKNOWN:
            raise ValueError("只有submission_unknown视频步骤可以查询对账候选")
        if step.provider_task_id:
            raise ValueError("该步骤已经具有provider task ID，应直接继续查询")
        snapshot = VideoInputSnapshot.model_validate(step.input_snapshot)
        reference_time = step.submitted_at or step.created_at
        lower_bound = (
            None
            if reference_time is None
            else reference_time.astimezone(timezone.utc) - timedelta(minutes=5)
        )
        upper_bound = (
            None
            if reference_time is None
            else reference_time.astimezone(timezone.utc) + timedelta(minutes=30)
        )
        candidates: list[dict[str, Any]] = []
        for task in self._gateway.list_video_tasks(model=step.model or self._gateway.video_model):
            if not _matches_reconciliation_task(
                task,
                snapshot,
                model=step.model or self._gateway.video_model,
                lower_bound=lower_bound,
                upper_bound=upper_bound,
            ):
                continue
            owner = self._repository.find_step_by_provider_task_id(task.task_id)
            if owner is not None and owner.id != step.id:
                continue
            candidates.append(_task_candidate(task))
        result = tuple(candidates)
        self._repository.patch_step_snapshot(
            step.id,
            {
                "reconciliation_candidates": result,
                "reconciliation_queried_at": datetime.now(timezone.utc),
            },
        )
        return result

    def reconcile_step(
        self,
        episode: StoredEpisode,
        step: StoredStep,
        *,
        provider_task_id: str,
    ) -> dict[str, Any]:
        """人工确认候选后绑定原Ark任务，并沿同一task ID继续下载与QC。"""

        candidates = self.reconciliation_candidates(step)
        if provider_task_id not in {item["taskId"] for item in candidates}:
            raise ValueError("所选Task ID不属于当前步骤的可对账候选")
        owner = self._repository.find_step_by_provider_task_id(provider_task_id)
        if owner is not None and owner.id != step.id:
            raise ValueError("该Ark Task ID已绑定其他WorkflowStep")
        now = datetime.now(timezone.utc)
        self._repository.set_step_status(
            step.id,
            StepStatus.QUEUED,
            provider_task_id=provider_task_id,
            input_snapshot_patch={
                "provider_task_status": "reconciled",
                "reconciled_provider_task_id": provider_task_id,
                "reconciled_at": now,
            },
        )
        episode = self._restore_episode_for_existing_task(episode, step)
        return self._finish(episode, self._repository.get_step(step.id))

    def _restore_episode_for_existing_task(
        self,
        episode: StoredEpisode,
        step: StoredStep,
    ) -> StoredEpisode:
        """将已有Ark task ID的尝试恢复到可以落盘/QC的Episode状态。

        显式重试可能与旧成片审核状态并存；这里只恢复已经拥有task ID的尝试，
        绝不创建新的供应商POST。
        """

        status_changed = False
        if episode.status is EpisodeStatus.CONTENT_REVIEW:
            snapshot = VideoInputSnapshot.model_validate(step.input_snapshot)
            if snapshot.retry_of_step_id is None:
                raise ValueError("非重试视频不能覆盖当前内容审核阶段")
            self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            self._repository.set_episode_status(episode.id, EpisodeStatus.VIDEO_PENDING)
            self._repository.set_episode_status(episode.id, EpisodeStatus.VIDEO_GENERATING)
            status_changed = True
        elif episode.status is EpisodeStatus.FAILED:
            self._repository.set_episode_status(episode.id, EpisodeStatus.VIDEO_PENDING)
            self._repository.set_episode_status(episode.id, EpisodeStatus.VIDEO_GENERATING)
            status_changed = True
        elif episode.status is EpisodeStatus.VIDEO_PENDING:
            self._repository.set_episode_status(episode.id, EpisodeStatus.VIDEO_GENERATING)
            status_changed = True
        elif episode.status not in {EpisodeStatus.VIDEO_GENERATING, EpisodeStatus.MEDIA_QC}:
            raise ValueError(
                f"当前Episode状态{episode.status.value}不能继续已有视频任务"
            )
        if not status_changed:
            return episode
        return self._repository.get_episode(episode.run_id, episode.plan.slot)

    def retry_video(
        self,
        episode: StoredEpisode,
        original_step: StoredStep,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """显式创建同一剧本的新收费attempt，旧Step和Prompt永久保留。"""

        if not original_step.operation_key.startswith("video:"):
            raise ValueError("只支持重试视频步骤")
        # 剧本重规划后，旧视频attempt绑定的故事板只用于审计。
        # 新的收费attempt必须只使用当前Episode最新一组完整批准面板，
        # 否则会把新脚本和旧画面错误组合，无意中再产生一次费用。
        inputs = self._repository.latest_approved_storyboard(episode.id)
        if not 3 <= len(inputs) <= 4:
            raise ValueError("当前Episode没有一组完整批准的最新故事板")
        # 媒体重试应使用当前Episode与当前输入路由重新编译Prompt；旧attempt的实际
        # Prompt仍保留作审计。只有用户显式保存的视频覆盖才继续作为唯一覆盖来源。
        prompt_override = self._repository.get_prompt_overrides(episode.id).get("video")
        attempt = self._repository.next_step_attempt(
            episode_id=episode.id,
            kind=StepKind.VIDEO,
            operation_key=original_step.operation_key,
        )
        if episode.status is EpisodeStatus.CONTENT_REVIEW:
            # 人工打回不会覆盖原成片；新attempt从合法的失败恢复边重新进入视频阶段。
            self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            self._repository.set_episode_status(episode.id, EpisodeStatus.VIDEO_PENDING)
            episode = self._repository.get_episode(episode.run_id, episode.plan.slot)
        elif episode.status is EpisodeStatus.VIDEO_GENERATING:
            # 上一次任务中断（异常或进程重启）会把Episode卡在generating；
            # 显式重试沿失败恢复边回到可收费起点。
            self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            self._repository.set_episode_status(episode.id, EpisodeStatus.VIDEO_PENDING)
            episode = self._repository.get_episode(episode.run_id, episode.plan.slot)
        elif episode.status is EpisodeStatus.FAILED:
            self._repository.set_episode_status(episode.id, EpisodeStatus.VIDEO_PENDING)
            episode = self._repository.get_episode(episode.run_id, episode.plan.slot)
        elif episode.status is not EpisodeStatus.VIDEO_PENDING:
            raise ValueError(
                f"当前Episode状态{episode.status.value}不允许创建新的视频attempt"
            )
        if self._use_per_shot(episode):
            # 逐镜头重试从第一镜重新生成：后续镜头依赖前序真实尾帧，
            # 无法只重做单镜；旧镜头片段与尾帧资产保留审计。
            return self._generate_per_shot(episode, inputs, force_all=True)
        return self._generate(
            episode,
            inputs,
            prompt_override=prompt_override,
            attempt=attempt,
            retry_of_step_id=original_step.id,
            retry_reason=reason,
        )

    def _episode_input_mode(self, episode: StoredEpisode) -> VideoInputMode:
        """结尾精确锁定且无移动关键道具时用严格首尾帧，否则完整故事板。"""

        entities = episode.plan.script.continuity.entities
        has_critical_prop = any(
            entity.kind is EntityKind.PROP for entity in entities
        )
        has_moving_critical_prop = any(
            entity.kind is EntityKind.PROP
            and (
                entity.lifecycle is not EntityLifecycle.PERSIST
                or entity.start_state != entity.end_state
                or entity.form_key != (entity.final_form_key or entity.form_key)
            )
            for entity in entities
        )
        return (
            VideoInputMode.STRICT_FIRST_LAST
            if episode.plan.script.ending.visual_critical
            and has_critical_prop
            and not has_moving_critical_prop
            else VideoInputMode.STORYBOARD_REFERENCE
        )

    def _use_per_shot(self, episode: StoredEpisode) -> bool:
        """逐镜头生成适用条件：配置开启、多镜头、非严格首尾帧集、时长可分。"""

        if self._generation_mode != "per_shot":
            return False
        script = episode.plan.script
        if len(script.shots) < 2:
            return False
        if self._episode_input_mode(episode) is VideoInputMode.STRICT_FIRST_LAST:
            return False
        return (
            len(script.shots) * self._shot_minimum_seconds <= script.duration_seconds
        )

    def _shot_durations(self, episode: StoredEpisode) -> dict[int, int]:
        """把整集时长按镜头均分，余数并入最后一镜。"""

        shots = episode.plan.script.shots
        total = episode.plan.script.duration_seconds
        base = total // len(shots)
        durations = {shot.order: base for shot in shots}
        durations[shots[-1].order] += total - base * len(shots)
        return durations

    def _shot_clip(
        self,
        episode: StoredEpisode,
        shot_order: int,
        *,
        statuses: tuple[str, ...],
    ) -> StoredAsset | None:
        """查本集指定镜头的已落盘片段资产。"""

        key = f"shot_clip:{shot_order}"
        assets = self._repository.list_assets(
            episode_id=episode.id,
            roles=("video_shot",),
            statuses=statuses,
            semantic_keys=(key,),
        )
        matched = [
            item
            for item in assets
            if item.episode_id == episode.id and item.semantic_key == key
        ]
        return matched[-1] if matched else None

    def _tail_frame(
        self,
        episode: StoredEpisode,
        shot_order: int,
    ) -> StoredAsset | None:
        key = f"shot_tail:{shot_order}"
        assets = self._repository.list_assets(
            episode_id=episode.id,
            roles=("shot_tail_frame",),
            statuses=("ready",),
            semantic_keys=(key,),
        )
        matched = [
            item
            for item in assets
            if item.episode_id == episode.id and item.semantic_key == key
        ]
        return matched[-1] if matched else None

    def _generate_per_shot(
        self,
        episode: StoredEpisode,
        inputs: tuple[StoredAsset, ...],
        *,
        force_all: bool = False,
    ) -> dict[str, Any]:
        """逐镜头生成：首帧锚定+真实尾帧链接，全部批准后拼接成片。

        幂等续跑：已批准镜头直接复用；有镜头停在人工审核时返回shot_review
        不再前推。``force_all``用于显式重试——从第一镜重新生成全部镜头
        （后续镜头依赖前序尾帧，无法只重做单镜）。
        """

        if not 3 <= len(inputs) <= 4:
            raise ValueError("Seedance生成前必须具有3至4张完整故事板")
        ordered_storyboard = tuple(sorted(inputs, key=_panel_ordinal))
        if not all(
            item.role == "storyboard_panel" and item.status in {"approved", "ready"}
            for item in ordered_storyboard
        ):
            raise ValueError("Seedance只能使用完整且已批准的故事板面板")
        shots = episode.plan.script.shots
        durations = self._shot_durations(episode)
        if episode.status is EpisodeStatus.VIDEO_PENDING:
            self._repository.set_episode_status(
                episode.id,
                EpisodeStatus.VIDEO_GENERATING,
            )
        tail_frame: StoredAsset | None = None
        shot_clips: list[StoredAsset] = []
        last_step: StoredStep | None = None
        for index, shot in enumerate(shots):
            order = shot.order
            if not force_all:
                approved = self._shot_clip(
                    episode,
                    order,
                    statuses=("approved", "ready"),
                )
                if approved is not None:
                    shot_clips.append(approved)
                    tail_frame = self._tail_frame(episode, order)
                    continue
                pending = self._shot_clip(episode, order, statuses=("candidate",))
                if pending is not None:
                    return {
                        "episodeId": str(episode.id),
                        "slot": episode.plan.slot.value,
                        "status": "shot_review",
                        "shotOrder": order,
                        "assetId": str(pending.id),
                        "message": f"镜头{order}片段等待人工审核",
                    }
            first_frame = (
                ordered_storyboard[0] if index == 0 else tail_frame
            )
            if first_frame is None:
                raise RuntimeError(f"镜头{order}缺少首帧锚点（前序尾帧未就绪）")
            last_frame = (
                ordered_storyboard[-1]
                if index == len(shots) - 1 and index > 0
                else None
            )
            result = self._generate_shot_clip(
                episode,
                shot_order=order,
                first_frame=first_frame,
                last_frame=last_frame,
                duration_seconds=durations[order],
                feedback=None,
            )
            if result["outcome"] == "awaiting_review":
                return {
                    "episodeId": str(episode.id),
                    "slot": episode.plan.slot.value,
                    "status": "shot_review",
                    "shotOrder": order,
                    "assetId": str(result["asset"].id),
                    "message": f"镜头{order}片段转人工审核",
                }
            if result["outcome"] == "rejected":
                # 高置信违规：带违规清单自动重修一次；重修仍被高置信拒绝时
                # 保留候选片段转人工审核，绝不把整集打进失败死局。
                repaired = self._generate_shot_clip(
                    episode,
                    shot_order=order,
                    first_frame=first_frame,
                    last_frame=last_frame,
                    duration_seconds=durations[order],
                    feedback="；".join(result["violations"]),
                )
                if repaired["outcome"] != "approved":
                    return {
                        "episodeId": str(episode.id),
                        "slot": episode.plan.slot.value,
                        "status": "shot_review",
                        "shotOrder": order,
                        "assetId": str(repaired["asset"].id),
                        "message": f"镜头{order}自动重修后仍转人工审核",
                    }
                result = repaired
            shot_clips.append(result["asset"])
            tail_frame = self._ensure_tail_frame(
                episode,
                result["asset"],
                order,
                result["step_id"],
            )
            last_step = self._repository.get_step(result["step_id"])
        return self._stitch_episode(episode, shot_clips, last_step)

    def _generate_shot_clip(
        self,
        episode: StoredEpisode,
        *,
        shot_order: int,
        first_frame: StoredAsset,
        last_frame: StoredAsset | None,
        duration_seconds: int,
        feedback: str | None,
    ) -> dict[str, Any]:
        """生成并审核一个镜头片段；返回approved/awaiting_review/rejected。

        镜头片段使用严格首帧锚定，Ark禁止其与reference media混用，
        因此不带身份参考——身份由首帧素材传递（故事板面板生成时已注入
        身份参考与姿态护栏）。
        """

        frames: tuple[StoredAsset, ...] = (
            (first_frame, last_frame) if last_frame is not None else (first_frame,)
        )
        input_mode = (
            VideoInputMode.STRICT_FIRST_LAST
            if last_frame is not None
            else VideoInputMode.STRICT_FIRST
        )
        input_plan = build_video_input_plan(
            input_mode=input_mode,
            resolution=self._resolution,
            duration_seconds=duration_seconds,
            sources=tuple(_media_source(asset) for asset in frames),
        )
        by_id = {item.id: item for item in frames}
        ordered_inputs = tuple(
            by_id[item.asset_id] for item in input_plan.bindings
        )
        compiled = compile_shot_video_prompt(
            episode.plan,
            shot_order=shot_order,
            input_plan=input_plan,
            style_profile=self._style_profile,
            series_profile=self._series_profile,
        )
        if feedback:
            compiled = CompiledPrompt(
                text=f"{compiled.text}\n【诊断修正】必须修正：{feedback}。",
                char_count=compiled.char_count + len(feedback),
                utf8_bytes=compiled.utf8_bytes + len(feedback.encode("utf-8")),
                warnings=compiled.warnings,
            )
        input_hash = _input_hash(
            compiled.text,
            input_plan.model_dump_json(),
            *(item.sha256 for item in ordered_inputs),
        )
        operation_key = f"video:shot:{shot_order}"
        attempt = self._repository.next_step_attempt(
            episode_id=episode.id,
            kind=StepKind.VIDEO,
            operation_key=operation_key,
        )
        prompt_sha = hashlib.sha256(compiled.text.encode("utf-8")).hexdigest()
        snapshot = VideoInputSnapshot(
            prompt_sha256=prompt_sha,
            input_plan=input_plan,
            input_asset_ids=tuple(item.id for item in ordered_inputs),
            api_request_timeout_seconds=self._api_timeout,
            task_timeout_seconds=self._task_timeout,
            poll_interval_seconds=self._poll_interval,
        )
        step, _ = self._repository.create_step_with_prompt_intent(
            run_id=episode.run_id,
            episode_id=episode.id,
            parent_step_id=None,
            kind=StepKind.VIDEO,
            attempt=attempt,
            operation_key=operation_key,
            provider=self._provider_name,
            model=self._gateway.video_model,
            input_hash=input_hash,
            input_snapshot=snapshot.model_dump(mode="json"),
            prompt_purpose=PromptPurpose.VIDEO,
            prompt_model=self._gateway.video_model,
            prompt_text=compiled.text,
            parent_prompt_id=None,
        )
        if step.status in {StepStatus.QUEUED, StepStatus.RUNNING}:
            return self._finish_shot(
                episode,
                step,
                shot_order,
                duration_seconds,
            )
        if step.status is StepStatus.SUBMISSION_UNKNOWN:
            raise RuntimeError("镜头片段提交结果未知，必须先对账，禁止重复POST")
        if step.status is StepStatus.SUCCEEDED:
            approved = self._shot_clip(
                episode,
                shot_order,
                statuses=("approved", "ready"),
            )
            if approved is None:
                raise RuntimeError("镜头步骤已成功但缺少批准的片段资产")
            return {"outcome": "approved", "asset": approved, "step_id": step.id}
        if step.status is StepStatus.AWAITING_REVIEW:
            pending = self._shot_clip(episode, shot_order, statuses=("candidate",))
            if pending is None:
                raise RuntimeError("镜头步骤等待审核但缺少候选片段资产")
            return {"outcome": "awaiting_review", "asset": pending, "step_id": step.id}
        if step.status in {
            StepStatus.FAILED,
            StepStatus.EXPIRED,
            StepStatus.CANCELLED,
        }:
            raise StepRetryRequired(step.id, operation_key)

        self._repository.set_step_status(step.id, StepStatus.SUBMITTING)
        try:
            task = self._gateway.submit_video(
                prompt=compiled.text,
                input_plan=input_plan,
                input_paths=tuple(item.path for item in ordered_inputs),
            )
        except GatewayError as exc:
            self._repository.fail_step(
                step.id,
                code=exc.code,
                message=str(exc),
                submission_unknown=exc.submission_unknown,
                request_id=exc.request_id,
                input_snapshot_patch={
                    "provider_task_status": (
                        "submission_unknown" if exc.submission_unknown else "failed"
                    )
                },
            )
            if not exc.submission_unknown:
                self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            raise
        self._repository.set_step_status(
            step.id,
            StepStatus.QUEUED,
            provider_task_id=task.task_id,
            input_snapshot_patch={"provider_task_status": task.status},
        )
        return self._finish_shot(
            episode,
            self._repository.get_step(step.id),
            shot_order,
            duration_seconds,
        )

    def _finish_shot(
        self,
        episode: StoredEpisode,
        step: StoredStep,
        shot_order: int,
        duration_seconds: int,
    ) -> dict[str, Any]:
        """轮询镜头片段任务；成功后落盘并做镜头级语义审核。"""

        if step.status is StepStatus.SUBMISSION_UNKNOWN:
            raise RuntimeError("submission_unknown只能人工对账")
        if not step.provider_task_id:
            raise ValueError("恢复镜头片段任务需要provider task ID")
        deadline = time.monotonic() + self._task_timeout
        while time.monotonic() < deadline:
            try:
                task = self._gateway.get_video_task(step.provider_task_id)
            except GatewayError as exc:
                if exc.retryable:
                    time.sleep(self._poll_interval)
                    continue
                self._repository.fail_step(step.id, code=exc.code, message=str(exc))
                raise
            if task.status in {"queued", "running"}:
                current = self._repository.get_step(step.id)
                if task.status == "running" and current.status is StepStatus.QUEUED:
                    self._repository.set_step_status(
                        step.id,
                        StepStatus.RUNNING,
                        input_snapshot_patch={"provider_task_status": task.status},
                    )
                time.sleep(self._poll_interval)
                continue
            if task.status != "succeeded" or not task.video_url:
                self._repository.fail_step(
                    step.id,
                    code=task.error_code or task.status,
                    message=task.error_message or "Seedance镜头任务失败",
                )
                self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
                raise RuntimeError(task.error_message or "Seedance镜头任务失败")
            return self._land_shot_clip(
                episode,
                step,
                task.video_url,
                shot_order,
                duration_seconds,
            )
        ended_at = datetime.now(timezone.utc)
        self._repository.patch_step_snapshot(
            step.id,
            {
                "provider_task_status": "polling_window_elapsed",
                "polling_window_ended_at": ended_at,
            },
        )
        return {
            "episodeId": str(episode.id),
            "slot": episode.plan.slot.value,
            "stepId": str(step.id),
            "status": "provider_running",
            "message": f"镜头{shot_order}任务仍在运行，可按原Task ID继续查询",
        }

    def _land_shot_clip(
        self,
        episode: StoredEpisode,
        step: StoredStep,
        video_url: str,
        shot_order: int,
        duration_seconds: int,
    ) -> dict[str, Any]:
        """下载镜头片段、技术QC、镜头级语义审核。"""

        landed = self._asset_store.download(video_url, suffix=".mp4")
        qc = self._probe.inspect_video(
            landed.path,
            expected_duration_seconds=duration_seconds,
            expected_resolution=self._resolution,
            minimum_duration_seconds=max(1, duration_seconds - 1),
            maximum_duration_seconds=duration_seconds + 1,
        )
        if not qc["passed"]:
            self._repository.fail_step(
                step.id,
                code="media_qc_failed",
                message=";".join(qc["failures"]),
            )
            self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            raise RuntimeError(f"镜头片段技术QC失败: {qc['failures']}")
        asset = self._repository.save_asset(
            run_id=episode.run_id,
            episode_id=episode.id,
            step_id=step.id,
            role="video_shot",
            semantic_key=f"shot_clip:{shot_order}",
            scope="episode",
            status="candidate",
            media_type="video",
            landed=landed,
            metadata=qc,
        )
        current = self._repository.get_step(step.id)
        if current.status is StepStatus.QUEUED:
            self._repository.set_step_status(step.id, StepStatus.RUNNING)
        self._repository.set_step_status(step.id, StepStatus.AWAITING_REVIEW)
        outcome, violations = self._review_shot_clip(episode, asset, shot_order)
        return {
            "outcome": outcome,
            "violations": violations,
            "asset": self._repository.asset_detail(asset.id)
            if outcome == "approved"
            else asset,
            "step_id": step.id,
        }

    def _review_shot_clip(
        self,
        episode: StoredEpisode,
        asset: StoredAsset,
        shot_order: int,
    ) -> tuple[str, tuple[str, ...]]:
        """镜头级抽帧审核：高置信通过自动批准，高置信违规反馈重修，其余人工。"""

        if self._diagnostic_mode == "off":
            return "awaiting_review", ()
        assert asset.step_id is not None
        evidence = self._run_video_diagnosis(
            episode,
            asset,
            shot_order=shot_order,
        )
        if evidence is None:
            self._repository.record_review(
                step_id=asset.step_id,
                asset_id=asset.id,
                source="technical",
                decision="pending",
                reason="镜头片段诊断不可用，转人工审核",
                warnings=[],
                evidence={},
            )
            return "awaiting_review", ()
        passed = bool(evidence.get("diagnosticPassed"))
        confidence = float(evidence.get("confidence", 0.0))
        violations = tuple(str(item) for item in evidence.get("violations", ()))
        if passed and confidence >= 0.8:
            self._repository.set_asset_status(asset.id, "approved")
            self._repository.record_review(
                step_id=asset.step_id,
                asset_id=asset.id,
                source="ark_visual",
                decision="approved",
                reason="镜头片段抽帧诊断自动审核通过",
                warnings=[],
                evidence=evidence,
            )
            return "approved", ()
        if not passed and confidence >= 0.8:
            # 被高置信拒绝的片段标记rejected，前端与后续查询不再当作可用候选。
            self._repository.set_asset_status(asset.id, "rejected")
            self._repository.record_review(
                step_id=asset.step_id,
                asset_id=asset.id,
                source="ark_visual",
                decision="rejected",
                reason="；".join(violations) or "镜头片段存在明确语义错误",
                warnings=[],
                evidence=evidence,
            )
            return "rejected", violations
        self._repository.record_review(
            step_id=asset.step_id,
            asset_id=asset.id,
            source="ark_visual",
            decision="pending",
            reason="镜头片段诊断置信度不足，转人工审核",
            warnings=[],
            evidence=evidence,
        )
        return "awaiting_review", ()

    def _ensure_tail_frame(
        self,
        episode: StoredEpisode,
        clip: StoredAsset,
        shot_order: int,
        step_id: uuid.UUID,
    ) -> StoredAsset:
        """提取已批准镜头的真实尾帧，作为下一镜头的首帧锚点。"""

        existing = self._tail_frame(episode, shot_order)
        if existing is not None:
            return existing
        assert self._frame_extractor is not None
        import tempfile

        with tempfile.TemporaryDirectory(prefix="shot-tail-") as workspace:
            target = Path(workspace) / f"tail-{shot_order}.png"
            self._frame_extractor.extract_last_frame(clip.path, target=target)
            metadata = self._probe.inspect_image(target)
            landed = self._asset_store.import_local(target)
        return self._repository.save_asset(
            run_id=episode.run_id,
            episode_id=episode.id,
            step_id=step_id,
            role="shot_tail_frame",
            semantic_key=f"shot_tail:{shot_order}",
            scope="episode",
            status="ready",
            media_type="image",
            landed=landed,
            metadata=metadata,
        )

    def _stitch_episode(
        self,
        episode: StoredEpisode,
        shot_clips: list[StoredAsset],
        last_step: StoredStep | None,
    ) -> dict[str, Any]:
        """按序拼接全部已批准镜头为成片，进入人工内容审核。"""

        existing_final = [
            item
            for item in self._repository.list_assets(
                episode_id=episode.id,
                roles=("video",),
                statuses=("candidate", "approved", "ready"),
            )
            if item.episode_id == episode.id
        ]
        if existing_final:
            final = existing_final[-1]
            return {
                "episodeId": str(episode.id),
                "slot": episode.plan.slot.value,
                "status": EpisodeStatus.CONTENT_REVIEW.value,
                "assetId": str(final.id),
                "localPath": str(final.path),
                "message": "成片已存在，等待人工内容审核",
            }
        assert self._frame_extractor is not None
        import tempfile

        refreshed = self._repository.get_episode(episode.run_id, episode.plan.slot)
        if refreshed.status is EpisodeStatus.VIDEO_GENERATING:
            self._repository.set_episode_status(refreshed.id, EpisodeStatus.MEDIA_QC)
        with tempfile.TemporaryDirectory(prefix="shot-stitch-") as workspace:
            target = Path(workspace) / "final.mp4"
            self._frame_extractor.concat_videos(
                tuple(clip.path for clip in shot_clips),
                target=target,
            )
            qc = self._probe.inspect_video(
                target,
                expected_duration_seconds=episode.plan.script.duration_seconds,
                expected_resolution=self._resolution,
            )
            if not qc["passed"]:
                self._repository.set_episode_status(refreshed.id, EpisodeStatus.FAILED)
                raise RuntimeError(f"成片拼接QC失败: {qc['failures']}")
            landed = self._asset_store.import_local(target)
        asset = self._repository.save_asset(
            run_id=episode.run_id,
            episode_id=episode.id,
            step_id=None if last_step is None else last_step.id,
            role="video",
            semantic_key=f"video:{episode.id}",
            scope="episode",
            status="candidate",
            media_type="video",
            landed=landed,
            metadata={
                **qc,
                "stitchedFromShotAssetIds": [str(item.id) for item in shot_clips],
            },
        )
        self._repository.set_episode_status(episode.id, EpisodeStatus.CONTENT_REVIEW)
        if last_step is not None:
            self._repository.record_review(
                step_id=last_step.id,
                asset_id=asset.id,
                source="technical",
                decision="pending",
                reason="逐镜头成片拼接完成，等待人工内容审核",
                warnings=[],
                evidence={**qc, "semanticReviewStatus": "diagnostic_pending"},
            )
        self._diagnose(episode, asset)
        return {
            "episodeId": str(episode.id),
            "slot": episode.plan.slot.value,
            "status": EpisodeStatus.CONTENT_REVIEW.value,
            "assetId": str(asset.id),
            "localPath": str(asset.path),
        }

    def _identity_references(self, episode: StoredEpisode) -> tuple[StoredAsset, ...]:
        """选择已批准的人物与猫咪本体身份参考。

        人物优先大头照（锁面容与发型的本体，不锁衣着——衣着自由是系列
        设定），缺失时回退全身照或视角图；猫咪取本集首镜头对应视角的
        Canon。全部缺失时返回空元组，退回纯故事板身份投影（旧Canon兼容）。
        """

        shots = episode.plan.script.shots
        view = shots[0].dominant_view.value if shots else "front"
        if view not in {"front", "side", "back"}:
            view = "front"
        person_keys = ("person:headshot", "person:fullbody", f"person:{view}")
        cat_keys = (f"cat:{view}",)
        candidates = self._repository.list_assets(
            run_id=episode.run_id,
            statuses=("approved", "ready"),
            semantic_keys=(*person_keys, *cat_keys),
        )
        latest: dict[str, StoredAsset] = {}
        for asset in candidates:
            if (
                asset.scope == "canon"
                and asset.media_type == "image"
                and asset.semantic_key
            ):
                latest[asset.semantic_key] = asset
        result: list[StoredAsset] = []
        for key in person_keys:
            if key in latest:
                result.append(latest[key])
                break
        if cat_keys[0] in latest:
            result.append(latest[cat_keys[0]])
        else:
            fallback = [item for key, item in latest.items() if key.startswith("cat:")]
            if fallback:
                result.append(fallback[-1])
        return tuple(result)

    def _generate(
        self,
        episode: StoredEpisode,
        inputs: tuple[StoredAsset, ...],
        *,
        prompt_override: str | None,
        attempt: int,
        retry_of_step_id: uuid.UUID | None = None,
        retry_reason: str | None = None,
    ) -> dict[str, Any]:
        if not 3 <= len(inputs) <= 4:
            raise ValueError("Seedance生成前必须具有3至4张完整故事板")
        ordered_storyboard = tuple(sorted(inputs, key=_panel_ordinal))
        if not all(
            item.role == "storyboard_panel" and item.status in {"approved", "ready"}
            for item in ordered_storyboard
        ):
            raise ValueError("Seedance只能使用完整且已批准的故事板面板")
        # 首尾帧适合固定两个端点；一旦关键道具需要跨镜头移动，中间审核面板就是
        # 必要执行锚点，优先完整故事板，避免模型在捡起/放下阶段复制或遗失道具。
        input_mode = self._episode_input_mode(episode)
        selected_inputs = (
            (ordered_storyboard[0], ordered_storyboard[-1])
            if input_mode is VideoInputMode.STRICT_FIRST_LAST
            else ordered_storyboard
        )
        # 身份锚点直喂Seedance：人物面容/发型与猫咪斑纹不再经故事板转译，
        # 故事板只承担镜头构图职责（业界"资产图+提示词直出"做法）。
        # Ark禁止first/last frame与reference media混用：严格帧模式只能靠
        # 首尾帧承载身份（面板生成时已注入身份参考），身份直喂仅用于
        # storyboard_reference模式。
        identity_inputs = (
            self._identity_references(episode)
            if input_mode is VideoInputMode.STORYBOARD_REFERENCE
            else ()
        )
        input_plan = build_video_input_plan(
            input_mode=input_mode,
            resolution=self._resolution,
            duration_seconds=episode.plan.script.duration_seconds,
            sources=tuple(
                _media_source(asset)
                for asset in (*identity_inputs, *selected_inputs)
            ),
        )
        by_id = {item.id: item for item in (*identity_inputs, *inputs)}
        ordered_inputs = tuple(by_id[item.asset_id] for item in input_plan.bindings)
        compiled = compile_video_prompt(
            episode.plan,
            input_plan=input_plan,
            style_profile=self._style_profile,
            series_profile=self._series_profile,
        )
        if prompt_override is not None and prompt_override.strip():
            compiled = _override_prompt(prompt_override)
        input_hash = _input_hash(
            compiled.text,
            input_plan.model_dump_json(),
            *(item.sha256 for item in ordered_inputs),
        )
        prompt_sha = hashlib.sha256(compiled.text.encode("utf-8")).hexdigest()
        snapshot = VideoInputSnapshot(
            prompt_sha256=prompt_sha,
            input_plan=input_plan,
            input_asset_ids=tuple(item.id for item in ordered_inputs),
            retry_of_step_id=retry_of_step_id,
            retry_reason=retry_reason,
            api_request_timeout_seconds=self._api_timeout,
            task_timeout_seconds=self._task_timeout,
            poll_interval_seconds=self._poll_interval,
        )
        step, _ = self._repository.create_step_with_prompt_intent(
            run_id=episode.run_id,
            episode_id=episode.id,
            parent_step_id=None,
            kind=StepKind.VIDEO,
            attempt=attempt,
            operation_key="video:single_pass",
            provider=self._provider_name,
            model=self._gateway.video_model,
            input_hash=input_hash,
            input_snapshot=snapshot.model_dump(mode="json"),
            prompt_purpose=PromptPurpose.VIDEO,
            prompt_model=self._gateway.video_model,
            prompt_text=compiled.text,
            parent_prompt_id=None,
        )
        if step.status in {StepStatus.QUEUED, StepStatus.RUNNING}:
            return self._finish(episode, step)
        if step.status is StepStatus.SUBMISSION_UNKNOWN:
            raise RuntimeError("视频提交结果未知，必须先对账，禁止重复POST")
        if step.status is StepStatus.SUCCEEDED:
            return _episode_result(episode, "视频步骤已经通过人工审核")
        if step.status is StepStatus.AWAITING_REVIEW:
            return _episode_result(episode, "视频等待人工内容审核")
        if step.status in {StepStatus.FAILED, StepStatus.EXPIRED, StepStatus.CANCELLED}:
            raise StepRetryRequired(step.id, step.operation_key)

        if episode.status is EpisodeStatus.VIDEO_PENDING:
            self._repository.set_episode_status(episode.id, EpisodeStatus.VIDEO_GENERATING)
        # 先提交收费意图，再调用Ark。即使进程在HTTP响应前中断，该Step也会阻止
        # run-day隐式创建第二次付费任务。
        self._repository.set_step_status(step.id, StepStatus.SUBMITTING)
        try:
            task = self._gateway.submit_video(
                prompt=compiled.text,
                input_plan=input_plan,
                input_paths=tuple(item.path for item in ordered_inputs),
            )
        except GatewayError as exc:
            self._repository.fail_step(
                step.id,
                code=exc.code,
                message=str(exc),
                submission_unknown=exc.submission_unknown,
                request_id=exc.request_id,
                input_snapshot_patch={
                    "provider_task_status": (
                        "submission_unknown" if exc.submission_unknown else "failed"
                    )
                },
            )
            if not exc.submission_unknown:
                self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            raise
        self._repository.set_step_status(
            step.id,
            StepStatus.QUEUED,
            provider_task_id=task.task_id,
            input_snapshot_patch={"provider_task_status": task.status},
        )
        return self._finish(episode, self._repository.get_step(step.id))

    def _finish(self, episode: StoredEpisode, step: StoredStep) -> dict[str, Any]:
        if step.status is StepStatus.SUBMISSION_UNKNOWN:
            raise RuntimeError("submission_unknown只能人工对账")
        if not step.provider_task_id:
            raise ValueError("恢复视频任务需要provider task ID")
        deadline = time.monotonic() + self._task_timeout
        while time.monotonic() < deadline:
            try:
                task = self._gateway.get_video_task(step.provider_task_id)
            except GatewayError as exc:
                if exc.retryable:
                    time.sleep(self._poll_interval)
                    continue
                self._repository.fail_step(step.id, code=exc.code, message=str(exc))
                raise
            if task.status in {"queued", "running"}:
                current = self._repository.get_step(step.id)
                if task.status == "running" and current.status is StepStatus.QUEUED:
                    self._repository.set_step_status(
                        step.id,
                        StepStatus.RUNNING,
                        input_snapshot_patch={"provider_task_status": task.status},
                    )
                time.sleep(self._poll_interval)
                continue
            if task.status != "succeeded" or not task.video_url:
                self._repository.fail_step(
                    step.id,
                    code=task.error_code or task.status,
                    message=task.error_message or "Seedance任务失败",
                )
                self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
                raise RuntimeError(task.error_message or "Seedance任务失败")
            return self._land_video(episode, step, task.video_url)
        ended_at = datetime.now(timezone.utc)
        self._repository.patch_step_snapshot(
            step.id,
            {
                "provider_task_status": "polling_window_elapsed",
                "polling_window_ended_at": ended_at,
            },
        )
        return {
            "episodeId": str(episode.id),
            "slot": episode.plan.slot.value,
            "stepId": str(step.id),
            "status": "provider_running",
            "message": "本地监看窗口结束，Ark任务仍可按原Task ID继续查询",
        }

    def _land_video(
        self,
        episode: StoredEpisode,
        step: StoredStep,
        video_url: str,
    ) -> dict[str, Any]:
        landed = self._asset_store.download(video_url, suffix=".mp4")
        episode = self._repository.get_episode(episode.run_id, episode.plan.slot)
        if episode.status is EpisodeStatus.VIDEO_GENERATING:
            self._repository.set_episode_status(episode.id, EpisodeStatus.MEDIA_QC)
        elif episode.status is not EpisodeStatus.MEDIA_QC:
            raise ValueError(
                f"当前Episode状态{episode.status.value}不能落盘视频"
            )
        qc = self._probe.inspect_video(
            landed.path,
            expected_duration_seconds=episode.plan.script.duration_seconds,
            expected_resolution=self._resolution,
        )
        if not qc["passed"]:
            self._repository.fail_step(
                step.id,
                code="media_qc_failed",
                message=";".join(qc["failures"]),
            )
            self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            raise RuntimeError(f"视频技术QC失败: {qc['failures']}")
        asset = self._repository.save_asset(
            run_id=episode.run_id,
            episode_id=episode.id,
            step_id=step.id,
            role="video",
            semantic_key=f"video:{episode.id}",
            scope="episode",
            status="candidate",
            media_type="video",
            landed=landed,
            metadata=qc,
        )
        current = self._repository.get_step(step.id)
        if current.status is StepStatus.QUEUED:
            self._repository.set_step_status(step.id, StepStatus.RUNNING)
        self._repository.set_step_status(step.id, StepStatus.AWAITING_REVIEW)
        self._repository.set_episode_status(episode.id, EpisodeStatus.CONTENT_REVIEW)
        self._repository.record_review(
            step_id=step.id,
            asset_id=asset.id,
            source="technical",
            decision="pending",
            reason="视频技术QC通过，等待人工内容审核",
            warnings=[],
            evidence={**qc, "semanticReviewStatus": "diagnostic_pending"},
        )
        self._diagnose(episode, asset)
        return {
            "episodeId": str(episode.id),
            "slot": episode.plan.slot.value,
            "status": EpisodeStatus.CONTENT_REVIEW.value,
            "assetId": str(asset.id),
            "localPath": str(asset.path),
        }

    def _diagnose(self, episode: StoredEpisode, asset: StoredAsset) -> None:
        """成片抽帧诊断：只记录证据，不改变资产状态（终审归人工）。"""

        evidence = self._run_video_diagnosis(episode, asset)
        if evidence is None:
            return
        assert asset.step_id is not None
        passed = bool(evidence.get("diagnosticPassed"))
        decision = (
            "approved"
            if passed and float(evidence.get("confidence", 0.0)) >= 0.8
            else "pending"
        )
        self._repository.record_review(
            step_id=asset.step_id,
            asset_id=asset.id,
            source="ark_visual",
            decision=decision,
            reason="抽帧诊断仅提供证据，最终决定仍由人工作出",
            warnings=[],
            evidence=evidence,
        )

    def _run_video_diagnosis(
        self,
        episode: StoredEpisode,
        asset: StoredAsset,
        *,
        shot_order: int | None = None,
    ) -> dict[str, Any] | None:
        """执行一次抽帧诊断并返回证据；关闭或异常时返回None。"""

        if self._diagnostic_mode == "off":
            return None
        assert self._review_gateway is not None
        assert self._frame_extractor is not None
        assert asset.step_id is not None
        prompt = compile_video_diagnostic_prompt(
            episode.plan,
            style_profile=self._style_profile,
            shot_order=shot_order,
        )
        video_prompt = self._repository.get_prompt_for_step(
            asset.step_id, purpose=PromptPurpose.VIDEO
        )
        try:
            self._repository.save_prompt(
                step_id=asset.step_id,
                parent_prompt_id=video_prompt.id,
                purpose=PromptPurpose.REVIEW,
                model=self._review_gateway.review_model,
                text=prompt,
            )
        except Exception:
            # 视频诊断是非阻断证据；Prompt无法持久化时跳过外部审核调用，
            # 已通过技术QC的视频仍保留在content_review等待人工判断。
            return None
        frames: tuple[Path, ...] = ()
        hashes: list[str] = []
        try:
            # 单次审核仍只调用一次Ark；提高时序采样密度，降低短暂复制、穿透或
            # 服装断裂刚好落在两个采样点之间而被漏检的概率。
            frames = self._frame_extractor.extract_review_frames(asset, count=12)
            hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in frames]
            result = self._review_gateway.diagnose_video_frames(
                prompt=prompt,
                frame_paths=frames,
            )
            passed = all(
                (
                    result.identity_ok,
                    result.style_ok,
                    result.world_continuity_ok,
                    result.narrative_order_ok,
                )
            )
            return {
                "diagnosticPassed": passed,
                "identityOk": result.identity_ok,
                "styleOk": result.style_ok,
                "worldContinuityOk": result.world_continuity_ok,
                "narrativeOrderOk": result.narrative_order_ok,
                "confidence": result.confidence,
                "violations": list(result.violations),
                "observations": list(result.evidence),
                "orderedFrameSha256": hashes,
                "responseId": result.response_id,
                "requestHash": result.request_hash,
            }
        except (GatewayError, OSError, RuntimeError, ValueError) as exc:
            return {
                "semanticReviewStatus": "pending",
                "orderedFrameSha256": hashes,
                "diagnosticError": getattr(exc, "code", type(exc).__name__),
            }
        finally:
            for frame in frames:
                frame.unlink(missing_ok=True)


def _media_source(asset: StoredAsset) -> MediaSource:
    if asset.semantic_key is None:
        raise ValueError(f"视频输入资产{asset.id}缺少semantic_key")
    return MediaSource(
        asset_id=asset.id,
        semantic_key=asset.semantic_key,
        media_type=asset.media_type,
        sha256=asset.sha256,
        metadata=asset.metadata,
    )


def _override_prompt(value: str) -> CompiledPrompt:
    text = value.strip()
    if not text:
        raise ValueError("视频Prompt覆盖不能为空")
    return CompiledPrompt(
        text=text,
        char_count=len(text),
        utf8_bytes=len(text.encode("utf-8")),
    )


def _panel_ordinal(asset: StoredAsset) -> int:
    ordinal = int(asset.metadata.get("panelOrdinal", 0))
    if ordinal <= 0:
        raise ValueError(f"故事板面板{asset.id}缺少合法panelOrdinal")
    return ordinal


def _input_hash(*values: str) -> str:
    payload = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _episode_result(episode: StoredEpisode, message: str) -> dict[str, Any]:
    return {
        "episodeId": str(episode.id),
        "slot": episode.plan.slot.value,
        "status": episode.status.value,
        "message": message,
    }


def _matches_reconciliation_task(
    task: VideoTaskResult,
    snapshot: VideoInputSnapshot,
    *,
    model: str,
    lower_bound: datetime | None,
    upper_bound: datetime | None,
) -> bool:
    """只按Ark任务列表实际可返回的稳定规格筛选，不推测Prompt等不可见字段。"""

    if task.model is not None and task.model != model:
        return False
    if task.duration_seconds is not None and (
        task.duration_seconds != snapshot.input_plan.duration_seconds
    ):
        return False
    if task.resolution is not None and task.resolution != snapshot.input_plan.resolution:
        return False
    if task.ratio is not None and task.ratio != "9:16":
        return False
    if task.generate_audio is not None and task.generate_audio is not True:
        return False
    if task.created_at is not None:
        if lower_bound is not None and task.created_at < lower_bound:
            return False
        if upper_bound is not None and task.created_at > upper_bound:
            return False
    return True


def _task_candidate(task: VideoTaskResult) -> dict[str, Any]:
    return {
        "taskId": task.task_id,
        "status": task.status,
        "model": task.model,
        "createdAt": None if task.created_at is None else task.created_at.isoformat(),
        "durationSeconds": task.duration_seconds,
        "ratio": task.ratio,
        "resolution": task.resolution,
        "generateAudio": task.generate_audio,
    }
