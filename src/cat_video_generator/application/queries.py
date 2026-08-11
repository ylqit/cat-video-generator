"""CLI 与 HTTP 共用的只读查询服务。"""

from __future__ import annotations

import uuid
from typing import Any

from ..domain.contracts import EpisodePlan, EpisodeScript
from ..domain.pipeline import PipelineSettings
from ..domain.prompts import (
    compile_look_prompt,
    compile_opening_anchor_prompt,
    compile_video_prompt_preview,
)
from ..domain.rendering import build_render_plan
from ..domain.visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
    SeriesVisualProfile,
)
from .ports import QueryStore, StoredAsset


class QueryService:
    """稳定工作流读模型，避免 CLI 与 Web 各自推测节点状态。"""

    def __init__(
        self,
        repository: QueryStore,
        *,
        video_resolution: str = "720p",
    ) -> None:
        self._repository = repository
        if video_resolution not in {"480p", "720p"}:
            raise ValueError("视频预览分辨率必须是480p或720p")
        self._video_resolution = video_resolution

    def list_runs(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("limit 必须在 1 至 200 之间且 offset 不能为负数")
        return self._repository.list_run_summaries(limit, offset)

    def run_graph(self, run_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.workflow_graph(run_id)

    def prompt(self, prompt_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.prompt_detail(prompt_id)

    def asset(self, asset_id: uuid.UUID) -> StoredAsset:
        return self._repository.asset_detail(asset_id)

    def episode(self, episode_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.episode_detail(episode_id)

    def prompt_preview(
        self,
        episode_id: uuid.UUID,
        *,
        script_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """编译定妆图、开场锚点和全部视频区段 Prompt，不创建 Step。

        创作台可传入尚未保存的结构化脚本以获得实时预览；该对象只在内存中
        通过同一领域编译器处理，不写数据库，也不会触发任何供应商调用。
        """

        detail = self._repository.episode_detail(episode_id)
        episode = EpisodePlan(
            slot=detail["slot"],
            script=(
                EpisodeScript.model_validate(script_override)
                if script_override is not None
                else detail["script"]
            ),
        )
        run_id = uuid.UUID(detail["runId"])
        context = self._repository.get_planning_context(run_id)
        metadata = context.get("planningMetadata", {})
        raw_profile = metadata.get("seriesProfile") if isinstance(metadata, dict) else None
        series = (
            SeriesVisualProfile.model_validate(raw_profile)
            if isinstance(raw_profile, dict)
            else DEFAULT_SERIES_VISUAL_PROFILE
        )
        style = DEFAULT_STYLE_PROFILE
        look_roles = ("person:front", style.line_reference_key)
        scene_style = (
            style.indoor_reference_key
            if episode.script.visual_context == "indoor"
            else style.outdoor_reference_key
        )
        anchor_roles = ("look:current-appearance", "cat:front", scene_style)
        render_plan = build_render_plan(episode)
        return {
            "episodeId": str(episode_id),
            "slot": detail["slot"],
            "look": compile_look_prompt(
                episode,
                reference_roles=look_roles,
                series_profile=series,
                style_profile=style,
            ).text,
            "openingAnchor": compile_opening_anchor_prompt(
                episode,
                reference_roles=anchor_roles,
                series_profile=series,
                style_profile=style,
            ).text,
            "videoSections": [
                {
                    "order": section.order,
                    "durationSeconds": section.duration_seconds,
                    "prompt": compile_video_prompt_preview(
                        episode,
                        resolution=self._video_resolution,
                        section_order=section.order,
                        style_profile=style,
                        series_profile=series,
                    ).text,
                }
                for section in render_plan.sections
            ],
            "renderPlan": render_plan.model_dump(mode="json"),
            "resolution": self._video_resolution,
            "overrides": self._repository.get_prompt_overrides(episode_id),
            "overrideState": self._repository.get_prompt_override_state(episode_id),
        }

    def step(self, step_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.step_detail(step_id)

    def step_trace(self, step_id: uuid.UUID) -> dict[str, Any]:
        """按需返回一个工作流节点的完整输入、Prompt、输出和审核血缘。"""

        trace = self._repository.step_trace(step_id)
        step = trace["step"]
        episode_id = step.get("episodeId")
        trace["currentCompiledPrompts"] = []
        trace["currentStructuredOutput"] = trace.get("effectiveOutput")
        if episode_id is None:
            return trace

        episode_uuid = uuid.UUID(episode_id)
        detail = self._repository.episode_detail(episode_uuid)
        trace["currentStructuredOutput"] = detail["script"]
        operation_key = str(step.get("operationKey") or "")
        if operation_key.startswith("director:"):
            return trace

        preview = self.prompt_preview(episode_uuid)
        if operation_key == "image:look":
            trace["currentCompiledPrompts"] = [
                {"purpose": "image", "label": "当前定妆图Prompt", "text": preview["look"]}
            ]
        elif operation_key == "image:opening_anchor":
            trace["currentCompiledPrompts"] = [
                {
                    "purpose": "image",
                    "label": "当前开场锚点Prompt",
                    "text": preview["openingAnchor"],
                }
            ]
        elif operation_key.startswith("video:range_edit:"):
            # 区间编辑Prompt绑定一次具体时间选区与边界帧，不能脱离原attempt重编译。
            # 已持久化actualPrompts就是唯一可审计事实。
            trace["currentCompiledPrompts"] = []
        elif operation_key.startswith("video:"):
            try:
                section_order = 1 if operation_key == "video:single_pass" else int(
                    operation_key.rsplit(":", 1)[1]
                )
            except ValueError:
                section_order = 1
            trace["currentCompiledPrompts"] = [
                {
                    "purpose": "video",
                    "label": f"当前视频区段{section_order} Prompt",
                    "text": item["prompt"],
                }
                for item in preview["videoSections"]
                if item["order"] == section_order
            ]
        return trace

    def pipeline_settings(self, run_id: uuid.UUID) -> PipelineSettings:
        return self._repository.get_pipeline_settings(run_id)

    def episode_assets(self, episode_id: uuid.UUID) -> tuple[StoredAsset, ...]:
        return tuple(
            asset
            for asset in self._repository.list_assets(episode_id=episode_id)
            if asset.episode_id == episode_id
        )

    def video_sequences(self, episode_id: uuid.UUID) -> list[dict[str, Any]]:
        """返回Episode的非破坏性视频版本和单轨EDL。"""

        return [
            {
                "id": str(item.id),
                "episodeId": str(item.episode_id),
                "revision": item.revision,
                "parentSequenceId": (
                    None if item.parent_sequence_id is None else str(item.parent_sequence_id)
                ),
                "baseAssetId": str(item.base_asset_id),
                "renderedAssetId": (
                    None if item.rendered_asset_id is None else str(item.rendered_asset_id)
                ),
                "status": item.status.value,
                "durationMs": item.plan.duration_ms,
                "audioPolicy": item.audio_policy,
                "clips": [clip.model_dump(mode="json") for clip in item.plan.clips],
                "createdAt": item.created_at.isoformat(),
                "updatedAt": item.updated_at.isoformat(),
            }
            for item in self._repository.list_video_sequences(episode_id)
        ]

    def list_canon(self) -> list[dict[str, Any]]:
        assets = self._repository.list_assets(
            run_id=None,
            roles=("person", "cat", "style"),
            statuses=("approved", "ready"),
        )
        return [
            {
                "id": str(asset.id),
                "role": asset.role,
                "semanticKey": asset.semantic_key,
                "scope": asset.scope,
                "status": asset.status,
                "sha256": asset.sha256,
                "metadata": asset.metadata,
            }
            for asset in assets
        ]

    def list_deliveries(self, run_id: uuid.UUID) -> list[dict[str, Any]]:
        return self._repository.list_delivery_packages(run_id)

    def delivery_detail(self, package_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.delivery_package_detail(package_id)

    def health(self) -> dict[str, Any]:
        return self._repository.health()
