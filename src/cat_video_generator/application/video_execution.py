"""Seedance 初始成片、官方视频延展、恢复、对账与技术 QC。

本服务拥有一个 Episode 的全部视频任务生命周期。它只消费已批准的开场锚点，
不生成视觉素材、不修改剧情。供应商延展返回新增尾段，因此这里只在各区段QC通过后
执行无重编码顺序封装；不承担逐镜或创作型多片段编排。已有 task ID 时只能继续查询；
创建响应不确定时必须先对账，避免重复付费。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..domain.prompts import compile_video_diagnostic_prompt, compile_video_prompt
from ..domain.rendering import (
    MediaSource,
    RenderOperation,
    RenderPlan,
    RenderSection,
    build_render_plan,
    build_video_input_plan,
    supports_video_extension,
)
from ..domain.snapshots import VideoInputSnapshot
from ..domain.visual_profiles import SeriesVisualProfile, StyleProfile
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
    """将批准的开场锚点推进为单次视频或官方延展视频。"""

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
        api_timeout_seconds: float = 120,
        poll_interval_seconds: float = 10,
        task_timeout_seconds: float = 1800,
    ) -> None:
        if diagnostic_mode not in {"off", "diagnostic"}:
            raise ValueError("视频语义诊断模式必须是 off 或 diagnostic")
        if diagnostic_mode == "diagnostic" and (review_gateway is None or frame_extractor is None):
            raise ValueError("diagnostic 模式需要审核网关和抽帧能力")
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
        self._api_timeout = api_timeout_seconds
        self._poll_interval = poll_interval_seconds
        self._task_timeout = task_timeout_seconds

    def execute(
        self,
        episode: StoredEpisode,
        opening_anchor: StoredAsset,
        *,
        prompt_override: str | None = None,
    ) -> dict[str, Any]:
        """从开场锚点开始执行完整 RenderPlan。

        中长视频的后续区段只引用上一版完整视频。任何已存在的收费 Step 都会按
        状态复用、查询或要求显式重试，不会由 run-day 隐式创建第二次 attempt。
        """

        if opening_anchor.role != "opening_anchor" or opening_anchor.status not in {
            "approved",
            "ready",
        }:
            raise ValueError("视频生成只能使用已批准的开场锚点")
        render_plan = build_render_plan(episode.plan)
        self._require_extension_capability(render_plan)
        return self._execute_sections(
            episode,
            render_plan,
            source=opening_anchor,
            start_order=1,
            prompt_override=prompt_override,
        )

    def resume_step(self, episode: StoredEpisode, step: StoredStep) -> dict[str, Any]:
        """继续查询已有视频 task ID，并在成功后推进剩余延展区段。"""

        local_recovery = (
            step.kind is StepKind.VIDEO
            and step.status is StepStatus.FAILED
            and step.provider_task_id is not None
            and step.error_code == "media_qc_failed"
            and step.input_snapshot.get("provider_task_status") == "succeeded"
        )
        if step.kind is not StepKind.VIDEO or (
            step.status not in {StepStatus.QUEUED, StepStatus.RUNNING} and not local_recovery
        ):
            raise ValueError("continue-query 只接受已有 task ID 的 queued/running 视频步骤")
        if not step.provider_task_id:
            raise ValueError("视频步骤缺少 provider task ID")
        if local_recovery:
            self._ensure_episode_generating(episode)
            self._repository.reopen_video_step_for_local_recovery(step.id)
            step = self._repository.get_step(step.id)
        result = self._finish_section(episode, step)
        if result["status"] != "succeeded":
            return result
        landed = self._repository.asset_detail(uuid.UUID(result["assetId"]))
        plan = build_render_plan(episode.plan)
        current_order = VideoInputSnapshot.model_validate(step.input_snapshot).render_section_order
        if current_order >= len(plan.sections):
            return result
        return self._execute_sections(
            episode,
            plan,
            source=landed,
            start_order=current_order + 1,
            prompt_override=None,
        )

    def retry_video(
        self,
        episode: StoredEpisode,
        step: StoredStep,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """为终止的视频节点显式创建 attempt+1，并保留原任务证据。"""

        if step.kind is not StepKind.VIDEO or step.status not in {
            StepStatus.FAILED,
            StepStatus.EXPIRED,
            StepStatus.CANCELLED,
        }:
            raise ValueError("只允许重试 failed、expired 或 cancelled 视频步骤")
        snapshot = VideoInputSnapshot.model_validate(step.input_snapshot)
        if len(snapshot.input_asset_ids) != 1:
            raise ValueError("视频步骤必须只有一个开场锚点或上一版视频输入")
        source = self._repository.asset_detail(snapshot.input_asset_ids[0])
        render_plan = build_render_plan(episode.plan)
        self._require_extension_capability(render_plan)
        section = _section(render_plan, snapshot.render_section_order)
        result = self._run_section(
            episode,
            render_plan,
            section,
            source,
            attempt=self._repository.next_step_attempt(
                episode_id=episode.id,
                kind=StepKind.VIDEO,
                operation_key=step.operation_key,
            ),
            retry_of_step_id=step.id,
            retry_reason=reason,
        )
        if result["status"] != "succeeded":
            return result
        landed = self._repository.asset_detail(uuid.UUID(result["assetId"]))
        if section.order >= len(render_plan.sections):
            return result
        return self._execute_sections(
            episode,
            render_plan,
            source=landed,
            start_order=section.order + 1,
            prompt_override=None,
        )

    def reconciliation_candidates(self, step: StoredStep) -> tuple[dict[str, Any], ...]:
        """筛选创建响应丢失时可能对应的 Ark 视频任务，不自动绑定。"""

        if step.kind is not StepKind.VIDEO or step.status is not StepStatus.SUBMISSION_UNKNOWN:
            raise ValueError("只有 submission_unknown 视频步骤可以查询对账候选")
        if step.provider_task_id:
            raise ValueError("已有 task ID 的步骤应直接继续查询")
        snapshot = VideoInputSnapshot.model_validate(step.input_snapshot)
        reference_time = step.submitted_at or step.created_at
        lower = (
            None
            if reference_time is None
            else reference_time.astimezone(timezone.utc) - timedelta(minutes=5)
        )
        upper = (
            None
            if reference_time is None
            else reference_time.astimezone(timezone.utc) + timedelta(minutes=30)
        )
        candidates: list[dict[str, Any]] = []
        model = step.model or self._gateway.video_model
        for task in self._gateway.list_video_tasks(model=model):
            if not _matches_task(task, snapshot, model=model, lower=lower, upper=upper):
                continue
            owner = self._repository.find_step_by_provider_task_id(task.task_id)
            if owner is not None and owner.id != step.id:
                continue
            candidates.append(_task_dict(task))
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
        """人工确认 Ark task 后绑定原 Step 并继续查询。"""

        candidates = self.reconciliation_candidates(step)
        if provider_task_id not in {str(item["taskId"]) for item in candidates}:
            raise ValueError("选择的 task ID 不在当前对账候选中")
        owner = self._repository.find_step_by_provider_task_id(provider_task_id)
        if owner is not None and owner.id != step.id:
            raise ValueError("该 Ark task ID 已被其他步骤绑定")
        now = datetime.now(timezone.utc)
        self._repository.set_step_status(
            step.id,
            StepStatus.QUEUED,
            provider_task_id=provider_task_id,
            input_snapshot_patch={
                "reconciled_provider_task_id": provider_task_id,
                "reconciled_at": now,
                "provider_task_status": "reconciled",
            },
        )
        return self.resume_step(episode, self._repository.get_step(step.id))

    def _execute_sections(
        self,
        episode: StoredEpisode,
        render_plan: RenderPlan,
        *,
        source: StoredAsset,
        start_order: int,
        prompt_override: str | None,
    ) -> dict[str, Any]:
        current_source = source
        last_result: dict[str, Any] | None = None
        for section in render_plan.sections:
            if section.order < start_order:
                continue
            last_result = self._run_section(
                episode,
                render_plan,
                section,
                current_source,
                attempt=1,
                prompt_override=prompt_override if section.order == 1 else None,
            )
            if last_result["status"] != "succeeded":
                return last_result
            current_source = self._repository.asset_detail(uuid.UUID(last_result["assetId"]))
        if last_result is None:
            raise ValueError("RenderPlan 没有待执行区段")
        return last_result

    def _run_section(
        self,
        episode: StoredEpisode,
        render_plan: RenderPlan,
        section: RenderSection,
        source: StoredAsset,
        *,
        attempt: int,
        prompt_override: str | None = None,
        retry_of_step_id: uuid.UUID | None = None,
        retry_reason: str | None = None,
    ) -> dict[str, Any]:
        media_source = MediaSource(
            asset_id=source.id,
            semantic_key=source.semantic_key or source.role,
            media_type="image" if section.order == 1 else "video",
            sha256=source.sha256,
            metadata=source.metadata,
        )
        input_plan = build_video_input_plan(
            operation=(RenderOperation.INITIAL if section.order == 1 else RenderOperation.EXTEND),
            resolution=self._resolution,
            duration_seconds=section.duration_seconds,
            source=media_source,
        )
        compiled = compile_video_prompt(
            episode.plan,
            input_plan=input_plan,
            section=section,
            series_profile=self._series_profile,
            style_profile=self._style_profile,
        )
        prompt = (
            prompt_override.strip()
            if prompt_override and prompt_override.strip()
            else compiled.text
        )
        prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        operation_key = (
            "video:single_pass" if section.order == 1 else f"video:extend:{section.order}"
        )
        snapshot = VideoInputSnapshot(
            prompt_sha256=prompt_sha,
            input_plan=input_plan,
            input_asset_ids=(source.id,),
            render_section_order=section.order,
            retry_of_step_id=retry_of_step_id,
            retry_reason=retry_reason,
            api_request_timeout_seconds=self._api_timeout,
            task_timeout_seconds=self._task_timeout,
            poll_interval_seconds=self._poll_interval,
        )
        input_hash = _input_hash(prompt, input_plan.model_dump(mode="json"), source.sha256)
        step, _ = self._repository.create_step_with_prompt_intent(
            run_id=episode.run_id,
            episode_id=episode.id,
            # 初始段关联开场锚点，延展段关联上一段视频。父子链既用于 Web
            # 展示，也让恢复流程能够确认当前段唯一允许继承的媒体来源。
            parent_step_id=source.step_id,
            kind=StepKind.VIDEO,
            operation_key=operation_key,
            attempt=attempt,
            provider=self._provider_name,
            model=self._gateway.video_model,
            input_hash=input_hash,
            input_snapshot=snapshot.model_dump(mode="json"),
            prompt_purpose=PromptPurpose.VIDEO,
            prompt_model=self._gateway.video_model,
            prompt_text=prompt,
            parent_prompt_id=None,
        )
        if step.status in {StepStatus.QUEUED, StepStatus.RUNNING}:
            return self._finish_section(episode, step)
        if step.status is StepStatus.SUBMISSION_UNKNOWN:
            raise RuntimeError("视频创建响应未知，必须先对账，禁止重复提交")
        if step.status in {StepStatus.FAILED, StepStatus.EXPIRED, StepStatus.CANCELLED}:
            raise StepRetryRequired(step.id, step.operation_key)
        if step.status in {StepStatus.SUCCEEDED, StepStatus.AWAITING_REVIEW}:
            asset = self._asset_for_step(episode, step.id)
            return {"status": "succeeded", "stepId": str(step.id), "assetId": str(asset.id)}
        self._ensure_episode_generating(episode)
        self._repository.set_step_status(step.id, StepStatus.SUBMITTING)
        try:
            if section.order == 1:
                task = self._gateway.submit_video(
                    prompt=prompt,
                    input_plan=input_plan,
                    input_paths=(source.path,),
                )
            else:
                # Ark延展不接受本地MP4或Base64。通过上一段已持久化的task ID
                # 查询一个临时下载URL并直接传给reference_video；签名URL不会进入
                # Step快照、日志或数据库，恢复时可再次安全查询。
                source_task_id = str(source.metadata.get("providerTaskId") or "")
                if not source_task_id:
                    raise GatewayError(
                        "延展源视频缺少Ark task ID",
                        code="missing_extension_source_task_id",
                        retryable=False,
                    )
                source_task = self._gateway.get_video_task(source_task_id)
                if source_task.status != "succeeded" or not source_task.video_url:
                    raise GatewayError(
                        "延展源视频尚无可用供应商URL",
                        code="extension_source_url_unavailable",
                        retryable=True,
                    )
                task = self._gateway.submit_video(
                    prompt=prompt,
                    input_plan=input_plan,
                    input_urls=(source_task.video_url,),
                )
        except GatewayError as exc:
            self._repository.fail_step(
                step.id,
                code=exc.code,
                message=str(exc),
                submission_unknown=exc.submission_unknown,
                request_id=exc.request_id,
                input_snapshot_patch={
                    "provider_task_status": "submission_unknown"
                    if exc.submission_unknown
                    else "failed"
                },
            )
            raise
        if not task.task_id:
            self._repository.fail_step(
                step.id,
                code="missing_task_id",
                message="Ark 创建视频任务未返回 task ID",
                submission_unknown=True,
            )
            raise RuntimeError("Ark 创建视频任务未返回 task ID，步骤已冻结等待对账")
        self._repository.set_step_status(
            step.id,
            StepStatus.QUEUED,
            provider_task_id=task.task_id,
            input_snapshot_patch={"provider_task_status": task.status},
        )
        return self._finish_section(episode, self._repository.get_step(step.id), initial_task=task)

    def _finish_section(
        self,
        episode: StoredEpisode,
        step: StoredStep,
        *,
        initial_task: VideoTaskResult | None = None,
    ) -> dict[str, Any]:
        if not step.provider_task_id:
            raise ValueError("视频步骤缺少 provider task ID")
        deadline = time.monotonic() + self._task_timeout
        task = initial_task
        while time.monotonic() < deadline:
            if task is None:
                task = self._gateway.get_video_task(step.provider_task_id)
            if task.status in {"queued", "running"}:
                current = self._repository.get_step(step.id)
                if task.status == "running" and current.status is StepStatus.QUEUED:
                    self._repository.set_step_status(
                        step.id,
                        StepStatus.RUNNING,
                        input_snapshot_patch={"provider_task_status": task.status},
                    )
                time.sleep(self._poll_interval)
                task = None
                continue
            if task.status != "succeeded" or not task.video_url:
                self._repository.fail_step(
                    step.id,
                    code=task.error_code or task.status,
                    message=task.error_message or "Ark 视频任务失败",
                    input_snapshot_patch={"provider_task_status": task.status},
                )
                raise RuntimeError(task.error_message or f"Ark 视频任务状态为 {task.status}")
            return self._land_section(episode, step, task.video_url)
        ended = datetime.now(timezone.utc)
        self._repository.patch_step_snapshot(
            step.id,
            {
                "provider_task_status": "polling_window_elapsed",
                "polling_window_ended_at": ended,
            },
        )
        return {
            "status": "provider_running",
            "stepId": str(step.id),
            "taskId": step.provider_task_id,
            "message": "本地监看窗口结束，供应商任务仍可继续查询",
        }

    def _land_section(
        self,
        episode: StoredEpisode,
        step: StoredStep,
        video_url: str,
    ) -> dict[str, Any]:
        snapshot = VideoInputSnapshot.model_validate(step.input_snapshot)
        render_plan = build_render_plan(episode.plan)
        section = _section(render_plan, snapshot.render_section_order)
        landed = self._asset_store.download(video_url, suffix=".mp4")
        segment_qc = self._probe.inspect_video(
            landed.path,
            expected_duration_seconds=section.duration_seconds,
            expected_resolution=snapshot.input_plan.resolution,
            minimum_duration_seconds=max(8, section.duration_seconds - 1),
            maximum_duration_seconds=min(15, section.duration_seconds + 1),
            duration_tolerance_ms=1500,
        )
        if not segment_qc.get("passed"):
            self._repository.fail_step(
                step.id,
                code="media_qc_failed",
                message=json.dumps(segment_qc, ensure_ascii=False),
                input_snapshot_patch={
                    "provider_task_status": "succeeded",
                    "media_qc": segment_qc,
                },
            )
            self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            raise RuntimeError(f"视频区段技术QC失败: {segment_qc.get('failures', [])}")

        if section.order == 1:
            component_paths = (landed.path,)
            component_sha256 = (landed.sha256,)
        else:
            source = self._repository.asset_detail(snapshot.input_asset_ids[0])
            stored_paths = source.metadata.get("componentPaths")
            stored_hashes = source.metadata.get("componentSha256")
            previous_paths = (
                tuple(Path(str(item)) for item in stored_paths)
                if isinstance(stored_paths, list) and stored_paths
                else (source.path,)
            )
            previous_hashes = (
                tuple(str(item) for item in stored_hashes)
                if isinstance(stored_hashes, list) and stored_hashes
                else (source.sha256,)
            )
            component_paths = (*previous_paths, landed.path)
            component_sha256 = (*previous_hashes, landed.sha256)

        expected_total = sum(
            item.duration_seconds for item in render_plan.sections[: section.order]
        )
        final = section.order == len(render_plan.sections)
        final_landed = (
            self._asset_store.concatenate_videos(component_paths)
            if final and section.order > 1
            else landed
        )
        qc = (
            self._probe.inspect_video(
                final_landed.path,
                expected_duration_seconds=expected_total,
                expected_resolution=snapshot.input_plan.resolution,
                minimum_duration_seconds=max(8, expected_total - 1),
                maximum_duration_seconds=min(45, expected_total + 1),
                duration_tolerance_ms=1500,
            )
            if final and section.order > 1
            else segment_qc
        )
        if not qc.get("passed"):
            self._repository.fail_step(
                step.id,
                code="media_qc_failed",
                message=json.dumps(qc, ensure_ascii=False),
                input_snapshot_patch={"provider_task_status": "succeeded", "media_qc": qc},
            )
            self._repository.set_episode_status(episode.id, EpisodeStatus.FAILED)
            raise RuntimeError(f"视频累计成片技术QC失败: {qc.get('failures', [])}")
        asset = self._repository.save_asset(
            run_id=episode.run_id,
            episode_id=episode.id,
            step_id=step.id,
            role="video" if final else "video_intermediate",
            semantic_key=f"video:{episode.plan.slot.value}:section-{section.order}",
            scope="episode",
            status="candidate" if final else "ready",
            media_type="video",
            landed=final_landed,
            metadata={
                "qc": qc,
                "segmentQc": segment_qc,
                "renderSectionOrder": section.order,
                "renderSectionCount": len(render_plan.sections),
                "totalDurationSeconds": expected_total,
                "providerTaskId": step.provider_task_id,
                "componentPaths": [str(item) for item in component_paths],
                "componentSha256": list(component_sha256),
                "assemblyMode": (
                    "ffmpeg_concat_stream_copy" if final and section.order > 1 else "none"
                ),
            },
        )
        if not final:
            self._repository.set_step_status(
                step.id,
                StepStatus.SUCCEEDED,
                input_snapshot_patch={"provider_task_status": "succeeded", "media_qc": qc},
            )
            return {"status": "succeeded", "stepId": str(step.id), "assetId": str(asset.id)}
        self._repository.set_episode_status(episode.id, EpisodeStatus.MEDIA_QC)
        self._repository.set_step_status(
            step.id,
            StepStatus.AWAITING_REVIEW,
            input_snapshot_patch={"provider_task_status": "succeeded", "media_qc": qc},
        )
        self._repository.set_episode_status(episode.id, EpisodeStatus.CONTENT_REVIEW)
        self._run_diagnostic(episode, asset, step)
        return {"status": "succeeded", "stepId": str(step.id), "assetId": str(asset.id)}

    def _run_diagnostic(self, episode: StoredEpisode, asset: StoredAsset, step: StoredStep) -> None:
        if self._diagnostic_mode != "diagnostic":
            return
        assert self._review_gateway is not None and self._frame_extractor is not None
        prompt = compile_video_diagnostic_prompt(
            episode.plan,
            series_profile=self._series_profile,
            style_profile=self._style_profile,
        )
        generation = self._repository.get_prompt_for_step(step.id, purpose=PromptPurpose.VIDEO)
        self._repository.save_prompt(
            step_id=step.id,
            parent_prompt_id=generation.id,
            purpose=PromptPurpose.REVIEW,
            model=self._review_gateway.review_model,
            text=prompt,
        )
        frames: tuple[Any, ...] = ()
        try:
            frames = self._frame_extractor.extract_review_frames(asset, count=12)
            result = self._review_gateway.diagnose_video_frames(
                prompt=prompt,
                frame_paths=frames,
            )
        except Exception as exc:
            # 视频诊断只提供人工审核证据，不是媒体生产硬门。抽帧或Ark审核异常
            # 都不能把已经通过技术QC并落盘的收费成片错误标记为生成失败。
            self._repository.record_review(
                step_id=step.id,
                asset_id=asset.id,
                source="ark_visual",
                decision="pending",
                reason="视频语义诊断异常，保留成片并转人工审核",
                warnings=[],
                evidence={
                    "reviewMode": "diagnostic",
                    "diagnosticError": f"{type(exc).__name__}: {exc}",
                },
            )
            return
        finally:
            # 诊断帧不是业务资产。无论Ark审核成功或失败都立即删除，避免工作目录
            # 随着日更任务无限增长；正式视频及其哈希仍由资产表完整保留。
            for frame in frames:
                frame.unlink(missing_ok=True)
        self._repository.record_review(
            step_id=step.id,
            asset_id=asset.id,
            source="ark_visual",
            decision="pending",
            reason="视频语义诊断已完成，最终结论等待人工审核",
            warnings=[],
            evidence={
                "reviewMode": "diagnostic",
                "reviewerModel": result.model,
                "identityOk": result.identity_ok,
                "styleOk": result.style_ok,
                "criticalPropsOk": result.critical_props_ok,
                "narrativeOrderOk": result.narrative_order_ok,
                "confidence": result.confidence,
                "violations": result.violations,
                "evidence": result.evidence,
                "requestHash": result.request_hash,
                "responseId": result.response_id,
            },
        )

    def _asset_for_step(
        self,
        episode: StoredEpisode,
        step_id: uuid.UUID,
    ) -> StoredAsset:
        assets = self._repository.list_assets(
            run_id=episode.run_id,
            episode_id=episode.id,
            statuses=("candidate", "approved", "ready"),
        )
        matches = tuple(
            item for item in assets if item.step_id == step_id and item.media_type == "video"
        )
        if len(matches) != 1:
            raise ValueError(f"视频步骤 {step_id} 没有唯一落盘资产")
        return matches[0]

    def _ensure_episode_generating(self, episode: StoredEpisode) -> None:
        current = self._repository.get_episode(episode.run_id, episode.plan.slot)
        if current.status is EpisodeStatus.FAILED:
            self._repository.set_episode_status(current.id, EpisodeStatus.VIDEO_PENDING)
            current = self._repository.get_episode(episode.run_id, episode.plan.slot)
        if current.status is EpisodeStatus.PLANNED:
            self._repository.set_episode_status(current.id, EpisodeStatus.VIDEO_PENDING)
            current = self._repository.get_episode(episode.run_id, episode.plan.slot)
        if current.status is EpisodeStatus.PREPARING_VISUALS:
            self._repository.set_episode_status(current.id, EpisodeStatus.VIDEO_PENDING)
            current = self._repository.get_episode(episode.run_id, episode.plan.slot)
        if current.status is EpisodeStatus.VIDEO_PENDING:
            self._repository.set_episode_status(current.id, EpisodeStatus.VIDEO_GENERATING)

    def _require_extension_capability(self, plan: RenderPlan) -> None:
        if len(plan.sections) > 1 and not supports_video_extension(self._gateway.video_model):
            raise ValueError(
                f"模型 {self._gateway.video_model} 不支持官方视频延展，无法生成 "
                f"{plan.total_duration_seconds} 秒成片"
            )


def _section(plan: RenderPlan, order: int) -> RenderSection:
    try:
        return next(item for item in plan.sections if item.order == order)
    except StopIteration as exc:
        raise ValueError(f"RenderPlan 不存在区段 {order}") from exc


def _input_hash(prompt: str, input_plan: dict[str, Any], source_sha: str) -> str:
    payload = json.dumps(
        {"prompt": prompt, "inputPlan": input_plan, "sourceSha256": source_sha},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _matches_task(
    task: VideoTaskResult,
    snapshot: VideoInputSnapshot,
    *,
    model: str,
    lower: datetime | None,
    upper: datetime | None,
) -> bool:
    if task.model is not None and task.model != model:
        return False
    if task.created_at is not None:
        created = task.created_at.astimezone(timezone.utc)
        if lower is not None and created < lower:
            return False
        if upper is not None and created > upper:
            return False
    plan = snapshot.input_plan
    return all(
        (
            task.duration_seconds in {None, plan.duration_seconds},
            task.ratio in {None, "9:16"},
            task.resolution in {None, plan.resolution},
            task.generate_audio in {None, True},
        )
    )


def _task_dict(task: VideoTaskResult) -> dict[str, Any]:
    return {
        "taskId": task.task_id,
        "status": task.status,
        "model": task.model,
        "createdAt": task.created_at,
        "durationSeconds": task.duration_seconds,
        "ratio": task.ratio,
        "resolution": task.resolution,
        "generateAudio": task.generate_audio,
    }
