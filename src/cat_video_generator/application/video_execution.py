"""Seedance任务、下载与视频技术QC用例。

本模块只拥有视频收费任务的幂等、轮询和落盘生命周期。参考图选择与关键帧审核
由 ``VisualPreparationService`` 完成，Run/Episode顺序由 ``ProductionService`` 编排。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from ..domain.contracts import (
    GenerationStrategy,
    SegmentPlan,
    VideoInputMode,
)
from ..domain.media import MediaSource, build_video_input_plan
from ..domain.prompts import (
    CompiledPrompt,
    compile_segment_video_prompt,
    compile_video_prompt,
)
from ..domain.visual_profiles import StyleProfile
from ..domain.workflow import EpisodeStatus, StepKind, StepStatus
from .errors import StepRetryRequired
from .multi_clip_finalization import MultiClipFinalization
from .ports import (
    AssetStore,
    GatewayError,
    MediaFinalizer,
    MediaGenerationGateway,
    MediaProbe,
    StoredAsset,
    StoredEpisode,
    StoredStep,
    WorkflowRepository,
)
from .video_diagnostic import VideoDiagnosticService
from .video_landing import VideoAssetLandingService, segment_semantic_key


class VideoExecutionService:
    """执行一次完整Seedance成片并将原始MP4保存为候选资产。"""

    def __init__(
        self,
        *,
        repository: WorkflowRepository,
        media_gateway: MediaGenerationGateway,
        asset_store: AssetStore,
        media_probe: MediaProbe,
        provider_name: str,
        resolution: str,
        style_profile: StyleProfile,
        media_finalizer: MediaFinalizer | None = None,
        video_diagnostic: VideoDiagnosticService | None = None,
        poll_interval_seconds: float = 10,
        task_timeout_seconds: float = 1800,
    ) -> None:
        self._repository = repository
        self._gateway = media_gateway
        self._provider_name = provider_name
        self._resolution = resolution
        self._multi_clip_finalization = (
            None
            if media_finalizer is None
            else MultiClipFinalization(
                repository=repository,
                asset_store=asset_store,
                media_probe=media_probe,
                media_finalizer=media_finalizer,
                resolution=resolution,
                video_diagnostic=video_diagnostic,
            )
        )
        self._landing = VideoAssetLandingService(
            repository=repository,
            asset_store=asset_store,
            media_probe=media_probe,
            resolution=resolution,
            video_diagnostic=video_diagnostic,
        )
        self._poll_interval = poll_interval_seconds
        self._task_timeout = task_timeout_seconds
        self._style_profile = style_profile

    def execute(
        self,
        episode: StoredEpisode,
        inputs: tuple[StoredAsset, ...],
        *,
        allow_multi_clip: bool,
        prompt_override: str | None = None,
    ) -> dict[str, Any]:
        """执行本集配置的策略；multi_clip必须由调用者再次显式授权。"""

        if episode.plan.generation_strategy is GenerationStrategy.MULTI_CLIP:
            if not allow_multi_clip:
                raise ValueError("multi_clip必须显式提供--allow-multi-clip")
            if prompt_override is not None and prompt_override.strip():
                raise ValueError("multi_clip分段Prompt由系统逐段编译，不支持整体覆盖")
            return self._generate_multi_clip(episode, inputs)
        return self._generate_single_pass(
            episode,
            inputs,
            prompt_override=prompt_override,
        )

    def resume_step(
        self,
        episode: StoredEpisode,
        step: StoredStep,
    ) -> dict[str, Any]:
        """只轮询已有task ID，绝不重新提交收费POST。"""

        return self._finish_video_step(episode, step)

    def _generate_single_pass(
        self,
        episode: StoredEpisode,
        inputs: tuple[StoredAsset, ...],
        *,
        prompt_override: str | None = None,
        attempt: int = 1,
        retry_of_step_id: str | None = None,
        retry_reason: str | None = None,
    ) -> dict[str, Any]:
        input_plan = build_video_input_plan(
            episode.plan,
            model=self._gateway.video_model,
            resolution=self._resolution,
            sources=tuple(
                MediaSource(
                    asset_id=asset.id,
                    role=asset.role,
                    media_type=asset.media_type,
                    sha256=asset.sha256,
                    metadata=asset.metadata,
                    semantic_key=asset.semantic_key,
                )
                for asset in inputs
            ),
        )
        assets_by_id = {asset.id: asset for asset in inputs}
        ordered_inputs = tuple(assets_by_id[binding.asset_id] for binding in input_plan.bindings)
        compiled = compile_video_prompt(
            episode.plan,
            input_plan=input_plan,
            style_profile=self._style_profile,
        )
        if prompt_override is not None and prompt_override.strip():
            override_text = prompt_override.strip()
            compiled = CompiledPrompt(
                text=override_text,
                char_count=len(override_text),
                utf8_bytes=len(override_text.encode("utf-8")),
                warnings=(),
            )
        input_hash = _input_hash(
            compiled.text,
            input_plan.model_dump_json(),
            *(asset.sha256 for asset in ordered_inputs),
        )
        operation_key = "video:single_pass"
        step = self._repository.create_step_intent(
            run_id=episode.run_id,
            episode_id=episode.id,
            parent_step_id=None,
            kind=StepKind.VIDEO,
            attempt=attempt,
            provider=self._provider_name,
            model=self._gateway.video_model,
            input_hash=input_hash,
            request_summary={
                "operationKey": operation_key,
                "generationStrategy": "single_pass",
                "videoInputPlan": input_plan.model_dump(mode="json"),
                "inputAssetIds": [str(asset.id) for asset in ordered_inputs],
                "promptAliases": {
                    binding.prompt_alias: {
                        "assetId": str(binding.asset_id),
                        "semanticKey": binding.semantic_key,
                    }
                    for binding in input_plan.bindings
                },
                "retryOfStepId": retry_of_step_id,
                "retryReason": retry_reason,
            },
        )
        self._repository.save_prompt(
            step_id=step.id,
            parent_prompt_id=None,
            purpose="video",
            model=self._gateway.video_model,
            text=compiled.text,
        )
        if step.status in {StepStatus.QUEUED, StepStatus.RUNNING}:
            return self._finish_video_step(episode, step)
        if step.status is StepStatus.SUBMISSION_UNKNOWN:
            raise RuntimeError("视频提交结果未知，必须先人工对账")
        if step.status is StepStatus.SUCCEEDED:
            return _episode_result(episode, "视频步骤已经成功")
        if step.status in {
            StepStatus.FAILED,
            StepStatus.EXPIRED,
            StepStatus.CANCELLED,
        }:
            raise StepRetryRequired(step.id, operation_key)

        self._repository.set_episode_status(
            episode.id,
            EpisodeStatus.VIDEO_GENERATING,
        )
        # 先提交收费意图，再调用Ark；进程即使在HTTP响应前中断，幂等记录也会
        # 阻止同一输入被盲目再次POST。
        self._repository.set_step_status(step.id, StepStatus.SUBMITTING)
        try:
            task = self._gateway.submit_video(
                prompt=compiled.text,
                input_plan=input_plan,
                input_paths=tuple(asset.path for asset in ordered_inputs),
            )
        except GatewayError as exc:
            self._repository.fail_step(
                step.id,
                code=exc.code,
                message=str(exc),
                submission_unknown=exc.submission_unknown,
            )
            self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            raise
        self._repository.set_step_status(
            step.id,
            StepStatus.QUEUED,
            provider_task_id=task.task_id,
        )
        return self._finish_video_step(episode, self._repository.get_step(step.id))

    def _generate_multi_clip(
        self,
        episode: StoredEpisode,
        inputs: tuple[StoredAsset, ...],
    ) -> dict[str, Any]:
        """按人工批准边界逐段生成，最后才执行条件式 FFmpeg。

        multi_clip 不是自动降级：同一 Episode 必须先存在被人工拒绝的单次成片，
        并在新导演方案中明确声明两个天然硬切片段。每段都要人工批准后才会继续。
        """

        if self._multi_clip_finalization is None:
            raise ValueError("multi_clip需要可用的FFmpeg，且必须在首次片段收费前配置")
        rejected = self._repository.list_assets(
            episode_id=episode.id,
            roles=("video",),
            statuses=("rejected",),
        )
        if not rejected:
            raise ValueError("multi_clip只允许在同一Episode已有人工拒绝的single_pass后显式使用")

        ready_parts: list[StoredAsset] = []
        for segment in episode.plan.segments:
            semantic_key = segment_semantic_key(episode, segment)
            ready = self._repository.list_assets(
                episode_id=episode.id,
                statuses=("ready",),
                semantic_keys=(semantic_key,),
            )
            if ready:
                ready_parts.append(ready[0])
                continue
            awaiting = self._repository.list_assets(
                episode_id=episode.id,
                statuses=("candidate", "approved"),
                semantic_keys=(semantic_key,),
            )
            if awaiting:
                return {
                    "episodeId": str(episode.id),
                    "slot": episode.plan.slot.value,
                    "status": "segment_review",
                    "segmentOrder": segment.order,
                    "assetId": str(awaiting[0].id),
                    "localPath": str(awaiting[0].path),
                }
            segment_inputs = inputs
            segment_mode = VideoInputMode.MULTIMODAL_REFERENCE
            if segment.requires_tail_link:
                if segment.order != 2 or not ready_parts:
                    raise ValueError("连续空间片段只能把已批准的第一段尾帧用于第二段")
                segment_inputs = (
                    self._multi_clip_finalization.ensure_tail_frame(
                        episode,
                        ready_parts[0],
                    ),
                )
                segment_mode = VideoInputMode.STRICT_FIRST_FRAME
            return self._generate_segment(
                episode,
                segment,
                segment_inputs,
                input_mode=segment_mode,
            )
        return self._multi_clip_finalization.finalize(
            episode,
            (ready_parts[0], ready_parts[1]),
        )

    def _generate_segment(
        self,
        episode: StoredEpisode,
        segment: SegmentPlan,
        inputs: tuple[StoredAsset, ...],
        *,
        input_mode: VideoInputMode,
        attempt: int = 1,
        retry_of_step_id: str | None = None,
        retry_reason: str | None = None,
    ) -> dict[str, Any]:
        plan_episode = episode.plan.model_copy(
            update={
                "video_input_mode": input_mode,
                "generation_strategy": GenerationStrategy.SINGLE_PASS,
                "segments": [],
            }
        )
        input_plan = build_video_input_plan(
            plan_episode,
            model=self._gateway.video_model,
            resolution=self._resolution,
            duration_seconds=segment.duration_seconds,
            sources=tuple(
                MediaSource(
                    asset_id=asset.id,
                    role=asset.role,
                    media_type=asset.media_type,
                    sha256=asset.sha256,
                    metadata=asset.metadata,
                    semantic_key=asset.semantic_key,
                )
                for asset in inputs
            ),
        )
        assets_by_id = {asset.id: asset for asset in inputs}
        ordered_inputs = tuple(assets_by_id[binding.asset_id] for binding in input_plan.bindings)
        compiled = compile_segment_video_prompt(
            episode.plan,
            segment,
            input_plan=input_plan,
            style_profile=self._style_profile,
        )
        input_hash = _input_hash(
            "multi_clip",
            str(segment.order),
            compiled.text,
            input_plan.model_dump_json(),
            *(asset.sha256 for asset in ordered_inputs),
        )
        operation_key = f"video:segment:{segment.order}"
        step = self._repository.create_step_intent(
            run_id=episode.run_id,
            episode_id=episode.id,
            parent_step_id=None,
            kind=StepKind.VIDEO,
            attempt=attempt,
            provider=self._provider_name,
            model=self._gateway.video_model,
            input_hash=input_hash,
            request_summary={
                "operationKey": operation_key,
                "generationStrategy": "multi_clip",
                "segmentOrder": segment.order,
                "videoInputPlan": input_plan.model_dump(mode="json"),
                "inputAssetIds": [str(asset.id) for asset in ordered_inputs],
                "promptAliases": {
                    binding.prompt_alias: {
                        "assetId": str(binding.asset_id),
                        "semanticKey": binding.semantic_key,
                    }
                    for binding in input_plan.bindings
                },
                "retryOfStepId": retry_of_step_id,
                "retryReason": retry_reason,
            },
        )
        self._repository.save_prompt(
            step_id=step.id,
            parent_prompt_id=None,
            purpose=f"video_segment_{segment.order}",
            model=self._gateway.video_model,
            text=compiled.text,
        )
        if step.status in {StepStatus.QUEUED, StepStatus.RUNNING}:
            return self._finish_video_step(episode, step)
        if step.status is StepStatus.SUBMISSION_UNKNOWN:
            raise RuntimeError("片段提交结果未知，必须先人工对账")
        if step.status is StepStatus.AWAITING_REVIEW:
            return _episode_result(episode, f"片段{segment.order}等待人工审核")
        if step.status is StepStatus.SUCCEEDED:
            return _episode_result(episode, f"片段{segment.order}已经批准")
        if step.status is not StepStatus.PENDING:
            if step.status in {
                StepStatus.FAILED,
                StepStatus.EXPIRED,
                StepStatus.CANCELLED,
            }:
                raise StepRetryRequired(step.id, operation_key)
            raise ValueError(f"片段{segment.order}步骤状态{step.status.value}不可重复提交")

        if episode.status is EpisodeStatus.VIDEO_PENDING:
            self._repository.set_episode_status(
                episode.id,
                EpisodeStatus.VIDEO_GENERATING,
            )
        # 片段与完整视频使用同一收费幂等原则：先提交意图，再发送供应商 POST。
        self._repository.set_step_status(step.id, StepStatus.SUBMITTING)
        try:
            task = self._gateway.submit_video(
                prompt=compiled.text,
                input_plan=input_plan,
                input_paths=tuple(asset.path for asset in ordered_inputs),
            )
        except GatewayError as exc:
            self._repository.fail_step(
                step.id,
                code=exc.code,
                message=str(exc),
                submission_unknown=exc.submission_unknown,
            )
            self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            raise
        self._repository.set_step_status(
            step.id,
            StepStatus.QUEUED,
            provider_task_id=task.task_id,
        )
        return self._finish_video_step(episode, self._repository.get_step(step.id))

    def _finish_video_step(
        self,
        episode: StoredEpisode,
        step: StoredStep,
    ) -> dict[str, Any]:
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
                    self._repository.set_step_status(step.id, StepStatus.RUNNING)
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
            if step.request_summary.get("generationStrategy") == "multi_clip":
                segment_order = int(step.request_summary["segmentOrder"])
                segment = next(
                    item for item in episode.plan.segments if item.order == segment_order
                )
                return self._landing.land_segment(
                    episode,
                    step,
                    segment,
                    task.video_url,
                )
            return self._landing.land_video(episode, step, task.video_url)
        raise TimeoutError("等待Seedance任务超过配置时限，可稍后使用resume恢复")

    def retry_video(
        self,
        episode: StoredEpisode,
        original_step: StoredStep,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """按原输入和Prompt语义显式创建收费视频新attempt。"""

        operation_key = str(original_step.request_summary.get("operationKey", ""))
        input_ids = original_step.request_summary.get("inputAssetIds", [])
        inputs = tuple(
            self._repository.asset_detail(uuid.UUID(str(value))) for value in input_ids
        )
        attempt = self._repository.next_step_attempt(
            episode_id=episode.id,
            kind=StepKind.VIDEO,
            operation_key=operation_key,
        )
        if episode.status is EpisodeStatus.FAILED:
            self._repository.set_episode_status(
                episode.id,
                EpisodeStatus.VIDEO_PENDING,
            )
            episode = self._repository.get_episode(
                episode.run_id,
                episode.plan.slot,
            )
        if operation_key == "video:single_pass":
            return self._generate_single_pass(
                episode,
                inputs,
                attempt=attempt,
                retry_of_step_id=str(original_step.id),
                retry_reason=reason,
            )
        if operation_key.startswith("video:segment:"):
            segment_order = int(operation_key.rsplit(":", 1)[1])
            segment = next(
                item for item in episode.plan.segments if item.order == segment_order
            )
            input_plan = original_step.request_summary.get("videoInputPlan", {})
            return self._generate_segment(
                episode,
                segment,
                inputs,
                input_mode=VideoInputMode(input_plan["input_mode"]),
                attempt=attempt,
                retry_of_step_id=str(original_step.id),
                retry_reason=reason,
            )
        raise ValueError(f"不支持重试视频操作{operation_key!r}")

    def retry_finalize(
        self,
        episode: StoredEpisode,
        original_step: StoredStep,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """显式重做multi_clip本地后期，不产生供应商请求。"""

        if self._multi_clip_finalization is None:
            raise ValueError("当前环境没有可用FFmpeg后期能力")
        return self._multi_clip_finalization.retry_finalize(
            episode,
            original_step,
            reason=reason,
        )

def _input_hash(*values: str) -> str:
    return hashlib.sha256(
        json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _episode_result(episode: StoredEpisode, message: str) -> dict[str, Any]:
    return {
        "episodeId": str(episode.id),
        "slot": episode.plan.slot.value,
        "status": episode.status.value,
        "message": message,
    }
