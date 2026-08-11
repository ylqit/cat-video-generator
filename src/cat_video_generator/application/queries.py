"""CLI 与 HTTP 共用的只读查询服务。"""

from __future__ import annotations

import uuid
from typing import Any

from ..domain.contracts import (
    CrossSlotReference,
    CrossSlotReferenceTarget,
    EpisodePlan,
    EpisodeScript,
)
from ..domain.pipeline import PipelineSettings
from ..domain.prompts import (
    compile_look_prompt,
    compile_opening_anchor_prompt,
    compile_video_prompt_preview,
)
from ..domain.rendering import MediaModality, build_render_plan
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
        run_projection = self._repository.workflow_graph(run_id).get("run", {})
        metadata = run_projection.get("planningMetadata", {})
        raw_profile = metadata.get("seriesProfile") if isinstance(metadata, dict) else None
        series = (
            SeriesVisualProfile.model_validate(raw_profile)
            if isinstance(raw_profile, dict)
            else DEFAULT_SERIES_VISUAL_PROFILE
        )
        style = DEFAULT_STYLE_PROFILE
        look_roles = ("person:front", style.line_reference_key)
        raw_references = run_projection.get("crossSlotReferences", {})
        slot_references = (
            raw_references.get(episode.slot.value, [])
            if isinstance(raw_references, dict)
            else []
        )
        references = tuple(
            CrossSlotReference.model_validate(item)
            for item in slot_references
            if isinstance(item, dict)
        )
        referenced_assets = {
            item.asset_id: self._repository.asset_detail(item.asset_id)
            for item in references
        }
        referenced_labels = {
            asset_id: asset.semantic_key or asset.role
            for asset_id, asset in referenced_assets.items()
        }
        anchor_roles = (
            "look:current-appearance",
            "cat:front",
            *(
                f"previous:{item.role.value}:{referenced_labels[item.asset_id]}"
                for item in references
                if item.apply_to
                in {CrossSlotReferenceTarget.OPENING_ANCHOR, CrossSlotReferenceTarget.BOTH}
            ),
        )
        video_references = tuple(
            (
                f"previous:{item.role.value}:{referenced_labels[item.asset_id]}",
                MediaModality(referenced_assets[item.asset_id].media_type),
            )
            for item in references
            if item.apply_to
            in {CrossSlotReferenceTarget.VIDEO, CrossSlotReferenceTarget.BOTH}
        )
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
                        references=video_references if section.order == 1 else (),
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

    def eligible_cross_slot_assets(
        self,
        run_id: uuid.UUID,
        slot: str,
    ) -> list[dict[str, Any]]:
        """列出当前项目中更早时段的已批准媒体；是否引用仍由用户决定。"""

        target_order = {"morning": 1, "noon": 2, "evening": 3}[slot]
        graph = self._repository.workflow_graph(run_id)
        values: list[dict[str, Any]] = []
        for episode in graph.get("episodes", []):
            if int(episode.get("sortOrder", 0)) >= target_order:
                continue
            episode_id = uuid.UUID(str(episode["id"]))
            for asset in self.episode_assets(episode_id):
                if asset.status not in {"approved", "ready"}:
                    continue
                if asset.media_type not in {"image", "video"}:
                    continue
                values.append(
                    {
                        "assetId": str(asset.id),
                        "episodeId": str(episode_id),
                        "sourceSlot": episode["slot"],
                        "role": asset.role,
                        "mediaType": asset.media_type,
                        "semanticKey": asset.semantic_key,
                        "sha256": asset.sha256,
                        "suggestedRole": _suggested_cross_slot_role(asset),
                        "recommendationReason": _cross_slot_reference_reason(asset),
                    }
                )
        return values

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


def _suggested_cross_slot_role(asset: StoredAsset) -> str:
    if asset.role == "look_reference":
        return "identity"
    if asset.role == "opening_anchor":
        return "composition"
    if asset.media_type == "video":
        return "motion"
    if asset.role in {"element", "prop", "reference"}:
        return "prop"
    return "scene"


def _cross_slot_reference_reason(asset: StoredAsset) -> str:
    role = _suggested_cross_slot_role(asset)
    label = {
        "identity": "适合帮助保持人物或猫咪身份",
        "composition": "适合参考前序空间和相对站位",
        "motion": "适合参考前序动作方向或运动节奏",
        "prop": "适合保持跨时段关键道具外观",
        "scene": "适合参考前序场景气氛",
    }[role]
    return f"{label}；仅为素材类型建议，不会自动加入请求"
