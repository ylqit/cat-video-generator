"""Minimal creator workflow contracts.

This module deliberately separates flexible creative content from strict paid
execution inputs. Historical orchestration models are intentionally kept out
of this boundary.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CreatorReferenceRole = Literal[
    "child_identity",
    "cat_identity",
    "style_board",
    "child_appearance",
    "cat_appearance",
    "pair_scale",
    "environment",
    "prop",
    "style_source",
]
GenerationKind = Literal["story_text", "image", "video", "video_edit", "composition"]


class CreatorModel(BaseModel):
    """Creator payloads accept harmless extra creative fields at the boundary."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class CreativeTextCandidate(CreatorModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=40_000)
    summary: str | None = Field(default=None, max_length=2_000)


class CreatorReference(CreatorModel):
    asset_id: uuid.UUID = Field(alias="assetId")
    role: CreatorReferenceRole
    provider_eligible: bool = Field(alias="providerEligible")
    title: str = Field(min_length=1, max_length=200)
    instruction: str = Field(default="", max_length=2_000)

    @field_validator("provider_eligible")
    @classmethod
    def reject_provider_eligible_style_source(cls, provider_eligible: bool, info: Any) -> bool:
        if info.data.get("role") == "style_source" and provider_eligible:
            raise ValueError("style_source 只允许用于画风提炼，不能提交 Provider")
        return provider_eligible


class CreatorShotDraft(CreatorModel):
    id: uuid.UUID | None = None
    version: int | None = Field(default=None, ge=1)
    title: str = Field(min_length=1, max_length=160)
    direction: str = Field(min_length=1, max_length=20_000)
    duration_seconds: int = Field(alias="durationSeconds", ge=1, le=60)
    scene_label: str | None = Field(alias="sceneLabel", default=None, max_length=160)
    reference_bindings: list[CreatorReference] = Field(
        alias="referenceBindings", default_factory=list, max_length=12
    )
    prompt_draft: str | None = Field(alias="promptDraft", default=None, max_length=30_000)


class GenerationSnapshotDraft(CreatorModel):
    kind: GenerationKind
    prompt_text: str = Field(alias="promptText", min_length=1, max_length=40_000)
    ordered_references: list[CreatorReference] = Field(
        alias="orderedReferences", default_factory=list, max_length=14
    )
    provider_config: dict[str, Any] = Field(alias="providerConfig")


def assert_project_reference_authority(bindings: list[CreatorReference]) -> None:
    """Require exactly one immutable authority for every project Canon role."""

    required = ("child_identity", "cat_identity", "style_board")
    for role in required:
        matching = [item for item in bindings if item.role == role]
        if not matching:
            raise ValueError(f"项目缺少必需的 {role} 权威参考")
        if len(matching) != 1:
            raise ValueError(f"{role} 必须且只能有一个唯一权威参考")
        if not matching[0].provider_eligible:
            raise ValueError(f"{role} 权威参考必须可提交 Provider")

    unique_roles = {
        "child_identity",
        "cat_identity",
        "style_board",
        "child_appearance",
        "cat_appearance",
        "pair_scale",
        "environment",
        "prop",
    }
    for role in unique_roles:
        if sum(item.role == role for item in bindings) > 1:
            raise ValueError(f"{role} 同一层级只能绑定一个权威参考")


def assert_provider_references(bindings: list[CreatorReference]) -> None:
    """Validate the exact ordered reference manifest shown at paid confirmation."""

    if any(item.role == "style_source" for item in bindings):
        raise ValueError("style_source 不能进入日常 Provider 输入")
    if any(not item.provider_eligible for item in bindings):
        raise ValueError("参考清单包含不可提交 Provider 的素材")
    asset_ids = [item.asset_id for item in bindings]
    if len(asset_ids) != len(set(asset_ids)):
        raise ValueError("同一素材不能在一个生成快照中重复")
    roles = [item.role for item in bindings]
    if len(roles) != len(set(roles)):
        raise ValueError("同一参考职责在一个生成快照中只能出现一次")


def generation_input_hash(draft: GenerationSnapshotDraft) -> str:
    """Hash only Provider-visible creative inputs; audit metadata stays separate."""

    assert_provider_references(draft.ordered_references)
    payload = {
        "kind": draft.kind,
        "promptText": draft.prompt_text,
        "orderedReferences": [
            item.model_dump(by_alias=True, mode="json") for item in draft.ordered_references
        ],
        "providerConfig": draft.provider_config,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalized_task_status(status: str, _progress: dict[str, Any] | None = None) -> str:
    """Expose one task truth; progress snapshots never override the durable status."""

    mapping = {
        "pending": "local_queued",
        "queued": "provider_queued",
        "submitting": "submitting",
        "running": "provider_running",
        "awaiting_review": "awaiting_selection",
        "succeeded": "succeeded",
        "failed": "failed",
        "expired": "failed",
        "cancelled": "cancelled",
        "submission_unknown": "unknown",
        "cancelling": "unknown",
        "cancellation_unknown": "unknown",
    }
    return mapping.get(status, "unknown")
