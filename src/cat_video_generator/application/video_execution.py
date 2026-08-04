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

from ..domain.continuity import EntityKind
from ..domain.prompts import CompiledPrompt, compile_video_diagnostic_prompt, compile_video_prompt
from ..domain.rendering import MediaSource, VideoInputMode, build_video_input_plan
from ..domain.snapshots import VideoInputSnapshot
from ..domain.visual_profiles import StyleProfile
from ..domain.workflow import EpisodeStatus, PromptPurpose, StepKind, StepStatus
from .errors import StepRetryRequired
from .ports import (
    AssetStore,
    GatewayError,
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
        style_profile: StyleProfile,
        review_gateway: VisualReviewGateway | None = None,
        frame_extractor: ReviewFrameExtractor | None = None,
        diagnostic_mode: str = "off",
        api_timeout_seconds: float = 120,
        poll_interval_seconds: float = 10,
        task_timeout_seconds: float = 1800,
    ) -> None:
        if diagnostic_mode not in {"off", "diagnostic"}:
            raise ValueError("视频语义诊断模式必须是off或diagnostic")
        if diagnostic_mode == "diagnostic" and (review_gateway is None or frame_extractor is None):
            raise ValueError("diagnostic模式需要审核网关和抽帧能力")
        self._repository = repository
        self._gateway = media_gateway
        self._asset_store = asset_store
        self._probe = media_probe
        self._provider_name = provider_name
        self._resolution = resolution
        self._style_profile = style_profile
        self._review_gateway = review_gateway
        self._frame_extractor = frame_extractor
        self._diagnostic_mode = diagnostic_mode
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
        """创建或复用唯一single-pass收费意图。"""

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
        if episode.status is EpisodeStatus.FAILED:
            self._repository.set_episode_status(episode.id, EpisodeStatus.VIDEO_PENDING)
            self._repository.set_episode_status(episode.id, EpisodeStatus.VIDEO_GENERATING)
            episode = self._repository.get_episode(episode.run_id, episode.plan.slot)
        return self._finish(episode, self._repository.get_step(step.id))

    def retry_video(
        self,
        episode: StoredEpisode,
        original_step: StoredStep,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """显式创建同一剧本的新收费attempt，旧Step和Prompt永久保留。"""

        if original_step.operation_key != "video:single_pass":
            raise ValueError("只支持重试single-pass视频步骤")
        snapshot = VideoInputSnapshot.model_validate(original_step.input_snapshot)
        submitted_inputs = tuple(
            self._repository.asset_detail(asset_id) for asset_id in snapshot.input_asset_ids
        )
        storyboard_step_ids = {item.step_id for item in submitted_inputs}
        if len(storyboard_step_ids) != 1 or None in storyboard_step_ids:
            raise ValueError("原视频步骤没有绑定同一组故事板")
        storyboard_step_id = next(iter(storyboard_step_ids))
        inputs = tuple(
            item
            for item in self._repository.list_assets(
                run_id=episode.run_id,
                episode_id=episode.id,
                roles=("storyboard_panel",),
                statuses=("approved", "ready"),
            )
            if item.step_id == storyboard_step_id
        )
        prompt = self._repository.get_prompt_for_step(
            original_step.id,
            purpose=PromptPurpose.VIDEO,
        ).text
        attempt = self._repository.next_step_attempt(
            episode_id=episode.id,
            kind=StepKind.VIDEO,
            operation_key=original_step.operation_key,
        )
        if episode.status is EpisodeStatus.FAILED:
            self._repository.set_episode_status(episode.id, EpisodeStatus.VIDEO_PENDING)
            episode = self._repository.get_episode(episode.run_id, episode.plan.slot)
        return self._generate(
            episode,
            inputs,
            prompt_override=prompt,
            attempt=attempt,
            retry_of_step_id=original_step.id,
            retry_reason=reason,
        )

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
        has_critical_prop = any(
            item.kind is EntityKind.PROP
            for item in episode.plan.script.continuity.entities
        )
        input_mode = (
            VideoInputMode.STRICT_FIRST_LAST
            if episode.plan.script.ending.visual_critical and has_critical_prop
            else VideoInputMode.STORYBOARD_REFERENCE
        )
        selected_inputs = (
            (ordered_storyboard[0], ordered_storyboard[-1])
            if input_mode is VideoInputMode.STRICT_FIRST_LAST
            else ordered_storyboard
        )
        input_plan = build_video_input_plan(
            input_mode=input_mode,
            resolution=self._resolution,
            duration_seconds=episode.plan.script.duration_seconds,
            sources=tuple(_media_source(asset) for asset in selected_inputs),
        )
        by_id = {item.id: item for item in inputs}
        ordered_inputs = tuple(by_id[item.asset_id] for item in input_plan.bindings)
        compiled = compile_video_prompt(
            episode.plan,
            input_plan=input_plan,
            style_profile=self._style_profile,
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
        self._repository.set_episode_status(episode.id, EpisodeStatus.MEDIA_QC)
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
        if self._diagnostic_mode == "off":
            return
        assert self._review_gateway is not None
        assert self._frame_extractor is not None
        assert asset.step_id is not None
        prompt = compile_video_diagnostic_prompt(episode.plan)
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
            return
        frames: tuple[Path, ...] = ()
        hashes: list[str] = []
        try:
            frames = self._frame_extractor.extract_review_frames(asset, count=8)
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
            decision = "approved" if passed and result.confidence >= 0.8 else "pending"
            evidence = {
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
            decision = "pending"
            evidence = {
                "semanticReviewStatus": "pending",
                "orderedFrameSha256": hashes,
                "diagnosticError": getattr(exc, "code", type(exc).__name__),
            }
        finally:
            for frame in frames:
                frame.unlink(missing_ok=True)
        self._repository.record_review(
            step_id=asset.step_id,
            asset_id=asset.id,
            source="ark_visual",
            decision=decision,
            reason="抽帧诊断仅提供证据，最终决定仍由人工作出",
            warnings=[],
            evidence=evidence,
        )


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
