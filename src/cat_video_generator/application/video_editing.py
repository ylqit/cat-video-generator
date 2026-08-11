"""Seedance区间编辑与本地非破坏性时间轴重建。

本服务拥有一次区间编辑从选择、收费意图、Ark任务到新sequence revision的完整生命周期；
它不修改剧情脚本，也不会在新版本批准前替换Episode正式视频。
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from ..domain.prompts import compile_video_range_edit_prompt
from ..domain.rendering import (
    BoundaryMode,
    ClipOrigin,
    MediaSource,
    SequenceStatus,
    VideoSequenceClip,
    VideoSequencePlan,
    build_video_edit_input_plan,
)
from ..domain.snapshots import VideoInputSnapshot
from ..domain.workflow import PromptPurpose, RunStatus, StepKind, StepStatus
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
    StoredVideoSequence,
    VideoTaskResult,
)
from .video_execution import VideoExecutionService


class VideoEditingService:
    """单轨EDL的AI区间替换与版本选择。"""

    def __init__(
        self,
        *,
        repository: ProductionStore,
        media_gateway: MediaGenerationGateway,
        asset_store: AssetStore,
        media_probe: MediaProbe,
        frame_extractor: ReviewFrameExtractor,
        video_execution: VideoExecutionService,
        provider_name: str,
        resolution: str,
        poll_interval_seconds: float,
        task_timeout_seconds: float,
        api_timeout_seconds: float,
    ) -> None:
        self._repository = repository
        self._gateway = media_gateway
        self._asset_store = asset_store
        self._probe = media_probe
        self._frames = frame_extractor
        self._video_execution = video_execution
        self._provider_name = provider_name
        self._resolution = resolution
        self._poll_interval = poll_interval_seconds
        self._task_timeout = task_timeout_seconds
        self._api_timeout = api_timeout_seconds

    def range_edit(
        self,
        episode_id: uuid.UUID,
        sequence_id: uuid.UUID,
        *,
        start_ms: int,
        end_ms: int,
        boundary_mode: BoundaryMode,
        instruction: str,
        allow_paid_generation: bool,
        retry_of_step_id: uuid.UUID | None = None,
        retry_reason: str | None = None,
        operation_key_override: str | None = None,
        parent_prompt_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        if not allow_paid_generation:
            raise ValueError("视频区间编辑需要显式付费许可")
        if not 500 <= end_ms - start_ms <= 13_000:
            raise ValueError("区间编辑选区必须在0.5至13秒之间")
        episode = self._episode(episode_id)
        if self._repository.get_run(episode.run_id).status == RunStatus.DELIVERED.value:
            raise ValueError("已交付Run不可创建区间编辑版本；请新建Run保留交付不可变性")
        parent = self._repository.get_video_sequence(sequence_id)
        if parent.episode_id != episode.id or parent.rendered_asset_id is None:
            raise ValueError("时间轴版本不属于当前Episode或尚无可编辑成片")
        start_ms, end_ms = self._resolve_selection(
            parent,
            start_ms=start_ms,
            end_ms=end_ms,
            boundary_mode=boundary_mode,
        )
        clip = self._single_source_clip(parent.plan, start_ms, end_ms)
        source = self._repository.asset_detail(clip.source_asset_id)
        provider_task_id = str(source.metadata.get("providerTaskId") or "")
        if not provider_task_id:
            raise ValueError("选区来源不是可查询Ark URL的视频，不能执行AI区间编辑")
        provider_task = self._gateway.get_video_task(provider_task_id)
        if provider_task.status != "succeeded" or not provider_task.video_url:
            raise ValueError("选区来源Ark任务当前没有可用HTTPS视频URL")
        rendered = self._repository.asset_detail(parent.rendered_asset_id)
        boundary_assets = self._boundary_assets(episode, rendered, start_ms, end_ms)
        provider_offset_ms, provider_duration_ms = _provider_source_window(source)
        source_start_ms = (
            provider_offset_ms
            + clip.source_start_ms
            + start_ms
            - clip.timeline_start_ms
        )
        source_end_ms = source_start_ms + end_ms - start_ms
        if source_end_ms > provider_duration_ms:
            raise ValueError("选区映射超出来源Clip时长")
        edit_duration_seconds = min(15, max(4, math.ceil(provider_duration_ms / 1000)))
        input_plan = build_video_edit_input_plan(
            resolution=self._resolution,
            duration_seconds=edit_duration_seconds,
            source_video=_media_source(source, "video"),
            before_frame=_media_source(boundary_assets[0], "image"),
            after_frame=_media_source(boundary_assets[1], "image"),
        )
        prompt = compile_video_range_edit_prompt(
            episode.plan,
            instruction=instruction,
            duration_seconds=edit_duration_seconds,
            source_start_ms=source_start_ms,
            source_end_ms=source_end_ms,
            relevant_constraints=tuple(item.text for item in episode.plan.script.hard_constraints),
        ).text
        draft = self._repository.create_video_sequence(
            episode_id=episode.id,
            parent_sequence_id=parent.id,
            base_asset_id=rendered.id,
            status=SequenceStatus.DRAFT,
            plan=parent.plan,
        )
        prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        input_hash = hashlib.sha256(
            json.dumps(
                {
                    "prompt": prompt,
                    "inputPlan": input_plan.model_dump(mode="json"),
                    "selection": [start_ms, end_ms],
                    "source": [source_start_ms, source_end_ms],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        operation_key = operation_key_override or f"video:range_edit:{draft.id}"
        attempt = (
            1
            if operation_key_override is None
            else self._repository.next_step_attempt(
                episode_id=episode.id,
                kind=StepKind.VIDEO,
                operation_key=operation_key,
            )
        )
        snapshot = VideoInputSnapshot(
            prompt_sha256=prompt_sha,
            input_plan=input_plan,
            input_asset_ids=(source.id, boundary_assets[0].id, boundary_assets[1].id),
            render_section_order=0,
            sequence_id=draft.id,
            selection_start_ms=start_ms,
            selection_end_ms=end_ms,
            source_start_ms=source_start_ms,
            source_end_ms=source_end_ms,
            edit_instruction=instruction.strip(),
            retry_of_step_id=retry_of_step_id,
            retry_reason=retry_reason,
            api_request_timeout_seconds=self._api_timeout,
            task_timeout_seconds=self._task_timeout,
            poll_interval_seconds=self._poll_interval,
        )
        try:
            step, _ = self._repository.create_step_with_prompt_intent(
                run_id=episode.run_id,
                episode_id=episode.id,
                parent_step_id=source.step_id,
                kind=StepKind.VIDEO,
                attempt=attempt,
                operation_key=operation_key,
                provider=self._provider_name,
                model=self._gateway.video_model,
                input_hash=input_hash,
                input_snapshot=snapshot.model_dump(mode="json"),
                prompt_purpose=PromptPurpose.VIDEO,
                prompt_model=self._gateway.video_model,
                prompt_text=prompt,
                parent_prompt_id=parent_prompt_id,
            )
        except Exception:
            # Sequence先占用不可变revision；若收费意图与Prompt未能原子落库，
            # 立即封存该revision，避免留下看似可执行、实际没有Step的草稿。
            self._repository.update_video_sequence(
                sequence_id=draft.id,
                status=SequenceStatus.REJECTED,
            )
            raise
        self._repository.update_video_sequence(
            sequence_id=draft.id,
            status=SequenceStatus.GENERATING,
        )
        self._repository.set_step_status(step.id, StepStatus.SUBMITTING)
        try:
            task = self._gateway.submit_video(
                prompt=prompt,
                input_plan=input_plan,
                input_sources=(
                    provider_task.video_url,
                    boundary_assets[0].path,
                    boundary_assets[1].path,
                ),
            )
        except GatewayError as exc:
            self._repository.fail_step(
                step.id,
                code=exc.code,
                message=str(exc),
                submission_unknown=exc.submission_unknown,
                request_id=exc.request_id,
            )
            if not exc.submission_unknown:
                self._repository.update_video_sequence(
                    sequence_id=draft.id,
                    status=SequenceStatus.REJECTED,
                )
            raise
        self._repository.set_step_status(
            step.id,
            StepStatus.QUEUED,
            provider_task_id=task.task_id,
            input_snapshot_patch={"provider_task_status": task.status},
        )
        return self._finish(episode, self._repository.get_step(step.id), initial_task=task)

    def retry_edit(self, step: StoredStep, *, reason: str) -> dict[str, Any]:
        """从失败编辑的父版本创建新的非破坏性revision。"""

        if not step.operation_key.startswith("video:range_edit:"):
            raise ValueError("该Step不是区间编辑任务")
        if step.status not in {StepStatus.FAILED, StepStatus.EXPIRED, StepStatus.CANCELLED}:
            raise ValueError("区间编辑只允许重试已终止任务")
        if step.episode_id is None:
            raise ValueError("区间编辑Step缺少Episode")
        snapshot = VideoInputSnapshot.model_validate(step.input_snapshot)
        if snapshot.sequence_id is None or None in {
            snapshot.selection_start_ms,
            snapshot.selection_end_ms,
        }:
            raise ValueError("失败区间编辑缺少可恢复的选区快照")
        failed_sequence = self._repository.get_video_sequence(snapshot.sequence_id)
        if failed_sequence.parent_sequence_id is None:
            raise ValueError("失败区间编辑缺少父视频版本")
        instruction = snapshot.edit_instruction or "修复所选区间中的原问题"
        generation_prompt = self._repository.get_prompt_for_step(
            step.id,
            purpose=PromptPurpose.VIDEO,
        )
        return self.range_edit(
            step.episode_id,
            failed_sequence.parent_sequence_id,
            start_ms=int(snapshot.selection_start_ms),
            end_ms=int(snapshot.selection_end_ms),
            boundary_mode=BoundaryMode.EXACT,
            instruction=f"{instruction}。本次人工重试原因：{reason.strip()}",
            allow_paid_generation=True,
            retry_of_step_id=step.id,
            retry_reason=reason.strip(),
            operation_key_override=step.operation_key,
            parent_prompt_id=generation_prompt.id,
        )

    def resume_step(self, step: StoredStep) -> dict[str, Any]:
        if not step.operation_key.startswith("video:range_edit:"):
            raise ValueError("该Step不是区间编辑任务")
        if step.episode_id is None or not step.provider_task_id:
            raise ValueError("区间编辑Step缺少Episode或Ark task ID")
        local_recovery = (
            step.status is StepStatus.FAILED
            and step.error_code == "media_qc_failed"
            and step.input_snapshot.get("provider_task_status") == "succeeded"
        )
        if step.status not in {StepStatus.QUEUED, StepStatus.RUNNING} and not local_recovery:
            raise ValueError("区间编辑只能继续查询运行任务或重做已成功供应商任务的本地QC")
        if local_recovery:
            self._repository.reopen_video_step_for_local_recovery(step.id)
            step = self._repository.get_step(step.id)
        return self._finish(self._episode(step.episode_id), step)

    def reconciliation_candidates(self, step: StoredStep) -> tuple[dict[str, Any], ...]:
        """为创建响应丢失的区间编辑筛选Ark候选，绝不自动绑定。"""

        if (
            not step.operation_key.startswith("video:range_edit:")
            or step.status is not StepStatus.SUBMISSION_UNKNOWN
            or step.provider_task_id
        ):
            raise ValueError("只有没有task ID的submission_unknown区间编辑可以对账")
        snapshot = VideoInputSnapshot.model_validate(step.input_snapshot)
        reference_time = step.submitted_at or step.created_at
        lower = reference_time.astimezone(timezone.utc) - timedelta(minutes=5)
        upper = reference_time.astimezone(timezone.utc) + timedelta(minutes=30)
        model = step.model or self._gateway.video_model
        candidates: list[dict[str, Any]] = []
        for task in self._gateway.list_video_tasks(model=model):
            created = None if task.created_at is None else task.created_at.astimezone(timezone.utc)
            plan = snapshot.input_plan
            if task.model not in {None, model}:
                continue
            if created is not None and not lower <= created <= upper:
                continue
            if task.duration_seconds not in {None, plan.duration_seconds}:
                continue
            if task.ratio not in {None, "9:16"} or task.resolution not in {
                None,
                plan.resolution,
            }:
                continue
            owner = self._repository.find_step_by_provider_task_id(task.task_id)
            if owner is not None and owner.id != step.id:
                continue
            candidates.append(
                {
                    "taskId": task.task_id,
                    "status": task.status,
                    "model": task.model,
                    "createdAt": task.created_at,
                    "durationSeconds": task.duration_seconds,
                    "ratio": task.ratio,
                    "resolution": task.resolution,
                    "generateAudio": task.generate_audio,
                }
            )
        result = tuple(candidates)
        self._repository.patch_step_snapshot(
            step.id,
            {
                "reconciliation_candidates": result,
                "reconciliation_queried_at": datetime.now(timezone.utc),
            },
        )
        return result

    def reconcile_step(self, step: StoredStep, *, provider_task_id: str) -> dict[str, Any]:
        candidates = self.reconciliation_candidates(step)
        if provider_task_id not in {str(item["taskId"]) for item in candidates}:
            raise ValueError("选择的task ID不在当前区间编辑对账候选中")
        owner = self._repository.find_step_by_provider_task_id(provider_task_id)
        if owner is not None and owner.id != step.id:
            raise ValueError("该Ark task ID已经绑定其他步骤")
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
        return self.resume_step(self._repository.get_step(step.id))

    def select_sequence(
        self,
        sequence_id: uuid.UUID,
        *,
        revoke_confirmed_outcome: bool,
        keep_confirmed_outcome: bool,
    ) -> dict[str, Any]:
        result = self._repository.select_video_sequence(
            sequence_id,
            revoke_confirmed_outcome=revoke_confirmed_outcome,
            keep_confirmed_outcome=keep_confirmed_outcome,
        )
        selected = result.sequence
        return {
            "sequenceId": str(selected.id),
            "revision": selected.revision,
            "selectedAssetId": str(selected.rendered_asset_id),
            "outcomeKept": result.outcome_kept,
            "outcomeRevoked": result.outcome_revoked,
        }

    def _finish(
        self,
        episode: StoredEpisode,
        step: StoredStep,
        *,
        initial_task: VideoTaskResult | None = None,
    ) -> dict[str, Any]:
        if not step.provider_task_id:
            raise ValueError("区间编辑Step缺少provider task ID")
        deadline = time.monotonic() + self._task_timeout
        task = initial_task
        while time.monotonic() < deadline:
            task = task or self._gateway.get_video_task(step.provider_task_id)
            if task.status in {"queued", "running"}:
                current = self._repository.get_step(step.id)
                if task.status == "running" and current.status is StepStatus.QUEUED:
                    self._repository.set_step_status(step.id, StepStatus.RUNNING)
                time.sleep(self._poll_interval)
                task = None
                continue
            if task.status != "succeeded" or not task.video_url:
                self._repository.fail_step(
                    step.id,
                    code=task.error_code or task.status,
                    message=task.error_message or "Ark区间编辑任务失败",
                )
                snapshot = VideoInputSnapshot.model_validate(step.input_snapshot)
                assert snapshot.sequence_id is not None
                self._repository.update_video_sequence(
                    sequence_id=snapshot.sequence_id,
                    status=SequenceStatus.REJECTED,
                )
                raise RuntimeError(task.error_message or f"Ark区间编辑状态为{task.status}")
            try:
                return self._land(episode, step, task.video_url)
            except Exception as exc:
                current = self._repository.get_step(step.id)
                if current.status is not StepStatus.FAILED:
                    self._repository.fail_step(
                        step.id,
                        code="media_qc_failed",
                        message=str(exc),
                        input_snapshot_patch={"provider_task_status": "succeeded"},
                    )
                raise
        self._repository.patch_step_snapshot(
            step.id,
            {
                "provider_task_status": "polling_window_elapsed",
                "polling_window_ended_at": datetime.now(timezone.utc),
            },
        )
        return {
            "status": "provider_running",
            "stepId": str(step.id),
            "taskId": step.provider_task_id,
        }

    def _land(
        self,
        episode: StoredEpisode,
        step: StoredStep,
        video_url: str,
    ) -> dict[str, Any]:
        snapshot = VideoInputSnapshot.model_validate(step.input_snapshot)
        if snapshot.sequence_id is None or None in {
            snapshot.selection_start_ms,
            snapshot.selection_end_ms,
            snapshot.source_start_ms,
            snapshot.source_end_ms,
        }:
            raise ValueError("区间编辑快照不完整")
        parent = self._repository.get_video_sequence(snapshot.sequence_id)
        if parent.parent_sequence_id is None:
            raise ValueError("区间编辑版本缺少父版本")
        source_sequence = self._repository.get_video_sequence(parent.parent_sequence_id)
        assert source_sequence.rendered_asset_id is not None
        base = self._repository.asset_detail(source_sequence.rendered_asset_id)
        full_edit = self._asset_store.download(video_url, suffix=".mp4")
        edited_qc = self._probe.inspect_video(
            full_edit.path,
            expected_duration_seconds=snapshot.input_plan.duration_seconds,
            expected_resolution=snapshot.input_plan.resolution,
            minimum_duration_seconds=4,
            maximum_duration_seconds=15,
            duration_tolerance_ms=2000,
        )
        if not edited_qc.get("passed"):
            self._repository.fail_step(
                step.id,
                code="media_qc_failed",
                message=json.dumps(edited_qc, ensure_ascii=False),
                input_snapshot_patch={"provider_task_status": "succeeded"},
            )
            raise RuntimeError("区间编辑供应商视频技术QC失败")
        self._repository.save_asset(
            run_id=episode.run_id,
            episode_id=episode.id,
            step_id=step.id,
            role="video_edit_provider_output",
            semantic_key=f"video-edit-provider:{snapshot.sequence_id}",
            scope="episode",
            status="ready",
            media_type="video",
            landed=full_edit,
            metadata={
                "providerTaskId": step.provider_task_id,
                "qc": edited_qc,
                "temporaryInputForLocalEdl": False,
            },
        )
        source_start = int(snapshot.source_start_ms)
        source_end = int(snapshot.source_end_ms)
        replacement = self._asset_store.extract_video_range(
            source_path=full_edit.path,
            start_ms=source_start,
            end_ms=source_end,
        )
        selection_duration = int(snapshot.selection_end_ms) - int(snapshot.selection_start_ms)
        replacement_qc = self._probe.inspect_video(
            replacement.path,
            expected_duration_seconds=max(1, round(selection_duration / 1000)),
            expected_resolution=snapshot.input_plan.resolution,
            minimum_duration_seconds=0,
            maximum_duration_seconds=14,
            duration_tolerance_ms=1500,
            require_audio=False,
        )
        if not replacement_qc.get("passed"):
            self._repository.fail_step(
                step.id,
                code="media_qc_failed",
                message=json.dumps(replacement_qc, ensure_ascii=False),
                input_snapshot_patch={"provider_task_status": "succeeded"},
            )
            raise RuntimeError("区间编辑替换片段技术QC失败")
        replacement_duration = int(replacement_qc.get("durationMs") or selection_duration)
        segment = self._repository.save_asset(
            run_id=episode.run_id,
            episode_id=episode.id,
            step_id=step.id,
            role="video_edit_segment",
            semantic_key=f"video-edit:{parent.id}",
            scope="episode",
            status="ready",
            media_type="video",
            landed=replacement,
            metadata={
                "providerTaskId": step.provider_task_id,
                "qc": replacement_qc,
                "sourceSelectionMs": [source_start, source_end],
                "providerFullDurationMs": int(
                    edited_qc.get("durationMs") or snapshot.input_plan.duration_seconds * 1000
                ),
            },
        )
        plan = _replace_clip(
            source_sequence.plan,
            start_ms=int(snapshot.selection_start_ms),
            end_ms=int(snapshot.selection_end_ms),
            replacement_asset_id=segment.id,
            replacement_step_id=step.id,
        )
        rendered = self._asset_store.render_range_replacement(
            base_path=base.path,
            replacement_path=segment.path,
            replacement_duration_ms=replacement_duration,
            start_ms=int(snapshot.selection_start_ms),
            end_ms=int(snapshot.selection_end_ms),
        )
        final_qc = self._probe.inspect_video(
            rendered.path,
            expected_duration_seconds=round(plan.duration_ms / 1000),
            expected_resolution=snapshot.input_plan.resolution,
            minimum_duration_seconds=max(1, math.floor(plan.duration_ms / 1000) - 1),
            maximum_duration_seconds=math.ceil(plan.duration_ms / 1000) + 1,
            duration_tolerance_ms=1200,
        )
        if not final_qc.get("passed"):
            self._repository.fail_step(
                step.id,
                code="media_qc_failed",
                message=json.dumps(final_qc, ensure_ascii=False),
                input_snapshot_patch={"provider_task_status": "succeeded"},
            )
            raise RuntimeError("区间替换成片技术QC失败")
        asset = self._repository.save_asset(
            run_id=episode.run_id,
            episode_id=episode.id,
            step_id=step.id,
            role="video",
            semantic_key=f"video:{episode.plan.slot.value}:sequence-{parent.revision}",
            scope="episode",
            status="candidate",
            media_type="video",
            landed=rendered,
            metadata={
                "qc": final_qc,
                "sequenceId": str(parent.id),
                "parentSequenceId": str(source_sequence.id),
                "providerTaskId": step.provider_task_id,
                "shotBoundariesMs": base.metadata.get("shotBoundariesMs", []),
                "diagnosticShotBoundariesMs": base.metadata.get(
                    "diagnosticShotBoundariesMs",
                    [],
                ),
                "audioPolicy": "preserve_original",
            },
        )
        self._repository.update_video_sequence(
            sequence_id=parent.id,
            status=SequenceStatus.CONTENT_REVIEW,
            rendered_asset_id=asset.id,
            plan=plan,
        )
        self._repository.set_step_status(
            step.id,
            StepStatus.AWAITING_REVIEW,
            input_snapshot_patch={"provider_task_status": "succeeded", "media_qc": final_qc},
        )
        self._video_execution.diagnose_candidate(episode, asset, step)
        return {
            "status": "succeeded",
            "stepId": str(step.id),
            "assetId": str(asset.id),
            "sequenceId": str(parent.id),
            "revision": parent.revision,
        }

    def _boundary_assets(
        self,
        episode: StoredEpisode,
        rendered: StoredAsset,
        start_ms: int,
        end_ms: int,
    ) -> tuple[StoredAsset, StoredAsset]:
        duration_ms = _asset_duration_ms(rendered)
        # 容器 duration 的最后 1ms 通常已经越过最后一个可解码帧。边界落在片尾时
        # 向内保留 100ms 的安全余量，避免合法的整镜头选区在收费 Step 创建前因
        # FFmpeg 无法抽取“最后一帧之后”的时间点而失败。
        last_decodable_ms = max(0, duration_ms - min(100, duration_ms))
        paths = self._frames.extract_frames_at(
            rendered,
            timestamps_ms=(
                min(start_ms, last_decodable_ms),
                min(end_ms, last_decodable_ms),
            ),
        )
        assets: list[StoredAsset] = []
        try:
            for label, path in zip(("before", "after"), paths, strict=True):
                landed = self._asset_store.import_local(path)
                assets.append(
                    self._repository.save_asset(
                        run_id=episode.run_id,
                        episode_id=episode.id,
                        step_id=None,
                        role="edit_boundary",
                        semantic_key=f"edit-boundary:{label}-{landed.sha256[:12]}",
                        scope="episode",
                        status="ready",
                        media_type="image",
                        landed=landed,
                        metadata={
                            "sourceAssetId": str(rendered.id),
                            "timestampMs": start_ms if label == "before" else end_ms,
                        },
                    )
                )
        finally:
            for path in paths:
                path.unlink(missing_ok=True)
        return assets[0], assets[1]

    def _resolve_selection(
        self,
        sequence: StoredVideoSequence,
        *,
        start_ms: int,
        end_ms: int,
        boundary_mode: BoundaryMode,
    ) -> tuple[int, int]:
        if not 0 <= start_ms < end_ms <= sequence.plan.duration_ms:
            raise ValueError("时间轴选区超出视频范围")
        if boundary_mode is BoundaryMode.EXACT:
            return start_ms, end_ms
        if sequence.rendered_asset_id is None:
            raise ValueError("视频版本没有可读取的镜头边界")
        rendered = self._repository.asset_detail(sequence.rendered_asset_id)
        raw = rendered.metadata.get("diagnosticShotBoundariesMs")
        # 语义诊断偶尔只返回片头/片尾，不能据此把中段选区吸附成整条视频。
        # 此时回退到导演镜头确定性边界，并与 Web 时间轴保持同一选择语义。
        if not isinstance(raw, list) or len(raw) < 3:
            raw = rendered.metadata.get("shotBoundariesMs", [])
        boundaries = sorted(
            {
                int(item)
                for item in raw
                if isinstance(item, int | float) and 0 <= int(item) <= sequence.plan.duration_ms
            }
        )
        if len(boundaries) < 2:
            raise ValueError("当前视频没有可用于吸附的诊断或计划镜头边界，请改用exact模式")
        snapped_start = max((item for item in boundaries if item <= start_ms), default=0)
        snapped_end = min(
            (item for item in boundaries if item >= end_ms),
            default=sequence.plan.duration_ms,
        )
        if not 500 <= snapped_end - snapped_start <= 13_000:
            raise ValueError("吸附后的镜头区间不在0.5至13秒内，请缩小选区或使用exact模式")
        return snapped_start, snapped_end

    @staticmethod
    def _single_source_clip(
        plan: VideoSequencePlan,
        start_ms: int,
        end_ms: int,
    ) -> VideoSequenceClip:
        matches = [
            clip
            for clip in plan.clips
            if clip.timeline_start_ms <= start_ms and end_ms <= clip.timeline_end_ms
        ]
        if len(matches) != 1:
            raise ValueError("区间编辑选区不得跨越多个来源Clip")
        return matches[0]

    def _episode(self, episode_id: uuid.UUID) -> StoredEpisode:
        detail = self._repository.episode_detail(episode_id)
        from ..domain.contracts import Slot

        return self._repository.get_episode(uuid.UUID(detail["runId"]), Slot(detail["slot"]))


def _asset_duration_ms(asset: StoredAsset) -> int:
    qc = asset.metadata.get("qc")
    value = qc.get("durationMs") if isinstance(qc, dict) else asset.metadata.get("durationMs")
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"视频资产{asset.id}缺少durationMs")
    return value


def _provider_source_window(asset: StoredAsset) -> tuple[int, int]:
    """返回本地Clip在Ark原任务视频中的起点和原任务总时长。"""

    selection = asset.metadata.get("sourceSelectionMs")
    offset = (
        int(selection[0])
        if isinstance(selection, list | tuple)
        and len(selection) == 2
        and isinstance(selection[0], int | float)
        else 0
    )
    full_duration = asset.metadata.get("providerFullDurationMs")
    duration = (
        int(full_duration)
        if isinstance(full_duration, int | float)
        else _asset_duration_ms(asset)
    )
    return offset, duration


def _media_source(asset: StoredAsset, media_type: str) -> MediaSource:
    return MediaSource(
        asset_id=asset.id,
        semantic_key=asset.semantic_key or asset.role,
        media_type=media_type,
        sha256=asset.sha256,
        metadata=asset.metadata,
    )


def _replace_clip(
    plan: VideoSequencePlan,
    *,
    start_ms: int,
    end_ms: int,
    replacement_asset_id: uuid.UUID,
    replacement_step_id: uuid.UUID,
) -> VideoSequencePlan:
    target = VideoEditingService._single_source_clip(plan, start_ms, end_ms)
    values: list[VideoSequenceClip] = []
    for clip in plan.clips:
        if clip.order != target.order:
            values.append(clip)
            continue
        if start_ms > clip.timeline_start_ms:
            before_duration = start_ms - clip.timeline_start_ms
            values.append(
                clip.model_copy(
                    update={
                        "source_end_ms": clip.source_start_ms + before_duration,
                        "timeline_end_ms": start_ms,
                    }
                )
            )
        values.append(
            VideoSequenceClip(
                order=1,
                source_asset_id=replacement_asset_id,
                source_start_ms=0,
                source_end_ms=end_ms - start_ms,
                timeline_start_ms=start_ms,
                timeline_end_ms=end_ms,
                origin=ClipOrigin.GENERATED,
                replacement_step_id=replacement_step_id,
            )
        )
        if end_ms < clip.timeline_end_ms:
            consumed = end_ms - clip.timeline_start_ms
            values.append(
                clip.model_copy(
                    update={
                        "source_start_ms": clip.source_start_ms + consumed,
                        "timeline_start_ms": end_ms,
                    }
                )
            )
    normalized = [item.model_copy(update={"order": index}) for index, item in enumerate(values, 1)]
    return VideoSequencePlan(duration_ms=plan.duration_ms, clips=normalized)
