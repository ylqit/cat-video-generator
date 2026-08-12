"""V5任意场景、视频片段和人工参考素材契约。

创作事实只保存项目标题、场景原文和完整镜头描述。供应商输入、数据库状态和
审核结果属于各自边界，不重复塞进剧情JSON。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import Field, model_validator

from .contract_base import StrictModel
from .visual_profiles import DEFAULT_SERIES_VISUAL_PROFILE, DEFAULT_STYLE_PROFILE

CURRENT_CONTRACT_VERSION = 5


class StoryMode(StrEnum):
    SINGLE = "single"
    MULTI = "multi"


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


class EnvironmentStyle(StrEnum):
    OUTDOOR = "outdoor"
    INDOOR = "indoor"


class LookReferencePurpose(StrEnum):
    PERSON_IDENTITY = "person_identity"
    PERSON_BODY = "person_body"
    CAT_IDENTITY = "cat_identity"
    STYLE = "style"
    WARDROBE = "wardrobe"
    PROP = "prop"
    COMPOSITION = "composition"


class ReferenceBinding(StrictModel):
    asset_id: Annotated[UUID, Field(alias="assetId")]
    usage: ReferenceUsage
    role: ReferenceRole
    apply_to: Annotated[ReferenceTarget, Field(alias="applyTo")]


class LookReferenceBinding(StrictModel):
    asset_id: Annotated[UUID, Field(alias="assetId")]
    purpose: LookReferencePurpose
    instruction: Annotated[str, Field(max_length=1_000)] = ""


class VisualProfileDraft(StrictModel):
    person_identity: Annotated[
        str,
        Field(alias="personIdentity", min_length=8, max_length=600),
    ] = DEFAULT_SERIES_VISUAL_PROFILE.person_identity
    person_hair: Annotated[
        str,
        Field(alias="personHair", min_length=4, max_length=300),
    ] = DEFAULT_SERIES_VISUAL_PROFILE.person_hair
    person_body: Annotated[
        str,
        Field(alias="personBody", min_length=4, max_length=300),
    ] = DEFAULT_SERIES_VISUAL_PROFILE.person_body
    cat_identity: Annotated[
        str,
        Field(alias="catIdentity", min_length=8, max_length=600),
    ] = DEFAULT_SERIES_VISUAL_PROFILE.cat_identity
    style_positive: Annotated[
        tuple[str, ...],
        Field(alias="stylePositive", min_length=3, max_length=10),
    ] = DEFAULT_STYLE_PROFILE.positive_features
    style_negative: Annotated[
        tuple[str, ...],
        Field(alias="styleNegative", min_length=2, max_length=10),
    ] = DEFAULT_STYLE_PROFILE.excluded_features
    reference_bindings: list[LookReferenceBinding] = Field(
        default_factory=list,
        alias="referenceBindings",
        max_length=14,
    )

    @model_validator(mode="after")
    def validate_profile_references(self) -> VisualProfileDraft:
        allowed = {
            LookReferencePurpose.PERSON_IDENTITY,
            LookReferencePurpose.PERSON_BODY,
            LookReferencePurpose.CAT_IDENTITY,
            LookReferencePurpose.STYLE,
        }
        if any(item.purpose not in allowed for item in self.reference_bindings):
            raise ValueError("项目视觉档案只允许人物、猫咪和画风参考")
        _validate_unique_look_assets(self.reference_bindings)
        return self


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


class SceneLookPlan(StrictModel):
    person_wardrobe: Annotated[
        str,
        Field(alias="personWardrobe", max_length=1_000),
    ] = ""
    person_accessories: Annotated[
        str,
        Field(alias="personAccessories", max_length=1_000),
    ] = ""
    cat_appearance: Annotated[
        str,
        Field(alias="catAppearance", max_length=1_000),
    ] = ""
    key_props: Annotated[
        str,
        Field(alias="keyProps", max_length=1_000),
    ] = ""
    environment_style: EnvironmentStyle = Field(
        default=EnvironmentStyle.OUTDOOR,
        alias="environmentStyle",
    )
    person_pose: Annotated[str, Field(alias="personPose", max_length=1_000)] = ""
    cat_pose: Annotated[str, Field(alias="catPose", max_length=1_000)] = ""
    composition: Annotated[str, Field(max_length=1_500)] = ""
    additional_instructions: Annotated[
        str,
        Field(alias="additionalInstructions", max_length=2_000),
    ] = ""
    image_recommended: bool = Field(default=False, alias="imageRecommended")
    recommendation_reason: Annotated[
        str | None,
        Field(alias="recommendationReason", max_length=2_000),
    ] = None


class SceneLookDraft(StrictModel):
    visual_profile_revision_id: Annotated[UUID, Field(alias="visualProfileRevisionId")]
    look_plan: SceneLookPlan = Field(alias="lookPlan")
    reference_bindings: list[LookReferenceBinding] = Field(
        default_factory=list,
        alias="referenceBindings",
        max_length=14,
    )

    @model_validator(mode="after")
    def validate_reference_assets(self) -> SceneLookDraft:
        _validate_unique_look_assets(self.reference_bindings)
        return self


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
    story_mode: StoryMode = Field(default=StoryMode.SINGLE, alias="storyMode")
    target_shot_count: Annotated[
        int,
        Field(default=1, alias="targetShotCount", ge=1, le=6),
    ]
    look_plan: SceneLookPlan | None = Field(default=None, alias="lookPlan")

    @model_validator(mode="after")
    def validate_story_mode(self) -> SceneDraft:
        if self.story_mode is StoryMode.SINGLE and self.target_shot_count != 1:
            raise ValueError("single模式必须只生成一个视频片段")
        if self.story_mode is StoryMode.MULTI and self.target_shot_count < 2:
            raise ValueError("multi模式必须生成2到6个视频片段")
        return self


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
    look_plan: SceneLookPlan = Field(default_factory=SceneLookPlan, alias="lookPlan")
    shots: list[ShotSuggestion] = Field(min_length=1, max_length=6)


class ShotCardDraft(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=100)]
    direction: Annotated[str, Field(min_length=1, max_length=6_000)]
    duration_seconds: Annotated[int, Field(alias="durationSeconds", ge=8, le=15)] = 8
    anchor_mode: AnchorMode = Field(default=AnchorMode.TEXT_ONLY, alias="anchorMode")
    reference_bindings: list[ReferenceBinding] = Field(
        default_factory=list,
        alias="referenceBindings",
    )
    inherit_project_references: bool = Field(
        default=True,
        alias="inheritProjectReferences",
    )
    use_scene_look: bool = Field(default=True, alias="useSceneLook")

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


def _validate_unique_look_assets(bindings: list[LookReferenceBinding]) -> None:
    asset_ids = [item.asset_id for item in bindings]
    if len(asset_ids) != len(set(asset_ids)):
        raise ValueError("同一素材不能在定妆参考中重复绑定")
