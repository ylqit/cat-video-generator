"""CLI 与 HTTP 共用的只读查询服务。"""

from __future__ import annotations

import uuid
from typing import Any

from ..domain.contracts import EpisodePlan
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
    ) -> dict[str, Any]:
        """编译定妆图、开场锚点和全部视频区段 Prompt，不创建 Step。"""

        detail = self._repository.episode_detail(episode_id)
        episode = EpisodePlan(slot=detail["slot"], script=detail["script"])
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
            if any(word in episode.script.scene for word in ("室内", "房间", "家中", "店内"))
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
        }

    def step(self, step_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.step_detail(step_id)

    def pipeline_settings(self, run_id: uuid.UUID) -> PipelineSettings:
        return self._repository.get_pipeline_settings(run_id)

    def episode_assets(self, episode_id: uuid.UUID) -> tuple[StoredAsset, ...]:
        return tuple(
            asset
            for asset in self._repository.list_assets(episode_id=episode_id)
            if asset.episode_id == episode_id
        )

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
