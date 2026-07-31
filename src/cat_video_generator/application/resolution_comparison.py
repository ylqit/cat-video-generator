"""同一Prompt、素材与策略的视频分辨率对比生命周期。

本模块只创建独立比较Step和候选资产，不改变正式Episode或Run状态。这样分辨率
实验不会覆盖原视频，也不会意外把比较结果选为交付资产。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from ..domain.contracts import VideoInputPlan
from ..domain.workflow import StepKind, StepStatus
from .errors import StepRetryRequired
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
from .video_diagnostic import VideoDiagnosticService


class ResolutionComparisonService:
    """冻结原始执行输入，只改变分辨率并生成可审计的比较媒体。"""

    def __init__(
        self,
        *,
        repository: WorkflowRepository,
        media_gateway: MediaGenerationGateway,
        asset_store: AssetStore,
        media_probe: MediaProbe,
        provider_name: str,
        video_diagnostic: VideoDiagnosticService | None = None,
        poll_interval_seconds: float = 10,
        task_timeout_seconds: float = 1800,
    ) -> None:
        self._repository = repository
        self._gateway = media_gateway
        self._asset_store = asset_store
        self._probe = media_probe
        self._provider_name = provider_name
        self._video_diagnostic = video_diagnostic
        self._poll_interval = poll_interval_seconds
        self._task_timeout = task_timeout_seconds

    def compare_run(
        self,
        run_id: uuid.UUID,
        *,
        resolution: str,
    ) -> dict[str, Any]:
        """按1/2/3生成比较视频；重复调用只恢复或复用相同收费Step。"""

        if resolution not in {"480p", "720p"}:
            raise ValueError("对比分辨率必须是480p或720p")
        results = []
        for episode in self._repository.list_episodes(run_id):
            source_key = f"video:{episode.id}-single-pass"
            source_assets = self._repository.list_assets(
                run_id=run_id,
                episode_id=episode.id,
                roles=("video",),
                statuses=("candidate", "approved", "ready", "rejected"),
                semantic_keys=(source_key,),
            )
            if not source_assets:
                raise ValueError(f"{episode.plan.slot.value}没有可复用的单次成片")
            results.append(
                self._compare_episode(
                    episode,
                    source_assets[-1],
                    resolution=resolution,
                )
            )
        return {
            "runId": str(run_id),
            "resolution": resolution,
            "episodes": results,
        }

    def resume_step(
        self,
        episode: StoredEpisode,
        step: StoredStep,
    ) -> dict[str, Any]:
        """只恢复已有比较task ID，不创建新的供应商任务。"""

        return self._finish(episode, step)

    def _compare_episode(
        self,
        episode: StoredEpisode,
        source_asset: StoredAsset,
        *,
        resolution: str,
        attempt: int = 1,
        retry_of_step_id: str | None = None,
        retry_reason: str | None = None,
    ) -> dict[str, Any]:
        if source_asset.step_id is None:
            raise ValueError("源视频缺少可审计的producing step")
        source_step = self._repository.get_step(source_asset.step_id)
        if (
            source_step.kind is not StepKind.VIDEO
            or source_step.status is not StepStatus.SUCCEEDED
            or source_step.request_summary.get("generationStrategy") != "single_pass"
        ):
            raise ValueError("分辨率对比只能复用成功的single_pass视频Step")
        source_plan = VideoInputPlan.model_validate(source_step.request_summary["videoInputPlan"])
        if source_plan.model != self._gateway.video_model:
            raise ValueError("当前视频模型与源任务不同，不能称为同策略分辨率对比")
        if source_plan.resolution == resolution:
            raise ValueError(f"源视频已经是{resolution}，无需重复生成")
        source_prompt = self._repository.get_prompt_for_step(
            source_step.id,
            purpose="video",
        )
        input_plan = source_plan.model_copy(update={"resolution": resolution})
        ordered_inputs = tuple(
            self._repository.asset_detail(binding.asset_id) for binding in input_plan.bindings
        )
        input_hash = _input_hash(
            "resolution_comparison",
            str(source_step.id),
            source_prompt.sha256,
            input_plan.model_dump_json(),
            *(asset.sha256 for asset in ordered_inputs),
        )
        operation_key = f"video:resolution_comparison:{resolution}"
        step = self._repository.create_step_intent(
            run_id=episode.run_id,
            episode_id=episode.id,
            parent_step_id=source_step.id,
            kind=StepKind.VIDEO,
            attempt=attempt,
            provider=self._provider_name,
            model=input_plan.model,
            input_hash=input_hash,
            request_summary={
                "operationKey": operation_key,
                "generationStrategy": "resolution_comparison",
                "comparisonOfStepId": str(source_step.id),
                "sourcePromptId": str(source_prompt.id),
                "sourcePromptSha256": source_prompt.sha256,
                "videoInputPlan": input_plan.model_dump(mode="json"),
                "inputAssetIds": [str(asset.id) for asset in ordered_inputs],
                "promptAliases": source_step.request_summary.get("promptAliases", {}),
                "retryOfStepId": retry_of_step_id,
                "retryReason": retry_reason,
            },
        )
        self._repository.save_prompt(
            step_id=step.id,
            parent_prompt_id=source_prompt.id,
            purpose="video",
            model=input_plan.model,
            text=source_prompt.text,
        )
        if step.status in {StepStatus.QUEUED, StepStatus.RUNNING}:
            return self._finish(episode, step)
        if step.status is StepStatus.SUBMISSION_UNKNOWN:
            raise RuntimeError("对比视频提交结果未知，必须先人工对账")
        if step.status is StepStatus.SUCCEEDED:
            return self._existing_result(episode, step, resolution)
        if step.status in {
            StepStatus.FAILED,
            StepStatus.EXPIRED,
            StepStatus.CANCELLED,
        }:
            raise StepRetryRequired(step.id, operation_key)
        if step.status is not StepStatus.PENDING:
            raise ValueError(f"对比步骤状态{step.status.value}不可重复提交")

        # 先保存原Prompt、素材顺序与收费意图；比较任务不触碰正式Episode状态。
        self._repository.set_step_status(step.id, StepStatus.SUBMITTING)
        try:
            task = self._gateway.submit_video(
                prompt=source_prompt.text,
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
            raise
        self._repository.set_step_status(
            step.id,
            StepStatus.QUEUED,
            provider_task_id=task.task_id,
        )
        return self._finish(episode, self._repository.get_step(step.id))

    def retry_comparison(
        self,
        episode: StoredEpisode,
        original_step: StoredStep,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """用相同Prompt与素材显式重做一个分辨率比较任务。"""

        operation_key = str(original_step.request_summary.get("operationKey", ""))
        prefix = "video:resolution_comparison:"
        if not operation_key.startswith(prefix):
            raise ValueError("原步骤不是分辨率对比任务")
        resolution = operation_key.removeprefix(prefix)
        source_step_id = uuid.UUID(
            str(original_step.request_summary["comparisonOfStepId"])
        )
        source_assets = self._repository.list_assets(
            episode_id=episode.id,
            roles=("video",),
            statuses=("candidate", "approved", "ready", "rejected"),
        )
        source_asset = next(
            (
                asset
                for asset in reversed(source_assets)
                if asset.step_id == source_step_id
            ),
            None,
        )
        if source_asset is None:
            raise ValueError("分辨率对比源视频资产不存在")
        attempt = self._repository.next_step_attempt(
            episode_id=episode.id,
            kind=StepKind.VIDEO,
            operation_key=operation_key,
        )
        return self._compare_episode(
            episode,
            source_asset,
            resolution=resolution,
            attempt=attempt,
            retry_of_step_id=str(original_step.id),
            retry_reason=reason,
        )

    def _finish(
        self,
        episode: StoredEpisode,
        step: StoredStep,
    ) -> dict[str, Any]:
        if not step.provider_task_id:
            raise ValueError("恢复对比任务需要provider task ID")
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
                    message=task.error_message or "Seedance对比任务失败",
                )
                raise RuntimeError(task.error_message or "Seedance对比任务失败")
            return self._land(episode, step, task.video_url)
        raise TimeoutError("等待Seedance对比任务超时，可稍后使用resume恢复")

    def _land(
        self,
        episode: StoredEpisode,
        step: StoredStep,
        video_url: str,
    ) -> dict[str, Any]:
        input_plan = VideoInputPlan.model_validate(step.request_summary["videoInputPlan"])
        landed = self._asset_store.download(video_url, suffix=".mp4")
        qc = self._probe.inspect_video(
            landed.path,
            expected_duration_seconds=input_plan.duration_seconds,
            expected_resolution=input_plan.resolution,
        )
        if not qc["passed"]:
            self._repository.fail_step(
                step.id,
                code="comparison_media_qc_failed",
                message=";".join(qc["failures"]),
            )
            raise RuntimeError(f"分辨率对比视频技术QC失败: {qc['failures']}")
        asset = self._repository.save_asset(
            run_id=episode.run_id,
            episode_id=episode.id,
            step_id=step.id,
            role="video_comparison",
            semantic_key=_semantic_key(episode, step, input_plan.resolution),
            scope="episode",
            status="candidate",
            media_type="video",
            landed=landed,
            metadata={
                **qc,
                "comparisonOfStepId": step.request_summary["comparisonOfStepId"],
                "sourcePromptSha256": step.request_summary["sourcePromptSha256"],
                "resolution": input_plan.resolution,
            },
        )
        self._repository.set_step_status(step.id, StepStatus.SUCCEEDED)
        self._repository.record_review(
            step_id=step.id,
            asset_id=asset.id,
            source="technical",
            decision="approved",
            reason="分辨率对比视频通过技术QC；不改变正式Episode审核状态",
            warnings=[],
            evidence={
                **qc,
                "comparisonResolution": input_plan.resolution,
                "semanticReviewStatus": "diagnostic_pending",
                "semanticVerified": False,
            },
        )
        if self._video_diagnostic is not None:
            self._video_diagnostic.diagnose(episode, asset)
        return _result(episode, asset, input_plan.resolution)

    def _existing_result(
        self,
        episode: StoredEpisode,
        step: StoredStep,
        resolution: str,
    ) -> dict[str, Any]:
        assets = self._repository.list_assets(
            run_id=episode.run_id,
            episode_id=episode.id,
            roles=("video_comparison",),
            statuses=("candidate", "approved", "ready", "rejected"),
            semantic_keys=(_semantic_key(episode, step, resolution),),
        )
        if not assets:
            raise RuntimeError("对比步骤已成功但本地媒体资产缺失")
        return _result(episode, assets[-1], resolution)


def _semantic_key(
    episode: StoredEpisode,
    step: StoredStep,
    resolution: str,
) -> str:
    return (
        f"video_comparison:{episode.id}:{step.request_summary['comparisonOfStepId']}:{resolution}"
    )


def _input_hash(*values: str) -> str:
    return hashlib.sha256(
        json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _result(
    episode: StoredEpisode,
    asset: StoredAsset,
    resolution: str,
) -> dict[str, Any]:
    return {
        "episodeId": str(episode.id),
        "slot": episode.plan.slot.value,
        "status": "comparison_review",
        "resolution": resolution,
        "assetId": str(asset.id),
        "localPath": str(asset.path),
    }
