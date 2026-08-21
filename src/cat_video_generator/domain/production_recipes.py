"""Fixed-IP production recipes and their human-review contracts."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from .contract_base import StrictModel
from .rendering import SequenceTransition, SequenceTransitionType


class ProductionRecipeKey(StrEnum):
    HEALING_CHILD_CAT_V1 = "healing_child_cat_v1"


class QualityTier(StrEnum):
    QUICK = "quick"
    BALANCED = "balanced"
    PREMIUM = "premium"


class CatBehaviorMode(StrEnum):
    NATURAL = "natural"
    LIGHT_ANTHROPOMORPHIC = "light_anthropomorphic"


def recipe_task_source_hash(
    *,
    payload: dict[str, Any],
    instance_id: uuid.UUID,
    expected_revision: int,
    phase: str,
) -> str:
    """Fingerprint the immutable recipe inputs captured at enqueue time."""
    document = {
        "payload": payload,
        "recipeInstanceId": str(instance_id),
        "expectedInstanceRevision": expected_revision,
        "phase": phase,
    }
    return hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


class RecipeStage(StrEnum):
    CONCEPT = "concept"
    STORYBOARD = "storyboard"
    ANCHORS = "anchors"
    VIDEO = "video"
    SEQUENCE = "sequence"
    COMPLETE = "complete"


class RecipePhaseKey(StrEnum):
    """User-facing six-stage recipe phase derived from approved domain state."""

    CREATIVE = "creative"
    STORY = "story"
    CHARACTER_DESIGN = "character_design"
    STORYBOARD = "storyboard"
    RENDER = "render"
    EXPORT = "export"
    COMPLETE = "complete"


class CharacterDesignSlot(StrEnum):
    CHILD = "child"
    CAT = "cat"
    PAIR_SCALE = "pair_scale"


class HumanReviewDecision(StrEnum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    OVERRIDE = "override"


class StoryboardCreationMode(StrEnum):
    FROM_STORY = "from_story"
    FROM_CHARACTERS = "from_characters"
    MANUAL = "manual"


class TemporalBeatPhase(StrEnum):
    BEGINNING = "beginning"
    CHANGE = "change"
    WARM_ENDING = "warm_ending"


class SoundPlan(StrictModel):
    ambient: list[str] = Field(min_length=1, max_length=8)
    foley: list[str] = Field(min_length=1, max_length=8)
    music_mood: str = Field(alias="musicMood", min_length=1, max_length=200)
    dialogue_policy: Literal["none"] = Field(alias="dialoguePolicy", default="none")


class EpisodeRules(StrictModel):
    """Per-episode choices that become immutable after story approval."""

    person_wardrobe: str = Field(alias="personWardrobe", min_length=1, max_length=500)
    time_weather: str = Field(alias="timeWeather", min_length=1, max_length=300)
    main_scene: str = Field(alias="mainScene", min_length=1, max_length=500)
    environment: Literal["indoor", "outdoor"]
    core_props: list[str] = Field(alias="coreProps", default_factory=list, max_length=12)
    cat_behavior_mode: CatBehaviorMode = Field(alias="catBehaviorMode")
    sound_plan: SoundPlan = Field(alias="soundPlan")
    style_positive: list[str] = Field(alias="stylePositive", min_length=3, max_length=10)
    style_excluded: list[str] = Field(alias="styleExcluded", min_length=2, max_length=10)
    canon_profile_id: str = Field(
        alias="canonProfileId",
        pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$",
    )


class TemporalBeat(StrictModel):
    phase: TemporalBeatPhase
    start_second: int = Field(alias="startSecond", ge=0, le=15)
    end_second: int = Field(alias="endSecond", gt=0, le=15)
    child_action: str = Field(alias="childAction", min_length=1, max_length=1_000)
    cat_action: str = Field(alias="catAction", min_length=1, max_length=1_000)
    camera: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_interval(self) -> TemporalBeat:
        if self.end_second <= self.start_second:
            raise ValueError("动作节拍结束时间必须晚于开始时间")
        return self


class QualityTierPolicy(StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
        frozen=True,
    )

    anchor_candidate_count: int = Field(alias="anchorCandidateCount", ge=1, le=8)
    video_candidate_count: int = Field(alias="videoCandidateCount", ge=1, le=8)
    character_design_candidate_count: int = Field(
        alias="characterDesignCandidateCount", ge=1, le=8
    )


class ProductionRecipeDefinition(StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
        frozen=True,
    )

    key: ProductionRecipeKey
    title: str
    description: str
    default_duration_seconds: int = Field(alias="defaultDurationSeconds", ge=8, le=60)
    minimum_duration_seconds: int = Field(alias="minimumDurationSeconds", ge=8, le=60)
    maximum_duration_seconds: int = Field(alias="maximumDurationSeconds", ge=8, le=60)
    minimum_shot_seconds: int = Field(alias="minimumShotSeconds", ge=8, le=15)
    maximum_shot_seconds: int = Field(alias="maximumShotSeconds", ge=8, le=15)
    aspect_ratio: Literal["9:16"] = Field(alias="aspectRatio")
    resolution: Literal["720p"]
    story_candidate_count: int = Field(alias="storyCandidateCount", ge=1, le=8)
    quality_tiers: dict[QualityTier, QualityTierPolicy] = Field(alias="qualityTiers")


class ProductionRecipeInstanceDraft(StrictModel):
    recipe_key: ProductionRecipeKey = Field(
        alias="recipeKey",
        default=ProductionRecipeKey.HEALING_CHILD_CAT_V1,
    )
    theme: str = Field(min_length=1, max_length=2_000)
    inspiration_key: str | None = Field(
        alias="inspirationKey",
        default=None,
        max_length=80,
    )
    target_duration_seconds: int = Field(
        alias="targetDurationSeconds",
        default=15,
        ge=8,
        le=60,
    )
    quality_tier: QualityTier = Field(
        alias="qualityTier",
        default=QualityTier.BALANCED,
    )


class ProductionRecipeInstancePatch(StrictModel):
    theme: str | None = Field(default=None, min_length=1, max_length=2_000)
    inspiration_key: str | None = Field(
        alias="inspirationKey",
        default=None,
        max_length=80,
    )
    target_duration_seconds: int | None = Field(
        alias="targetDurationSeconds",
        default=None,
        ge=8,
        le=60,
    )
    quality_tier: QualityTier | None = Field(alias="qualityTier", default=None)

    @model_validator(mode="after")
    def require_change(self) -> ProductionRecipeInstancePatch:
        if not self.model_fields_set:
            raise ValueError("配方实例修改至少需要一个字段")
        return self


class PaidRecipeRunRequest(StrictModel):
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)
    accept_estimated_cost_micros: int = Field(
        alias="acceptEstimatedCostMicros",
        ge=0,
    )
    reason: str | None = Field(default=None, max_length=2_000)


class CharacterDesignBatchDraft(StrictModel):
    """A character-design image batch submitted through the universal media queue."""

    project_id: uuid.UUID = Field(alias="projectId")
    canvas_node_id: uuid.UUID = Field(alias="canvasNodeId")
    media_kind: Literal["image"] = Field(alias="mediaKind", default="image")
    candidate_count: int = Field(alias="candidateCount", ge=1, le=8)
    provider: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=200)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)
    input: dict[str, Any]


class CanvasGroupRunRequest(PaidRecipeRunRequest):
    """Execute exactly one derived recipe phase and stop at its review gate."""


class StoryboardRecipeRunRequest(PaidRecipeRunRequest):
    creation_mode: StoryboardCreationMode = Field(
        alias="creationMode",
        default=StoryboardCreationMode.FROM_STORY,
    )
    reference_asset_ids: list[uuid.UUID] = Field(
        alias="referenceAssetIds",
        default_factory=list,
        max_length=6,
    )
    instruction: str | None = Field(default=None, max_length=4_000)

    @model_validator(mode="after")
    def validate_creation_source(self) -> StoryboardRecipeRunRequest:
        if self.creation_mode is StoryboardCreationMode.MANUAL:
            raise ValueError("手工分镜不会调用付费生成接口，请使用人工分镜草稿接口")
        if (
            self.creation_mode is StoryboardCreationMode.FROM_CHARACTERS
            and not self.reference_asset_ids
        ):
            raise ValueError("角色生成分镜至少需要一个角色素材")
        return self


class RecipeSequenceTransition(StrictModel):
    after_shot_id: uuid.UUID = Field(alias="afterShotId")
    transition: SequenceTransition

    @model_validator(mode="after")
    def validate_recipe_fade_duration(self) -> RecipeSequenceTransition:
        if (
            self.transition.type is not SequenceTransitionType.CUT
            and self.transition.duration_ms < 300
        ):
            raise ValueError("组合包淡化或叠化转场必须为300至1000毫秒")
        return self


class RecipeSequenceRunRequest(PaidRecipeRunRequest):
    transitions: list[RecipeSequenceTransition] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def require_unique_following_shots(self) -> RecipeSequenceRunRequest:
        shot_ids = [item.after_shot_id for item in self.transitions]
        if len(shot_ids) != len(set(shot_ids)):
            raise ValueError("同一镜头只能配置一个转场")
        return self


class HumanReviewDraft(StrictModel):
    """A human decision pinned to an immutable target snapshot."""

    target_type: str = Field(alias="targetType", min_length=1, max_length=80)
    target_id: uuid.UUID = Field(alias="targetId")
    target_revision: int | None = Field(alias="targetRevision", default=None, ge=1)
    target_hash: str | None = Field(
        alias="targetHash",
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    decision: HumanReviewDecision
    blocking_diagnostic_present: bool = Field(
        alias="blockingDiagnosticPresent",
        default=False,
    )
    issues: list[str] = Field(default_factory=list, max_length=30)
    reason: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_resolution(self) -> HumanReviewDraft:
        reason = (self.reason or "").strip()
        if self.target_revision is None and self.target_hash is None:
            raise ValueError("人工审核必须固定目标版本或内容哈希")
        if self.decision is HumanReviewDecision.APPROVE and self.blocking_diagnostic_present:
            raise ValueError("存在阻断诊断时不能普通批准，请修改或人工覆盖")
        if self.decision is HumanReviewDecision.OVERRIDE and not reason:
            raise ValueError("人工覆盖必须填写覆盖理由")
        if (
            self.decision is HumanReviewDecision.REQUEST_CHANGES
            and not reason
            and not self.issues
        ):
            raise ValueError("请求修改必须填写修改原因或问题清单")
        return self


HEALING_CHILD_CAT_RECIPE = ProductionRecipeDefinition(
    key=ProductionRecipeKey.HEALING_CHILD_CAT_V1,
    title="一人一猫治愈短片",
    description="固定儿童、固定猫咪与统一水彩画风的日常治愈短片配方。",
    defaultDurationSeconds=15,
    minimumDurationSeconds=8,
    maximumDurationSeconds=60,
    minimumShotSeconds=8,
    maximumShotSeconds=15,
    aspectRatio="9:16",
    resolution="720p",
    storyCandidateCount=3,
    qualityTiers={
        QualityTier.QUICK: QualityTierPolicy(
            anchorCandidateCount=1,
            videoCandidateCount=1,
            characterDesignCandidateCount=1,
        ),
        QualityTier.BALANCED: QualityTierPolicy(
            anchorCandidateCount=2,
            videoCandidateCount=1,
            characterDesignCandidateCount=2,
        ),
        QualityTier.PREMIUM: QualityTierPolicy(
            anchorCandidateCount=4,
            videoCandidateCount=2,
            characterDesignCandidateCount=4,
        ),
    },
)


def split_shot_durations(total_seconds: int) -> tuple[int, ...]:
    """Balance an 8-60 second episode into provider-sized 8-15 second shots."""

    if not 8 <= total_seconds <= 60:
        raise ValueError("治愈短片总时长必须为8至60秒")
    shot_count = math.ceil(total_seconds / 15)
    base, remainder = divmod(total_seconds, shot_count)
    durations = tuple(base + (1 if index < remainder else 0) for index in range(shot_count))
    if any(not 8 <= item <= 15 for item in durations):
        raise ValueError("无法把总时长拆成8至15秒的镜头")
    return durations


def build_temporal_beats(
    shot_duration_seconds: int,
    *,
    actions: tuple[tuple[str, str, str], ...],
) -> tuple[TemporalBeat, ...]:
    """Build the three complete action windows used by a continuous shot."""

    if not 8 <= shot_duration_seconds <= 15:
        raise ValueError("单镜头时长必须为8至15秒")
    if len(actions) != 3:
        raise ValueError("连续镜头必须包含三个动作节拍")
    base, remainder = divmod(shot_duration_seconds, 3)
    durations = tuple(base + (1 if index < remainder else 0) for index in range(3))
    phases = (
        TemporalBeatPhase.BEGINNING,
        TemporalBeatPhase.CHANGE,
        TemporalBeatPhase.WARM_ENDING,
    )
    cursor = 0
    beats: list[TemporalBeat] = []
    for phase, duration, action in zip(phases, durations, actions, strict=True):
        child_action, cat_action, camera = action
        beats.append(
            TemporalBeat(
                phase=phase,
                startSecond=cursor,
                endSecond=cursor + duration,
                childAction=child_action,
                catAction=cat_action,
                camera=camera,
            )
        )
        cursor += duration
    return tuple(beats)


def canon_v2_reference_keys(
    environment: Literal["indoor", "outdoor"] | str,
) -> tuple[str, ...]:
    """Return the fixed identity order and exactly one watercolor style reference."""

    if environment not in {"indoor", "outdoor"}:
        raise ValueError("Canon-v2环境必须为indoor或outdoor")
    return (
        "person:headshot",
        "person:fullbody",
        "cat:front",
        "cat:side",
        f"style:{environment}",
    )
