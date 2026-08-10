"""写操作HTTP端点的请求模型与共享常量。

模型与api_write.py分离，既保持Router模块低于架构体量上限，也让前端契约
集中可读。这里只做字段校验；业务校验仍在端点与Application Service。
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from ..domain.contracts import RunCreativeControls, Slot
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
DEFAULT_PLANNING_CONTEXT = "根据日期、天气和角色习惯设计自然的一天。"


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


class PlanRequest(BaseModel):
    """触发全天规划的请求体；付费许可缺省为拒绝。"""

    model_config = ConfigDict(populate_by_name=True)

    target_date: date = Field(alias="targetDate")
    planning_context: str | None = Field(None, alias="planningContext")
    creative_profile: CreativeProfileRequest | None = Field(
        None,
        alias="creativeProfile",
    )
    candidate_count: int | None = Field(None, alias="candidateCount", ge=1, le=5)
    allow_paid_generation: bool = Field(False, alias="allowPaidGeneration")
    pipeline_settings: dict | None = Field(None, alias="pipelineSettings")
    story_mode: str = Field(
        "auto",
        alias="storyMode",
        pattern=r"^(auto|create|expand)$",
    )
    creative_controls: RunCreativeControls | None = Field(
        None,
        alias="creativeControls",
    )


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


class RetryStepRequest(BaseModel):
    """从失败步骤显式创建新attempt；理由必填以保留来源记录。"""

    model_config = ConfigDict(populate_by_name=True)

    reason: str = Field(min_length=4, max_length=500)
    allow_paid_generation: bool = Field(False, alias="allowPaidGeneration")
    acknowledge_duplicate_billing: bool = Field(
        False,
        alias="acknowledgeDuplicateBilling",
    )


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
