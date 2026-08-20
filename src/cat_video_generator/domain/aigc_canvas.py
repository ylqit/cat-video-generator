"""Canvas V2 contracts and deterministic production rules.

The canvas is a projection of these typed domain objects.  Coordinates and
edges may help the editor, but they never replace the business relationships
validated here.
"""

from __future__ import annotations

import math
import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from .contract_base import StrictModel


class SubjectKind(StrEnum):
    PERSON = "person"
    ANIMAL = "animal"
    PRODUCT = "product"
    OBJECT = "object"
    LOCATION = "location"
    STYLE = "style"


class SubjectRole(StrEnum):
    PROTAGONIST = "protagonist"
    CO_PROTAGONIST = "co_protagonist"
    SUPPORT = "support"
    HERO_PRODUCT = "hero_product"
    PROP = "prop"
    ENVIRONMENT = "environment"


class StoryRevisionStatus(StrEnum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class StoryStrategy(StrEnum):
    RELATIONSHIP = "relationship"
    PROBLEM_SOLVING = "problem_solving"
    TWIST_HOOK = "twist_hook"
    COMBINED = "combined"
    LEGACY_IMPORT = "legacy_import"


class CanvasNodeType(StrEnum):
    BRIEF = "BriefNode"
    SUBJECT = "SubjectNode"
    STORY_PLANNER = "StoryPlannerNode"
    STORY_CANDIDATE = "StoryCandidateNode"
    STORY_CRITIC = "StoryCriticNode"
    APPROVAL_GATE = "ApprovalGateNode"
    STORYBOARD_DIRECTOR = "StoryboardDirectorNode"
    SCENE = "SceneNode"
    SHOT_BEAT = "ShotBeatNode"
    IMAGE_GENERATION = "ImageGenerationNode"
    VIDEO_GENERATION = "VideoGenerationNode"
    REVIEW = "ReviewNode"
    TIMELINE = "TimelineNode"
    REFERENCE_ASSET = "ReferenceAssetNode"
    GENERATION_BATCH = "GenerationBatchNode"
    IMAGE_ASSET = "ImageAssetNode"
    VIDEO_ASSET = "VideoAssetNode"
    VIDEO_EDIT = "VideoEditNode"
    VIDEO_SEGMENT = "VideoSegmentNode"
    PROMPT_ARTIFACT = "PromptArtifactNode"
    AUDIO_GENERATION = "AudioGenerationNode"


class CanvasPortType(StrEnum):
    BRIEF = "brief"
    SUBJECTS = "subject[]"
    STORY_REVISION = "story_revision"
    SCENE_PLAN = "scene_plan"
    SHOT_BEATS = "shot_beat[]"
    IMAGE_REFERENCES = "image_reference[]"
    IMAGE_ASSET = "image_asset"
    VIDEO_ASSET = "video_asset"
    APPROVED_ASSET = "approved_asset"
    MEDIA_REFERENCES = "media_reference[]"
    PRODUCT_SUBJECT = "product_subject"
    IMAGE_ASSETS = "image_asset[]"
    EDIT_RECIPE = "edit_recipe"
    PROMPT = "prompt"
    AUDIO_ASSET = "audio_asset"


class StoryBrief(StrictModel):
    theme: str = Field(min_length=1, max_length=2_000)
    audience: str = Field(min_length=1, max_length=300)
    genre: str = Field(min_length=1, max_length=200)
    tone: str = Field(min_length=1, max_length=300)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = Field(alias="aspectRatio")
    target_duration_seconds: int = Field(
        alias="targetDurationSeconds",
        ge=8,
        le=600,
    )
    constraints: list[str] = Field(default_factory=list, max_length=30)


class SubjectReferenceDraft(StrictModel):
    asset_id: uuid.UUID = Field(alias="assetId")
    semantic_role: Literal[
        "front",
        "side",
        "back",
        "turnaround",
        "expression",
        "full_body",
        "outfit",
        "packshot_front",
        "label_detail",
        "material",
        "size_scale",
        "usage_scene",
        "other",
    ] = Field(alias="semanticRole")
    instruction: str = Field(default="", max_length=1_000)


class SubjectDraft(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    kind: SubjectKind
    role: SubjectRole
    identity_anchors: list[str] = Field(
        alias="identityAnchors",
        min_length=1,
        max_length=30,
    )
    immutable_traits: list[str] = Field(
        alias="immutableTraits",
        default_factory=list,
        max_length=30,
    )
    relationship_notes: str = Field(alias="relationshipNotes", default="", max_length=2_000)
    dramatic_function: str = Field(alias="dramaticFunction", default="", max_length=1_000)
    visual_risks: list[str] = Field(alias="visualRisks", default_factory=list, max_length=30)
    references: list[SubjectReferenceDraft] = Field(default_factory=list, max_length=30)


SubjectCompletionField = Literal[
    "identityAnchors",
    "immutableTraits",
    "relationshipNotes",
    "dramaticFunction",
    "visualRisks",
]


class SubjectCompletionProposal(StrictModel):
    """A reviewable suggestion; it never becomes a subject revision by itself."""

    identity_anchors: list[str] = Field(alias="identityAnchors", min_length=1, max_length=30)
    immutable_traits: list[str] = Field(
        alias="immutableTraits", default_factory=list, max_length=30
    )
    relationship_notes: str = Field(alias="relationshipNotes", default="", max_length=2_000)
    dramatic_function: str = Field(alias="dramaticFunction", default="", max_length=1_000)
    visual_risks: list[str] = Field(alias="visualRisks", default_factory=list, max_length=30)
    rationale: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list, max_length=30)


class ActualReferenceBinding(StrictModel):
    asset_id: uuid.UUID = Field(alias="assetId")
    subject_revision_id: uuid.UUID | None = Field(alias="subjectRevisionId", default=None)
    semantic_role: str = Field(alias="semanticRole", min_length=1, max_length=80)
    provider_included: bool = Field(alias="providerIncluded")
    omission_reason: str | None = Field(alias="omissionReason", default=None, max_length=1_000)

    @model_validator(mode="after")
    def require_omission_reason(self) -> ActualReferenceBinding:
        if not self.provider_included and not (self.omission_reason or "").strip():
            raise ValueError("omissionReason is required when a reference is omitted")
        return self


class NodeGenerationConfigDraft(StrictModel):
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)
    mode: Literal[
        "text_to_image",
        "text_to_video",
        "image_to_video",
        "first_last_frame",
        "all_reference",
        "audio",
    ]
    aspect_ratio: Literal["auto", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"] = (
        Field(alias="aspectRatio")
    )
    resolution: Literal["480p", "720p", "1080p", "4k"]
    duration_seconds: int = Field(alias="durationSeconds", ge=1, le=300)
    audio_enabled: bool = Field(alias="audioEnabled", default=False)
    candidate_count: int = Field(alias="candidateCount", ge=1, le=8)
    auto_validate: bool = Field(alias="autoValidate", default=True)
    auto_link: bool = Field(alias="autoLink", default=True)
    actual_references: list[ActualReferenceBinding] = Field(
        alias="actualReferences", default_factory=list, max_length=30
    )


