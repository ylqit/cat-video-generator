"""图片、视频、QC和人工审核用例；外部调用前持久化意图且不持有长事务。"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from ..domain.contracts import Slot, VideoInputMode
from ..domain.media import MediaSource, build_video_input_plan
from ..domain.prompts import compile_image_prompt, compile_video_prompt
from ..domain.rules import hard_failures, validate_input_gate
from ..domain.workflow import EpisodeStatus, RunStatus, StepKind, StepStatus
from .ports import (
    AssetStore,
    GatewayError,
    MediaGenerationGateway,
    MediaProbe,
    StoredAsset,
    StoredEpisode,
    StoredStep,
    WorkflowRepository,
)


class ProductionService:
    """按Episode推进视觉素材、Seedance、QC和审核。"""

    def __init__(
        self,
        *,
        repository: WorkflowRepository,
        media_gateway: MediaGenerationGateway,
        asset_store: AssetStore,
        media_probe: MediaProbe,
        provider_name: str,
        resolution: str,
        keyframe_review_mode: str,
        poll_interval_seconds: float = 10,
        task_timeout_seconds: float = 1800,
    ) -> None:
        self._repository = repository
        self._gateway = media_gateway
        self._asset_store = asset_store
        self._probe = media_probe
        self._provider_name = provider_name
        self._resolution = resolution
        self._keyframe_review_mode = str(keyframe_review_mode)
        self._poll_interval = poll_interval_seconds
        self._task_timeout = task_timeout_seconds

    def run_day(
        self,
        run_id: uuid.UUID,
        *,
        slot: Slot | None,
        allow_paid_generation: bool,
    ) -> dict[str, Any]:
        """生成指定Episode或按1、2、3推进全天。"""

        if not allow_paid_generation:
            raise ValueError("媒体生成需要显式提供--allow-paid-generation")
        stored_run = self._repository.get_run(run_id)
        if stored_run.plan is None:
            raise ValueError("Run尚未形成可执行方案")
        if stored_run.status in {RunStatus.ARCHIVED.value, RunStatus.DELIVERED.value}:
            raise ValueError(f"Run状态{stored_run.status}不允许继续生成")
        if stored_run.status == RunStatus.PLANNED.value:
            self._repository.set_run_status(run_id, RunStatus.GENERATING)

        episodes = (
            (self._repository.get_episode(run_id, slot),)
            if slot is not None
            else self._repository.list_episodes(run_id)
        )
        results = [self._run_episode(episode) for episode in episodes]
        current = self._repository.list_episodes(run_id)
        if all(item.status is EpisodeStatus.READY for item in current):
            self._repository.set_run_status(run_id, RunStatus.READY)
        elif any(item.status is EpisodeStatus.CONTENT_REVIEW for item in current):
            # 单Slot定向生成与全天生成必须收敛到同一Run状态。
            # 否则三次--slot执行全部完成后，Run会错误地永远停在generating。
            self._repository.set_run_status(run_id, RunStatus.REVIEWING)
        return {"runId": str(run_id), "episodes": results}

    def resume(self, run_id: uuid.UUID | None) -> list[dict[str, Any]]:
        """恢复已有异步任务，不创建新的供应商任务。"""

        results: list[dict[str, Any]] = []
        for step in self._repository.list_resumable_steps(run_id):
            if step.status is StepStatus.SUBMISSION_UNKNOWN:
                results.append(
                    {
                        "stepId": str(step.id),
                        "status": step.status.value,
                        "nextAction": "人工对账Ark任务列表",
                    }
                )
                continue
            if step.kind is not StepKind.VIDEO or not step.provider_task_id:
                continue
            episode = next(
                item
                for item in self._repository.list_episodes(step.run_id)
                if item.id == step.episode_id
            )
            results.append(
                self._finish_video_step(
                    episode,
                    step,
                )
            )
        return results

    def _run_episode(
        self,
        episode: StoredEpisode,
    ) -> dict[str, Any]:
        if episode.status in {
            EpisodeStatus.CONTENT_REVIEW,
            EpisodeStatus.READY,
        }:
            return _episode_result(episode, "无需重复生成")

        references = self._reference_assets(episode)
        issues = validate_input_gate(
            episode.plan,
            (asset.role for asset in references),
        )
        if hard_failures(issues):
            raise ValueError("; ".join(issue.message for issue in issues))

        visual_assets = self._ensure_visual_assets(
            episode,
            references,
        )
        if visual_assets is None:
            return _episode_result(
                self._repository.get_episode(
                    episode.run_id,
                    episode.plan.slot,
                ),
                "关键帧等待人工语义审核",
            )
        refreshed = self._repository.get_episode(
            episode.run_id,
            episode.plan.slot,
        )
        if refreshed.status is EpisodeStatus.PREPARING_VISUALS:
            self._repository.set_episode_status(
                refreshed.id,
                EpisodeStatus.VIDEO_PENDING,
            )
        elif refreshed.status in {
            EpisodeStatus.PLANNED,
            EpisodeStatus.FAILED,
        }:
            self._repository.set_episode_status(
                refreshed.id,
                EpisodeStatus.VIDEO_PENDING,
            )
        refreshed = self._repository.get_episode(
            episode.run_id,
            episode.plan.slot,
        )
        return self._generate_video(refreshed, visual_assets)

    def _reference_assets(
        self,
        episode: StoredEpisode,
    ) -> tuple[StoredAsset, ...]:
        optional_roles = ("element", "scene", "motion", "atmosphere")
        requested_roles = tuple(
            dict.fromkeys((*episode.plan.required_reference_roles, *optional_roles))
        )
        assets = self._repository.list_assets(
            run_id=episode.run_id,
            episode_id=episode.id,
            roles=requested_roles,
            statuses=("approved", "ready"),
        )
        # 运行输入优先采用批准Canon的front或subject-free派生资产。
        grouped: dict[str, list[StoredAsset]] = {}
        for asset in assets:
            grouped.setdefault(asset.role, []).append(asset)
        latest: dict[str, StoredAsset] = {}
        for role, candidates in grouped.items():
            preferred = [
                item
                for item in candidates
                if (
                    role in {"person", "cat"}
                    and item.metadata.get("referenceView") == "front"
                )
                or (role == "style" and item.metadata.get("subjectFree") is True)
            ]
            latest[role] = (preferred or candidates)[-1]
        return tuple(latest[role] for role in requested_roles if role in latest)

    def _ensure_visual_assets(
        self,
        episode: StoredEpisode,
        references: tuple[StoredAsset, ...],
    ) -> tuple[StoredAsset, ...] | None:
        input_mode = episode.plan.video_input_mode
        if input_mode is VideoInputMode.MULTIMODAL_REFERENCE:
            return references

        if episode.status in {EpisodeStatus.PLANNED, EpisodeStatus.FAILED}:
            self._repository.set_episode_status(
                episode.id,
                EpisodeStatus.PREPARING_VISUALS,
            )
        first = self._ensure_image(
            episode,
            references,
            target="first_frame",
        )
        if first.status == "candidate":
            return None
        if input_mode is VideoInputMode.STRICT_FIRST_FRAME:
            return (first,)
        last = self._ensure_image(
            episode,
            (first, *references),
            target="last_frame",
        )
        if last.status == "candidate":
            return None
        return (first, last)

    def _ensure_image(
        self,
        episode: StoredEpisode,
        references: tuple[StoredAsset, ...],
        *,
        target: str,
    ) -> StoredAsset:
        existing = self._repository.list_assets(
            run_id=episode.run_id,
            episode_id=episode.id,
            roles=(target,),
            statuses=("candidate", "approved", "ready"),
        )
        if existing:
            return existing[-1]
        image_references = tuple(
            asset for asset in references if asset.media_type == "image"
        )
        if not image_references:
            raise ValueError("Seedream关键帧生成至少需要一张图片参考")
        if len(image_references) > 5:
            raise ValueError("Seedream关键帧最多使用5张重要参考图")
        compiled = compile_image_prompt(
            episode.plan,
            target=target,
            reference_roles=tuple(asset.role for asset in image_references),
        )
        input_hash = _input_hash(
            compiled.text,
            *(asset.sha256 for asset in image_references),
        )
        step = self._repository.create_step_intent(
            run_id=episode.run_id,
            episode_id=episode.id,
            parent_step_id=None,
            kind=StepKind.IMAGE,
            attempt=1,
            provider=self._provider_name,
            model=self._gateway.image_model,
            input_hash=input_hash,
            request_summary={
                "target": target,
                "inputAssetIds": [str(asset.id) for asset in image_references],
            },
        )
        self._repository.save_prompt(
            step_id=step.id,
            parent_prompt_id=None,
            purpose="image",
            model=self._gateway.image_model,
            text=compiled.text,
        )
        if step.status is StepStatus.SUCCEEDED:
            raise RuntimeError("图片步骤已成功但缺少对应资产")
        self._repository.set_step_status(step.id, StepStatus.SUBMITTING)
        try:
            result = self._gateway.generate_image(
                prompt=compiled.text,
                reference_paths=tuple(asset.path for asset in image_references),
            )
            landed = self._asset_store.download(result.url, suffix=".png")
            metadata = self._probe.inspect_image(landed.path)
        except GatewayError as exc:
            self._repository.fail_step(
                step.id,
                code=exc.code,
                message=str(exc),
                submission_unknown=exc.submission_unknown,
            )
            raise
        asset = self._repository.save_asset(
            run_id=episode.run_id,
            episode_id=episode.id,
            step_id=step.id,
            role=target,
            scope="episode",
            status=(
                "candidate" if self._keyframe_review_mode == "manual" else "approved"
            ),
            media_type="image",
            landed=landed,
            metadata=metadata,
        )
        if self._keyframe_review_mode == "manual":
            # 技术QC只证明图片可读，不能证明人物身份、眼睛或物理初态正确。
            # manual模式必须停在这里，人工批准后同一资产才会被后续步骤复用。
            self._repository.set_step_status(
                step.id,
                StepStatus.AWAITING_REVIEW,
            )
            self._repository.record_review(
                step_id=step.id,
                asset_id=asset.id,
                source="technical",
                decision="pending",
                reason="图片技术QC通过，等待人工语义审核",
                warnings=[],
                evidence={
                    **metadata,
                    "reviewMode": "manual",
                    "semanticReviewStatus": "pending",
                    "autoApprovedUnverified": False,
                },
            )
        else:
            # 技术自动放行必须明确标记语义未验证，不能伪装成人工审核。
            self._repository.set_step_status(step.id, StepStatus.SUCCEEDED)
            self._repository.record_review(
                step_id=step.id,
                asset_id=asset.id,
                source="technical",
                decision="approved",
                reason="图片技术QC通过；按technical_auto模式非阻断放行",
                warnings=[],
                evidence={
                    **metadata,
                    "reviewMode": "technical_auto",
                    "semanticReviewStatus": "skipped",
                    "semanticVerified": False,
                    "autoApprovedUnverified": True,
                },
            )
        return asset

    def _generate_video(
        self,
        episode: StoredEpisode,
        inputs: tuple[StoredAsset, ...],
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
                )
                for asset in inputs
            ),
        )
        assets_by_id = {asset.id: asset for asset in inputs}
        ordered_inputs = tuple(
            assets_by_id[binding.asset_id] for binding in input_plan.bindings
        )
        compiled = compile_video_prompt(
            episode.plan,
            input_plan=input_plan,
        )
        input_hash = _input_hash(
            compiled.text,
            input_plan.model_dump_json(),
            *(asset.sha256 for asset in ordered_inputs),
        )
        step = self._repository.create_step_intent(
            run_id=episode.run_id,
            episode_id=episode.id,
            parent_step_id=None,
            kind=StepKind.VIDEO,
            attempt=1,
            provider=self._provider_name,
            model=self._gateway.video_model,
            input_hash=input_hash,
            request_summary={
                "videoInputPlan": input_plan.model_dump(mode="json"),
                "inputAssetIds": [str(asset.id) for asset in ordered_inputs],
                "promptAliases": {
                    binding.prompt_alias: str(binding.asset_id)
                    for binding in input_plan.bindings
                },
            },
        )
        self._repository.save_prompt(
            step_id=step.id,
            parent_prompt_id=None,
            purpose="video",
            model=self._gateway.video_model,
            text=compiled.text,
        )
        if step.status in {
            StepStatus.QUEUED,
            StepStatus.RUNNING,
        }:
            return self._finish_video_step(episode, step)
        if step.status is StepStatus.SUBMISSION_UNKNOWN:
            raise RuntimeError("视频提交结果未知，必须先人工对账")
        if step.status is StepStatus.SUCCEEDED:
            return _episode_result(episode, "视频步骤已经成功")

        self._repository.set_episode_status(
            episode.id,
            EpisodeStatus.VIDEO_GENERATING,
        )
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
            self._repository.set_episode_status(
                episode.id,
                EpisodeStatus.FAILED,
            )
            raise
        self._repository.set_step_status(
            step.id,
            StepStatus.QUEUED,
            provider_task_id=task.task_id,
        )
        return self._finish_video_step(
            episode,
            self._repository.get_step(step.id),
        )

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
                self._repository.fail_step(
                    step.id,
                    code=exc.code,
                    message=str(exc),
                )
                raise
            if task.status in {"queued", "running"}:
                current = self._repository.get_step(step.id)
                if task.status == "running" and current.status is StepStatus.QUEUED:
                    self._repository.set_step_status(
                        step.id,
                        StepStatus.RUNNING,
                    )
                time.sleep(self._poll_interval)
                continue
            if task.status != "succeeded" or not task.video_url:
                self._repository.fail_step(
                    step.id,
                    code=task.error_code or task.status,
                    message=task.error_message or "Seedance任务失败",
                )
                self._repository.set_episode_status(
                    episode.id,
                    EpisodeStatus.FAILED,
                )
                raise RuntimeError(task.error_message or "Seedance任务失败")
            return self._land_video(episode, step, task.video_url)
        raise TimeoutError("等待Seedance任务超过配置时限，可稍后使用resume恢复")

    def _land_video(
        self,
        episode: StoredEpisode,
        step: StoredStep,
        video_url: str,
    ) -> dict[str, Any]:
        landed = self._asset_store.download(video_url, suffix=".mp4")
        self._repository.set_episode_status(
            episode.id,
            EpisodeStatus.MEDIA_QC,
        )
        qc = self._probe.inspect_video(
            landed.path,
            expected_duration_seconds=episode.plan.duration_seconds,
            expected_resolution=self._resolution,
        )
        if not qc["passed"]:
            self._repository.fail_step(
                step.id,
                code="media_qc_failed",
                message=";".join(qc["failures"]),
            )
            self._repository.set_episode_status(
                episode.id,
                EpisodeStatus.FAILED,
            )
            raise RuntimeError(f"视频技术QC失败: {qc['failures']}")
        asset = self._repository.save_asset(
            run_id=episode.run_id,
            episode_id=episode.id,
            step_id=step.id,
            role="video",
            scope="episode",
            status="candidate",
            media_type="video",
            landed=landed,
            metadata=qc,
        )
        self._repository.set_step_status(step.id, StepStatus.SUCCEEDED)
        self._repository.set_episode_status(
            episode.id,
            EpisodeStatus.CONTENT_REVIEW,
        )
        self._repository.record_review(
            step_id=step.id,
            asset_id=asset.id,
            source="technical",
            decision="approved",
            reason="视频通过容器、轨道、分辨率和时长检查",
            warnings=[],
            evidence=qc,
        )
        return {
            "episodeId": str(episode.id),
            "slot": episode.plan.slot.value,
            "status": EpisodeStatus.CONTENT_REVIEW.value,
            "assetId": str(asset.id),
            "localPath": str(asset.path),
        }


def _input_hash(*values: str) -> str:
    return hashlib.sha256(
        json.dumps(
            values,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _episode_result(
    episode: StoredEpisode,
    message: str,
) -> dict[str, Any]:
    return {
        "episodeId": str(episode.id),
        "slot": episode.plan.slot.value,
        "status": episode.status.value,
        "message": message,
    }
