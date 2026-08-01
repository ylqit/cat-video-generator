"""CLI和HTTP共用的只读查询服务。"""

from __future__ import annotations

import uuid
from typing import Any

from ..domain.contracts import EpisodePlan
from ..domain.prompts import compile_image_prompt, compile_video_prompt_preview
from ..domain.visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
)
from .ports import QueryStore, StoredAsset


class QueryService:
    """稳定工作流读模型，避免不同接口各自猜测状态。"""

    def __init__(self, repository: QueryStore) -> None:
        self._repository = repository

    def list_runs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """分页返回 Run 摘要，供 CLI 与 HTTP 使用同一排序。"""

        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("limit必须在1至200之间且offset不能为负数")
        return self._repository.list_run_summaries(limit, offset)

    def run_graph(self, run_id: uuid.UUID) -> dict[str, Any]:
        """返回一个 Run 的 Episode、Step、Prompt、资产和审核关系图。"""

        return self._repository.workflow_graph(run_id)

    def prompt(self, prompt_id: uuid.UUID) -> dict[str, Any]:
        """返回实际持久化且可审计的完整 Prompt。"""

        return self._repository.prompt_detail(prompt_id)

    def asset(self, asset_id: uuid.UUID) -> StoredAsset:
        """返回资产元数据；文件路径安全仍由接口层校验。"""

        return self._repository.asset_detail(asset_id)

    def episode(self, episode_id: uuid.UUID) -> dict[str, Any]:
        """返回单个 Episode 只读投影。"""

        return self._repository.episode_detail(episode_id)

    def prompt_preview(
        self,
        episode_id: uuid.UUID,
        *,
        resolution: str = "480p",
    ) -> dict[str, Any]:
        """实时编译首末帧与视频Prompt；纯函数预览，不创建Step或收费任务。

        参考素材顺序与VisualPreparationService.select_references保持一致，
        页面编辑后的覆盖文本原样附回，便于对照。
        """

        detail = self._repository.episode_detail(episode_id)
        episode = EpisodePlan(slot=detail["slot"], script=detail["script"])
        first_view = (
            episode.script.shots[0].dominant_view.value
            if episode.script.shots
            else "front"
        )
        view = first_view if first_view in {"front", "side", "back"} else "front"
        style_profile = DEFAULT_STYLE_PROFILE
        reference_keys = tuple(
            dict.fromkeys(
                (
                    f"person:{view}",
                    f"cat:{view}",
                    style_profile.line_reference_key,
                    (
                        style_profile.indoor_reference_key
                        if episode.style_context == "indoor"
                        else style_profile.outdoor_reference_key
                    ),
                    *(
                        entity.semantic_key
                        for entity in episode.script.visible_world.entities
                        if entity.semantic_key is not None
                        and entity.semantic_key.startswith(("element:", "scene:"))
                    ),
                )
            )
        )
        first = compile_image_prompt(
            episode,
            target="first_frame",
            reference_roles=reference_keys,
            style_profile=style_profile,
            series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        )
        last = compile_image_prompt(
            episode,
            target="last_frame",
            reference_roles=reference_keys,
            style_profile=style_profile,
            series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        )
        video = compile_video_prompt_preview(
            episode,
            resolution=resolution,
            style_profile=style_profile,
        )
        return {
            "episodeId": str(episode_id),
            "slot": detail["slot"],
            "firstFrame": first.text,
            "lastFrame": last.text,
            "video": video.text,
            "overrides": self._repository.get_prompt_overrides(episode_id),
        }

    def step(self, step_id: uuid.UUID) -> dict[str, Any]:
        """返回收费意图、Ark task ID 与恢复状态。"""

        return self._repository.step_detail(step_id)

    def list_canon(self) -> list[dict[str, Any]]:
        """返回人物、猫咪和画风三类已批准Canon资产。"""

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
        """返回一个 Run 的全部交付包及其条目。"""

        return self._repository.list_delivery_packages(run_id)

    def delivery_detail(self, package_id: uuid.UUID) -> dict[str, Any]:
        """返回单个交付包及其条目。"""

        return self._repository.delivery_package_detail(package_id)

    def health(self) -> dict[str, Any]:
        """返回数据库身份、版本、SSL 与迁移状态。"""

        return self._repository.health()
