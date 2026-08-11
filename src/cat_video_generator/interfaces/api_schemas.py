"""写操作HTTP端点的请求模型与共享常量。

模型与api_write.py分离，既保持Router模块低于架构体量上限，也让前端契约
集中可读。这里只做字段校验；业务校验仍在端点与Application Service。
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from ..domain.contracts import (
    CrossSlotReferenceRole,
    CrossSlotReferenceTarget,
    EpisodeSources,
    RunCreativeControls,
    SceneRoute,
    Slot,
    StoryConnectionMode,
    StoryInputMode,
    StoryProjectInput,
)
from ..domain.visual_profiles import CreativeProfileOverride

CANON_ROLES = frozenset({"person", "cat", "style"})
CANON_VIEWS = {
    "person": frozenset({"headshot", "fullbody", "front", "side", "back"}),
    "cat": frozenset({"front", "side", "back"}),
}
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
REFERENCE_ROLES = frozenset({"element", "scene"})
REFERENCE_SUFFIXES = {
    "element": IMAGE_SUFFIXES,
    "scene": IMAGE_SUFFIXES,
}


class CreativeProfileRequest(BaseModel):
    """Web 创建 Run 时可选的性格偏好；HTTP 使用 camelCase，领域仍保持 snake_case。"""

    model_config = ConfigDict(populate_by_name=True)

    person_personality: str | None = Field(
        None,
        alias="personPersonality",
        min_length=4,
        max_length=200,
    )
    cat_personality: str | None = Field(
        None,
        alias="catPersonality",
        min_length=4,
        max_length=200,
    )
    humor_style: str | None = Field(
        None,
        alias="humorStyle",
        min_length=4,
        max_length=200,
    )

    def to_domain(self) -> CreativeProfileOverride:
        """在HTTP边界完成命名转换，避免把前端字段约定泄漏到领域模型。"""

        return CreativeProfileOverride.model_validate(self.model_dump())


class StoryPreviewRequest(BaseModel):
    """仅执行本地三集文本拆分，不创建Run或收费意图。"""

    model_config = ConfigDict(populate_by_name=True)

    text: str = Field(min_length=1, max_length=50_000)


class EpisodeSourcesRequest(BaseModel):
    morning: str | None = Field(None, min_length=4, max_length=20_000)
    noon: str | None = Field(None, min_length=4, max_length=20_000)
    evening: str | None = Field(None, min_length=4, max_length=20_000)


class StoryProjectInputRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    theme: str = Field(min_length=2, max_length=160)
    input_mode: StoryInputMode = Field(alias="inputMode")
    scene_route: SceneRoute = Field(SceneRoute.ADAPTIVE, alias="sceneRoute")
    episode_sources: EpisodeSourcesRequest = Field(
        default_factory=EpisodeSourcesRequest,
        alias="episodeSources",
    )

    def to_domain(self) -> StoryProjectInput:
        return StoryProjectInput(
            theme=self.theme,
            input_mode=self.input_mode,
            scene_route=self.scene_route,
            episode_sources=EpisodeSources.model_validate(
                self.episode_sources.model_dump()
            ),
        )


class StoryProjectRequest(BaseModel):
    """创建生活故事项目；是否需要总导演由projectInput.inputMode决定。"""

    model_config = ConfigDict(populate_by_name=True)

    target_date: date = Field(alias="contentDate")
    project_input: StoryProjectInputRequest = Field(alias="projectInput")
    creative_profile: CreativeProfileRequest | None = Field(
        None,
        alias="creativeProfile",
    )
    allow_paid_generation: bool = Field(False, alias="allowPaidGeneration")
    pipeline_settings: dict | None = Field(None, alias="pipelineSettings")
    creative_controls: RunCreativeControls | None = Field(
        None,
        alias="creativeControls",
    )


class EpisodeSourceRequest(BaseModel):
    """用户在当前时段导演执行前保存的原始剧情。"""

    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(alias="sourceText", min_length=4, max_length=20_000)


class GenerateRequest(BaseModel):
    """按时段或全天生成的请求体；slot为空表示推进全天。"""

    model_config = ConfigDict(populate_by_name=True)

    slot: Slot | None = None
    allow_paid_generation: bool = Field(False, alias="allowPaidGeneration")


class ReviewRequest(BaseModel):
    """人工审核决定；理由必填以保留审计线索。"""

    approve: bool
    reason: str = Field(min_length=1, max_length=500)


class PaidRequest(BaseModel):
    """仅需付费许可的简单请求体。"""

    model_config = ConfigDict(populate_by_name=True)

    allow_paid_generation: bool = Field(False, alias="allowPaidGeneration")


class SlotPlanRequest(PaidRequest):
    """逐时段镜头化；无用户原文时必须显式要求AI生成。"""

    generate_from_theme: bool = Field(False, alias="generateFromTheme")


class AcceptedOutcomeRequest(BaseModel):
    """人工确认的实际成片结果；用于解锁与可选关联，不会被导演隐式读取。"""

    model_config = ConfigDict(populate_by_name=True)

    summary: str = Field(min_length=8, max_length=1000)
    carry_forward: list[str] = Field(
        default_factory=list,
        alias="carryForward",
        max_length=8,
    )
    do_not_carry_forward: list[str] = Field(
        default_factory=list,
        alias="doNotCarryForward",
        max_length=8,
    )


class StoryConnectionRequest(BaseModel):
    """用户确认的关联卡；保存正文和是否加载，不隐式调用AI。"""

    model_config = ConfigDict(populate_by_name=True)

    use_for_director: bool = Field(False, alias="useForDirector")
    mode: StoryConnectionMode = StoryConnectionMode.INDEPENDENT
    brief: str = Field("", max_length=1200)


class CrossSlotReferenceItemRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    asset_id: str = Field(alias="assetId", min_length=36, max_length=36)
    role: CrossSlotReferenceRole
    apply_to: CrossSlotReferenceTarget = Field(alias="applyTo")


class CrossSlotReferencesRequest(BaseModel):
    references: list[CrossSlotReferenceItemRequest] = Field(
        default_factory=list,
        max_length=12,
    )


class ShotNoteRequest(BaseModel):
    """非付费人工镜头备注，关联现有视频区间。"""

    model_config = ConfigDict(populate_by_name=True)

    asset_id: str = Field(alias="assetId", min_length=36, max_length=36)
    start_ms: int = Field(alias="startMs", ge=0)
    end_ms: int = Field(alias="endMs", gt=0)
    note: str = Field(min_length=2, max_length=2000)


class RetryStepRequest(BaseModel):
    """从失败步骤显式创建新attempt；理由必填以保留来源记录。"""

    model_config = ConfigDict(populate_by_name=True)

    reason: str = Field(min_length=4, max_length=500)
    allow_paid_generation: bool = Field(False, alias="allowPaidGeneration")
    acknowledge_downstream_replacement: bool = Field(
        False,
        alias="acknowledgeDownstreamReplacement",
    )
    acknowledge_duplicate_billing: bool = Field(
        False,
        alias="acknowledgeDuplicateBilling",
    )
    restart_from_beginning: bool = Field(False, alias="restartFromBeginning")


class RegenerateStepRequest(BaseModel):
    """为已经执行过的图片或整条视频节点创建非破坏性新attempt。"""

    model_config = ConfigDict(populate_by_name=True)

    reason: str = Field(min_length=4, max_length=500)
    prompt_override: str | None = Field(None, alias="promptOverride", max_length=20_000)
    allow_paid_generation: bool = Field(False, alias="allowPaidGeneration")
    acknowledge_downstream_replacement: bool = Field(
        False,
        alias="acknowledgeDownstreamReplacement",
    )


class RangeEditRequest(BaseModel):
    """时间轴选区AI重生成；区间单位为毫秒。"""

    model_config = ConfigDict(populate_by_name=True)

    start_ms: int = Field(alias="startMs", ge=0)
    end_ms: int = Field(alias="endMs", gt=0)
    boundary_mode: str = Field(
        "snap_to_shot",
        alias="boundaryMode",
        pattern=r"^(snap_to_shot|exact)$",
    )
    instruction: str = Field(min_length=4, max_length=2000)
    allow_paid_generation: bool = Field(False, alias="allowPaidGeneration")


class SelectVideoSequenceRequest(BaseModel):
    """选择已批准版本时，显式决定如何处理已确认结果卡。"""

    model_config = ConfigDict(populate_by_name=True)

    revoke_confirmed_outcome: bool = Field(False, alias="revokeConfirmedOutcome")
    keep_confirmed_outcome: bool = Field(False, alias="keepConfirmedOutcome")


class ReconcileStepRequest(BaseModel):
    """把人工确认的Ark Task ID绑定到submission_unknown视频步骤。"""

    model_config = ConfigDict(populate_by_name=True)

    provider_task_id: str = Field(alias="providerTaskId", min_length=1, max_length=200)


class ReplanRequest(BaseModel):
    """单时段局部重规划；原因必填且会注入导演修复Prompt。"""

    model_config = ConfigDict(populate_by_name=True)

    reason: str = Field(min_length=4, max_length=500)
    allow_paid_generation: bool = Field(False, alias="allowPaidGeneration")


class DeriveCropRequest(BaseModel):
    """从已批准Canon派生单视图或无主体裁片。"""

    model_config = ConfigDict(populate_by_name=True)

    role: str
    semantic_key: str = Field(alias="semanticKey")
    box: tuple[int, int, int, int] | None = None
    subject_free: bool = Field(False, alias="subjectFree")
    view: str | None = None


class PromptOverridesRequest(BaseModel):
    """主题创作台的Prompt编辑保存。"""

    model_config = ConfigDict(populate_by_name=True)

    overrides: dict[str, str] = Field(default_factory=dict)
    enabled: bool = False