_COMPLETION_FIELD_ATTRIBUTES: dict[str, str] = {
    "identityAnchors": "identity_anchors",
    "immutableTraits": "immutable_traits",
    "relationshipNotes": "relationship_notes",
    "dramaticFunction": "dramatic_function",
    "visualRisks": "visual_risks",
}


def subject_completion_missing_fields(subject: SubjectDraft) -> tuple[SubjectCompletionField, ...]:
    missing: list[SubjectCompletionField] = []
    for alias, attribute in _COMPLETION_FIELD_ATTRIBUTES.items():
        value = getattr(subject, attribute)
        if value == "" or value == []:
            missing.append(alias)  # type: ignore[arg-type]
    return tuple(missing)


def merge_subject_completion(
    source: SubjectDraft,
    proposal: SubjectCompletionProposal,
    *,
    accepted_fields: tuple[str, ...],
) -> SubjectDraft:
    unknown = set(accepted_fields) - set(_COMPLETION_FIELD_ATTRIBUTES)
    if unknown:
        raise ValueError(f"不支持的主体补全字段：{', '.join(sorted(unknown))}")
    updates = {
        attribute: getattr(proposal, attribute)
        for alias, attribute in _COMPLETION_FIELD_ATTRIBUTES.items()
        if alias in accepted_fields
    }
    return source.model_copy(update=updates, deep=True)


