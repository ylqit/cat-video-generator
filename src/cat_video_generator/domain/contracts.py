"""V4任意场景、镜头卡和人工参考素材契约。

创作事实只保存项目标题、场景原文和完整镜头描述。供应商输入、数据库状态和
审核结果属于各自边界，不重复塞进剧情JSON。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import Field, model_validator

from .contract_base import StrictModel

CURRENT_CONTRACT_VERSION = 4


class AnchorMode(StrEnum):
    TEXT_ONLY = "text_only"
    EXISTING = "existing"
    GENERATE = "generate"


class ReferenceUsage(StrEnum):
    APPROVED_ANCHOR = "approved_anchor"
    GENERATION_REFERENCE = "generation_reference"


class ReferenceRole(StrEnum):
    IDENTITY = "identity"
    STYLE = "style"
    SCENE = "scene"
    PROP = "prop"
    COMPOSITION = "composition"


class ReferenceTarget(StrEnum):
    ANCHOR = "anchor"
    VIDEO = "video"
    BOTH = "both"


class ReferenceBinding(StrictModel):
    asset_id: Annotated[UUID, Field(alias="assetId")]
    usage: ReferenceUsage
    role: ReferenceRole
    apply_to: Annotated[ReferenceTarget, Field(alias="applyTo")]


class StoryProjectInput(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=160)]
    first_scene_title: Annotated[
        str,
        Field(alias="firstSceneTitle", min_length=1, max_length=120),
    ] = "场景1"
    first_scene_text: Annotated[
        str,
        Field(alias="firstSceneText", min_length=1, max_length=12_000),
    ]


class SceneDraft(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=120)]
    source_text: Annotated[str, Field(alias="sourceText", min_length=1, max_length=12_000)]
    chapter_label: Annotated[
        str | None,
        Field(alias="chapterLabel", max_length=80),
    ] = None
    context_note: Annotated[
        str | None,
        Field(alias="contextNote", max_length=2_000),
    ] = None


class ShotSuggestion(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=100)]
    direction: Annotated[str, Field(min_length=1, max_length=6_000)]
    suggested_duration_seconds: Annotated[
        int,
        Field(alias="suggestedDurationSeconds", ge=8, le=15),
    ] = 8


class ShotSuggestionOutput(StrictModel):
    scene_title: Annotated[
        str,
        Field(alias="sceneTitle", min_length=1, max_length=120),
    ]
    shots: list[ShotSuggestion] = Field(min_length=1)


class ShotCardDraft(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=100)]
    direction: Annotated[str, Field(min_length=1, max_length=6_000)]
    duration_seconds: Annotated[int, Field(alias="durationSeconds", ge=8, le=15)] = 8
    anchor_mode: AnchorMode = Field(default=AnchorMode.TEXT_ONLY, alias="anchorMode")
    reference_bindings: list[ReferenceBinding] = Field(
        default_factory=list,
        alias="referenceBindings",
    )

    @model_validator(mode="after")
    def validate_references(self) -> ShotCardDraft:
        asset_ids = [item.asset_id for item in self.reference_bindings]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("同一素材不能在一张镜头卡中重复绑定")
        approved_anchors = [
            item for item in self.reference_bindings if item.usage is ReferenceUsage.APPROVED_ANCHOR
        ]
        if self.anchor_mode is AnchorMode.EXISTING and len(approved_anchors) != 1:
            raise ValueError("existing模式必须且只能选择一张approved_anchor")
        if self.anchor_mode is not AnchorMode.EXISTING and approved_anchors:
            raise ValueError("只有existing模式允许绑定approved_anchor")
        return self


class ShotPromptContext(StrictModel):
    """编译Prompt所需的最小只读投影。"""

    project_title: str
    scene_title: str
    scene_text: str
    context_note: str | None = None
    shot_title: str
    direction: str
    duration_seconds: int