class StoryScorecard(StrictModel):
    opening_hook: int = Field(alias="openingHook", ge=0, le=10)
    causal_completeness: int = Field(alias="causalCompleteness", ge=0, le=10)
    subject_necessity: int = Field(alias="subjectNecessity", ge=0, le=10)
    emotional_arc: int = Field(alias="emotionalArc", ge=0, le=10)
    visualizability: int = Field(ge=0, le=10)
    duration_fit: int = Field(alias="durationFit", ge=0, le=10)
    continuity_risk: int = Field(alias="continuityRisk", ge=0, le=10)
    safety: int = Field(ge=0, le=10)
    rationale: str = Field(min_length=1, max_length=4_000)
    warnings: list[str] = Field(default_factory=list, max_length=30)

    @property
    def average(self) -> float:
        values = (
            self.opening_hook,
            self.causal_completeness,
            self.subject_necessity,
            self.emotional_arc,
            self.visualizability,
            self.duration_fit,
            self.continuity_risk,
            self.safety,
        )
        return round(sum(values) / len(values), 2)


class StorySceneOutline(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    purpose: str = Field(min_length=1, max_length=1_000)
    synopsis: str = Field(min_length=1, max_length=4_000)
    duration_weight: int = Field(alias="durationWeight", ge=1, le=100)


class StoryCandidateOutput(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    logline: str = Field(min_length=1, max_length=2_000)
    synopsis: str = Field(min_length=1, max_length=12_000)
    scenes: list[StorySceneOutline] = Field(min_length=1, max_length=30)


class StoryboardBeatOutput(StrictModel):
    scene_order: int = Field(alias="sceneOrder", ge=1, le=30)
    title: str = Field(min_length=1, max_length=160)
    action: str = Field(min_length=1, max_length=4_000)
    camera: str = Field(min_length=1, max_length=2_000)
    dialogue: str = Field(default="", max_length=4_000)
    duration_weight: int = Field(alias="durationWeight", ge=1, le=100)


class StoryboardPlanOutput(StrictModel):
    beats: list[StoryboardBeatOutput] = Field(min_length=1, max_length=75)


class CanvasConnection(StrictModel):
    source_node_id: uuid.UUID = Field(alias="sourceNodeId")
    source_node_type: CanvasNodeType = Field(alias="sourceNodeType")
    source_port: CanvasPortType = Field(alias="sourcePort")
    target_node_id: uuid.UUID = Field(alias="targetNodeId")
    target_node_type: CanvasNodeType = Field(alias="targetNodeType")
    target_port: CanvasPortType = Field(alias="targetPort")

    @model_validator(mode="after")
    def validate_ports(self) -> CanvasConnection:
        output_ports = _NODE_OUTPUT_PORTS[self.source_node_type]
        if self.source_port not in output_ports:
            raise ValueError(
                f"{self.source_node_type.value} 不提供 {self.source_port.value} 输出"
            )
        input_ports = _NODE_INPUT_PORTS[self.target_node_type]
        if self.target_port not in input_ports:
            raise ValueError(
                f"{self.target_node_type.value} 不接受 {self.target_port.value} 输入"
            )
        compatible_targets = _PORT_COMPATIBILITY[self.source_port]
        if self.target_port not in compatible_targets:
            raise ValueError(
                f"{self.source_port.value} 不接受连接到 {self.target_port.value}"
            )
        if self.source_node_id == self.target_node_id:
            raise ValueError("画布节点不能连接到自身")
        return self


class PromptRunDraft(StrictModel):
    purpose: str = Field(min_length=1, max_length=120)
    node_id: uuid.UUID | None = Field(alias="nodeId", default=None)
    business_object_type: str = Field(alias="businessObjectType", min_length=1, max_length=80)
    business_object_id: uuid.UUID = Field(alias="businessObjectId")
    parent_run_id: uuid.UUID | None = Field(alias="parentRunId", default=None)
    template_name: str = Field(alias="templateName", min_length=1, max_length=160)
    template_version: str = Field(alias="templateVersion", min_length=1, max_length=80)
    system_prompt: str = Field(alias="systemPrompt", default="", max_length=100_000)
    user_prompt: str = Field(alias="userPrompt", min_length=1, max_length=100_000)
    final_prompt: str = Field(alias="finalPrompt", min_length=1, max_length=200_000)
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)
    provider_request_snapshot: dict[str, Any] = Field(alias="providerRequestSnapshot")
    input_snapshot: dict[str, Any] = Field(alias="inputSnapshot")
    parameters: dict[str, Any] = Field(default_factory=dict)
    provider_internal_transform: Literal["not_observable"] = Field(
        alias="providerInternalTransform",
        default="not_observable",
    )


_NARRATIVE_ROLES = {
    SubjectRole.PROTAGONIST,
    SubjectRole.CO_PROTAGONIST,
    SubjectRole.SUPPORT,
}


def validate_story_inputs(
    brief: StoryBrief,
    subjects: tuple[SubjectDraft, ...],
) -> None:
    del brief
    narrative_subjects = [item for item in subjects if item.role in _NARRATIVE_ROLES]
    if len(narrative_subjects) < 2:
        raise ValueError("故事策略生成至少需要至少两个叙事主体")
    normalized_names = [item.name.casefold() for item in subjects]
    if len(normalized_names) != len(set(normalized_names)):
        raise ValueError("同一项目内主体名称不能重复")


def allocate_durations(
    total_seconds: int,
    weights: tuple[int, ...],
    *,
    minimum_seconds: int = 1,
) -> tuple[int, ...]:
    """Allocate an exact integer duration using stable largest remainders."""

    if not weights or any(weight <= 0 for weight in weights):
        raise ValueError("时长权重必须为正整数")
    if minimum_seconds < 1:
        raise ValueError("最小时长必须为正整数")
    if total_seconds < len(weights) * minimum_seconds:
        raise ValueError("总时长不足以满足每段最小时长")

    allocations = [0] * len(weights)
    active = set(range(len(weights)))
    remaining = total_seconds
    while active:
        active_weight = sum(weights[index] for index in active)
        under_minimum = [
            index
            for index in active
            if remaining * weights[index] / active_weight < minimum_seconds
        ]
        if not under_minimum:
            raw = {
                index: remaining * weights[index] / active_weight for index in active
            }
            for index, value in raw.items():
                allocations[index] = math.floor(value)
            remainder = remaining - sum(allocations[index] for index in active)
            order = sorted(
                active,
                key=lambda index: (-(raw[index] - math.floor(raw[index])), index),
            )
            for index in order[:remainder]:
                allocations[index] += 1
            break
        for index in under_minimum:
            allocations[index] = minimum_seconds
            remaining -= minimum_seconds
            active.remove(index)

    return tuple(allocations)


def allocate_bounded_durations(
    total_seconds: int,
    weights: tuple[int, ...],
    *,
    minimum_seconds: int,
    maximum_seconds: int,
) -> tuple[int, ...]:
    """Allocate an exact total while respecting provider clip duration bounds."""

    if not weights or any(weight <= 0 for weight in weights):
        raise ValueError("时长权重必须为正整数")
    if minimum_seconds < 1 or maximum_seconds < minimum_seconds:
        raise ValueError("供应商时长边界无效")
    if not minimum_seconds * len(weights) <= total_seconds <= maximum_seconds * len(weights):
        raise ValueError("当前 Beat 数量无法适配供应商时长范围")

    allocations = [minimum_seconds] * len(weights)
    remaining = total_seconds - sum(allocations)
    while remaining:
        active = [index for index, value in enumerate(allocations) if value < maximum_seconds]
        active_weight = sum(weights[index] for index in active)
        increments = [0] * len(weights)
        for index in active:
            share = remaining * weights[index] // active_weight
            increments[index] = min(maximum_seconds - allocations[index], share)
        distributed = sum(increments)
        if distributed == 0:
            index = max(
                active,
                key=lambda item: (
                    remaining * weights[item] % active_weight,
                    weights[item],
                    -item,
                ),
            )
            increments[index] = 1
            distributed = 1
        for index, increment in enumerate(increments):
            allocations[index] += increment
        remaining -= distributed
    return tuple(allocations)


def approve_story_revision(
    current_status: StoryRevisionStatus,
    *,
    scorecard: StoryScorecard | None,
    revision_subject_ids: tuple[uuid.UUID, ...],
    required_subject_ids: tuple[uuid.UUID, ...],
) -> StoryRevisionStatus:
    if current_status is not StoryRevisionStatus.CANDIDATE:
        raise ValueError("只有候选故事版本可以批准")
    if scorecard is None:
        raise ValueError("故事批准前必须完成评审评分")
    missing = set(required_subject_ids) - set(revision_subject_ids)
    if missing:
        raise ValueError(f"故事版本缺少主体：{len(missing)} 个")
    return StoryRevisionStatus.APPROVED


_NODE_INPUT_PORTS: dict[CanvasNodeType, frozenset[CanvasPortType]] = {
    CanvasNodeType.BRIEF: frozenset(),
    CanvasNodeType.SUBJECT: frozenset(),
    CanvasNodeType.STORY_PLANNER: frozenset(
        {CanvasPortType.BRIEF, CanvasPortType.SUBJECTS}
    ),
    CanvasNodeType.STORY_CANDIDATE: frozenset({CanvasPortType.STORY_REVISION}),
    CanvasNodeType.STORY_CRITIC: frozenset({CanvasPortType.STORY_REVISION}),
    CanvasNodeType.APPROVAL_GATE: frozenset({CanvasPortType.STORY_REVISION}),
    CanvasNodeType.STORYBOARD_DIRECTOR: frozenset(
        {CanvasPortType.STORY_REVISION, CanvasPortType.SUBJECTS}
    ),
    CanvasNodeType.SCENE: frozenset({CanvasPortType.SCENE_PLAN}),
    CanvasNodeType.SHOT_BEAT: frozenset(
        {CanvasPortType.SHOT_BEATS, CanvasPortType.SUBJECTS}
    ),
    CanvasNodeType.IMAGE_GENERATION: frozenset(
        {CanvasPortType.SHOT_BEATS, CanvasPortType.IMAGE_REFERENCES, CanvasPortType.PROMPT}
    ),
    CanvasNodeType.VIDEO_GENERATION: frozenset(
        {
            CanvasPortType.SHOT_BEATS,
            CanvasPortType.IMAGE_REFERENCES,
            CanvasPortType.IMAGE_ASSET,
            CanvasPortType.PROMPT,
        }
    ),
    CanvasNodeType.REVIEW: frozenset(
        {CanvasPortType.IMAGE_ASSET, CanvasPortType.VIDEO_ASSET}
    ),
    CanvasNodeType.TIMELINE: frozenset({CanvasPortType.APPROVED_ASSET}),
    CanvasNodeType.REFERENCE_ASSET: frozenset(),
    CanvasNodeType.GENERATION_BATCH: frozenset(
        {CanvasPortType.PRODUCT_SUBJECT, CanvasPortType.MEDIA_REFERENCES}
    ),
    CanvasNodeType.IMAGE_ASSET: frozenset({CanvasPortType.IMAGE_ASSETS}),
    CanvasNodeType.VIDEO_ASSET: frozenset({CanvasPortType.VIDEO_ASSET}),
    CanvasNodeType.VIDEO_EDIT: frozenset(
        {CanvasPortType.VIDEO_ASSET, CanvasPortType.MEDIA_REFERENCES}
    ),
    CanvasNodeType.VIDEO_SEGMENT: frozenset({CanvasPortType.EDIT_RECIPE}),
    CanvasNodeType.PROMPT_ARTIFACT: frozenset(),
    CanvasNodeType.AUDIO_GENERATION: frozenset({CanvasPortType.PROMPT}),
}

_NODE_OUTPUT_PORTS: dict[CanvasNodeType, frozenset[CanvasPortType]] = {
    CanvasNodeType.BRIEF: frozenset({CanvasPortType.BRIEF}),
    CanvasNodeType.SUBJECT: frozenset(
        {CanvasPortType.SUBJECTS, CanvasPortType.PRODUCT_SUBJECT}
    ),
    CanvasNodeType.STORY_PLANNER: frozenset({CanvasPortType.STORY_REVISION}),
    CanvasNodeType.STORY_CANDIDATE: frozenset({CanvasPortType.STORY_REVISION}),
    CanvasNodeType.STORY_CRITIC: frozenset({CanvasPortType.STORY_REVISION}),
    CanvasNodeType.APPROVAL_GATE: frozenset({CanvasPortType.STORY_REVISION}),
    CanvasNodeType.STORYBOARD_DIRECTOR: frozenset({CanvasPortType.SCENE_PLAN}),
    CanvasNodeType.SCENE: frozenset({CanvasPortType.SHOT_BEATS}),
    CanvasNodeType.SHOT_BEAT: frozenset({CanvasPortType.SHOT_BEATS}),
    CanvasNodeType.IMAGE_GENERATION: frozenset({CanvasPortType.IMAGE_ASSET}),
    CanvasNodeType.VIDEO_GENERATION: frozenset({CanvasPortType.VIDEO_ASSET}),
    CanvasNodeType.REVIEW: frozenset({CanvasPortType.APPROVED_ASSET}),
    CanvasNodeType.TIMELINE: frozenset(),
    CanvasNodeType.REFERENCE_ASSET: frozenset({CanvasPortType.MEDIA_REFERENCES}),
    CanvasNodeType.GENERATION_BATCH: frozenset({CanvasPortType.IMAGE_ASSETS}),
    CanvasNodeType.IMAGE_ASSET: frozenset(
        {CanvasPortType.IMAGE_ASSET, CanvasPortType.MEDIA_REFERENCES}
    ),
    CanvasNodeType.VIDEO_ASSET: frozenset({CanvasPortType.VIDEO_ASSET}),
    CanvasNodeType.VIDEO_EDIT: frozenset({CanvasPortType.EDIT_RECIPE}),
    CanvasNodeType.VIDEO_SEGMENT: frozenset({CanvasPortType.VIDEO_ASSET}),
    CanvasNodeType.PROMPT_ARTIFACT: frozenset({CanvasPortType.PROMPT}),
    CanvasNodeType.AUDIO_GENERATION: frozenset({CanvasPortType.AUDIO_ASSET}),
}

_PORT_COMPATIBILITY: dict[CanvasPortType, frozenset[CanvasPortType]] = {
    CanvasPortType.BRIEF: frozenset({CanvasPortType.BRIEF}),
    CanvasPortType.SUBJECTS: frozenset({CanvasPortType.SUBJECTS}),
    CanvasPortType.STORY_REVISION: frozenset({CanvasPortType.STORY_REVISION}),
    CanvasPortType.SCENE_PLAN: frozenset({CanvasPortType.SCENE_PLAN}),
    CanvasPortType.SHOT_BEATS: frozenset({CanvasPortType.SHOT_BEATS}),
    CanvasPortType.IMAGE_REFERENCES: frozenset({CanvasPortType.IMAGE_REFERENCES}),
    CanvasPortType.IMAGE_ASSET: frozenset(
        {CanvasPortType.IMAGE_ASSET, CanvasPortType.IMAGE_REFERENCES}
    ),
    CanvasPortType.VIDEO_ASSET: frozenset({CanvasPortType.VIDEO_ASSET}),
    CanvasPortType.APPROVED_ASSET: frozenset({CanvasPortType.APPROVED_ASSET}),
    CanvasPortType.MEDIA_REFERENCES: frozenset({CanvasPortType.MEDIA_REFERENCES}),
    CanvasPortType.PRODUCT_SUBJECT: frozenset({CanvasPortType.PRODUCT_SUBJECT}),
    CanvasPortType.IMAGE_ASSETS: frozenset({CanvasPortType.IMAGE_ASSETS}),
    CanvasPortType.EDIT_RECIPE: frozenset({CanvasPortType.EDIT_RECIPE}),
    CanvasPortType.PROMPT: frozenset({CanvasPortType.PROMPT}),
    CanvasPortType.AUDIO_ASSET: frozenset({CanvasPortType.AUDIO_ASSET}),
}
