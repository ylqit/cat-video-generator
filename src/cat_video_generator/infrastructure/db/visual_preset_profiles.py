"""Persistence helpers for versioned Canon visual presets.

This module owns the shared database boundary used by recipe instantiation and
the public material-library API.  It deliberately reuses existing Asset and
VisualProfileRevision rows instead of creating another asset model.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...domain.production_recipes import (
    CANON_V3_PROFILE_ID,
    CANON_V3_STYLE_NEGATIVE,
    CANON_V3_STYLE_POSITIVE,
    VisualPresetKey,
    canon_reference_keys,
)
from .models import (
    Asset,
    ProductionRun,
    Subject,
    SubjectReference,
    SubjectRevision,
    VisualProfileRevision,
)
from .repositories import RecordNotFoundError, WorkflowConflictError

CANON_V3_REQUIRED_KEYS = canon_reference_keys(CANON_V3_PROFILE_ID, "indoor")

_REFERENCE_TITLES = {
    "person:headshot": "儿童面部",
    "person:fullbody": "儿童全身比例",
    "cat:front": "猫咪正面",
    "cat:side": "猫咪侧面",
    "style:line_texture": "线条材质",
}


def canon_v3_subject_documents() -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the two versioned narrative subjects carried by the v3 preset."""

    return (
        {
            "name": "固定儿童",
            "kind": "person",
            "role": "protagonist",
            "identityAnchors": [
                "脸部、年龄、五官与短发由 Canon-v3 人物参考锁定",
                "全片保持同一儿童身体比例，不成人化",
            ],
            "immutableTraits": ["固定五官", "固定短发", "儿童身体比例"],
            "relationshipNotes": "与固定猫咪相互信任，以轻微动作和目光完成日常互动。",
            "dramaticFunction": "主动完成一个低压力的日常小行动。",
            "visualRisks": ["年龄漂移", "脸部漂移", "身体比例成人化", "服装跨镜变化"],
            "references": [
                {
                    "semanticKey": "person:headshot",
                    "semanticRole": "front",
                    "instruction": "锁定脸型、五官、年龄与发型。",
                },
                {
                    "semanticKey": "person:fullbody",
                    "semanticRole": "full_body",
                    "instruction": "锁定儿童身体比例与整体轮廓。",
                },
            ],
        },
        {
            "name": "固定猫咪",
            "kind": "animal",
            "role": "co_protagonist",
            "identityAnchors": [
                "脸部、毛色分区、体型与环纹尾巴由 Canon-v3 猫咪参考锁定",
                "保持猫科身体结构与四足姿态",
            ],
            "immutableTraits": ["固定脸部", "固定毛色分区", "固定环纹尾巴", "四足猫科结构"],
            "relationshipNotes": "用耳朵、尾巴、步态和靠近动作回应儿童。",
            "dramaticFunction": "参与小变化并促成温暖收尾。",
            "visualRisks": ["毛色漂移", "尾巴纹路漂移", "人形肢体", "多余肢体"],
            "references": [
                {
                    "semanticKey": "cat:front",
                    "semanticRole": "front",
                    "instruction": "锁定猫咪脸部、眼睛与正面毛色分区。",
                },
                {
                    "semanticKey": "cat:side",
                    "semanticRole": "side",
                    "instruction": "锁定猫咪体型、侧面虎斑与环纹尾巴。",
                },
            ],
        },
    )


def ensure_canon_v3_subjects(
    session: Session,
    *,
    project_id: uuid.UUID,
    assets_by_key: dict[str, Asset],
) -> dict[str, Subject]:
    """Reuse an exact approved evidence binding or create a new subject revision."""

    existing_subjects = list(
        session.scalars(
            select(Subject)
            .where(Subject.production_run_id == project_id)
            .order_by(Subject.created_at, Subject.id)
        )
    )
    resolved: dict[str, Subject] = {}
    for document in canon_v3_subject_documents():
        expected_asset_ids = {
            assets_by_key[reference["semanticKey"]].id for reference in document["references"]
        }
        subject = next(
            (
                candidate
                for candidate in existing_subjects
                if candidate.kind == document["kind"]
                and candidate.role == document["role"]
                and candidate.current_revision_id is not None
                and _approved_subject_asset_ids(
                    session, candidate.current_revision_id
                )
                == expected_asset_ids
            ),
            None,
        )
        if subject is None:
            subject = _create_canon_v3_subject(
                session,
                project_id=project_id,
                document=document,
                assets_by_key=assets_by_key,
            )
            existing_subjects.append(subject)
        resolved[str(document["role"])] = subject
    return resolved


def _approved_subject_asset_ids(
    session: Session,
    revision_id: uuid.UUID,
) -> set[uuid.UUID]:
    revision = session.get(SubjectRevision, revision_id)
    if revision is None or revision.approval_status != "approved":
        return set()
    return set(
        session.scalars(
            select(SubjectReference.asset_id).where(
                SubjectReference.subject_revision_id == revision_id
            )
        )
    )


def _create_canon_v3_subject(
    session: Session,
    *,
    project_id: uuid.UUID,
    document: dict[str, Any],
    assets_by_key: dict[str, Asset],
) -> Subject:
    subject = Subject(
        id=uuid.uuid4(),
        production_run_id=project_id,
        kind=str(document["kind"]),
        role=str(document["role"]),
        status="ready",
    )
    session.add(subject)
    session.flush()
    references = [
        {
            "assetId": str(assets_by_key[reference["semanticKey"]].id),
            "semanticRole": reference["semanticRole"],
            "instruction": reference["instruction"],
        }
        for reference in document["references"]
    ]
    revision_document = {
        **{key: value for key, value in document.items() if key != "references"},
        "references": references,
    }
    revision = SubjectRevision(
        id=uuid.uuid4(),
        subject_id=subject.id,
        revision=1,
        name=str(document["name"]),
        identity_anchors_json=list(document["identityAnchors"]),
        immutable_traits_json=list(document["immutableTraits"]),
        relationship_notes=str(document["relationshipNotes"]),
        dramatic_function=str(document["dramaticFunction"]),
        visual_risks_json=list(document["visualRisks"]),
        revision_hash=hashlib.sha256(
            json.dumps(
                revision_document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        approval_status="approved",
    )
    session.add(revision)
    session.flush()
    subject.current_revision_id = revision.id
    for order, reference in enumerate(document["references"], 1):
        session.add(
            SubjectReference(
                id=uuid.uuid4(),
                subject_revision_id=revision.id,
                asset_id=assets_by_key[reference["semanticKey"]].id,
                semantic_role=str(reference["semanticRole"]),
                sort_order=order,
                instruction=str(reference["instruction"]),
            )
        )
    return subject


def load_canon_assets(
    session: Session,
    keys: tuple[str, ...],
) -> dict[str, Asset]:
    assets = list(
        session.scalars(
            select(Asset).where(
                Asset.scope == "canon",
                Asset.status.in_(("ready", "approved")),
                Asset.semantic_key.in_(keys),
            )
        )
    )
    by_key = {asset.semantic_key: asset for asset in assets if asset.semantic_key}
    missing = [key for key in keys if key not in by_key]
    if missing:
        raise WorkflowConflictError("缺少 Canon 参考：" + ", ".join(missing))
    return by_key


def visual_reference_json(asset: Asset, *, required: bool = True) -> dict[str, Any]:
    semantic_key = asset.semantic_key or ""
    content_url = f"/api/v1/assets/{asset.id}/content"
    return {
        "assetId": str(asset.id),
        "semanticKey": semantic_key,
        "title": _REFERENCE_TITLES.get(semantic_key, semantic_key or "视觉参考"),
        "contentUrl": content_url,
        "thumbnailUrl": content_url,
        "approvalStatus": asset.status,
        "sha256": asset.sha256,
        "required": required,
    }


def visual_profile_bindings(
    assets_by_key: dict[str, Asset],
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    return [
        {
            "assetId": str(assets_by_key[key].id),
            "purpose": _reference_purpose(key),
            "instruction": (
                "只锁定线条、材质和光线，不复制叶片、露珠、绿色配色或微距构图。"
                if key == "style:line_texture"
                else "固定身份视觉证据，不允许普通参考替换。"
            ),
        }
        for key in keys
    ]


def generation_reference_bindings(
    assets_by_key: dict[str, Asset],
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    return [
        {
            "assetId": str(assets_by_key[key].id),
            "usage": "generation_reference",
            "role": "style" if key.startswith("style:") else "identity",
            "applyTo": "anchor",
        }
        for key in keys
    ]


def ensure_canon_v3_visual_profile(
    session: Session,
    *,
    project_id: uuid.UUID,
    assets_by_key: dict[str, Asset] | None = None,
) -> VisualProfileRevision:
    resolved_assets = assets_by_key or load_canon_assets(session, CANON_V3_REQUIRED_KEYS)
    snapshot = [
        {
            **visual_reference_json(resolved_assets[key]),
            "role": "style" if key.startswith("style:") else "identity",
        }
        for key in CANON_V3_REQUIRED_KEYS
    ]
    bindings = visual_profile_bindings(resolved_assets, CANON_V3_REQUIRED_KEYS)
    profile_document = {
        "canonProfileId": CANON_V3_PROFILE_ID,
        "bindings": bindings,
        "references": snapshot,
        "stylePositive": list(CANON_V3_STYLE_POSITIVE),
        "styleExcluded": list(CANON_V3_STYLE_NEGATIVE),
    }
    profile_hash = hashlib.sha256(
        json.dumps(profile_document, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    profile = session.scalar(
        select(VisualProfileRevision).where(
            VisualProfileRevision.production_run_id == project_id,
            VisualProfileRevision.profile_hash == profile_hash,
        )
    )
    if profile is None:
        revision = (
            int(
                session.scalar(
                    select(func.coalesce(func.max(VisualProfileRevision.revision), 0)).where(
                        VisualProfileRevision.production_run_id == project_id
                    )
                )
                or 0
            )
            + 1
        )
        profile = VisualProfileRevision(
            id=uuid.uuid4(),
            production_run_id=project_id,
            revision=revision,
            profile_hash=profile_hash,
            source_profile_id=CANON_V3_PROFILE_ID,
            person_identity="固定儿童脸型、五官、年龄感与身份特征",
            person_hair="固定儿童短发轮廓、发色与发际线",
            person_body="固定儿童全身比例与非成人化身体结构",
            cat_identity="固定猫咪脸部、毛色分区、体型与环纹尾巴",
            style_positive_json=list(CANON_V3_STYLE_POSITIVE),
            style_negative_json=list(CANON_V3_STYLE_NEGATIVE),
            reference_bindings_json=bindings,
            reference_snapshot_json=snapshot,
        )
        session.add(profile)
        session.flush()
    project = session.get(ProductionRun, project_id)
    if project is None:
        raise RecordNotFoundError(f"ProductionRun not found: {project_id}")
    project.current_visual_profile_revision_id = profile.id
    project.default_reference_bindings_json = generation_reference_bindings(
        resolved_assets, CANON_V3_REQUIRED_KEYS
    )
    return profile


def visual_preset_profile_json(session: Session) -> dict[str, Any]:
    assets_by_key = load_canon_assets(session, CANON_V3_REQUIRED_KEYS)
    slots = []
    for key in CANON_V3_REQUIRED_KEYS:
        role = (
            "style" if key.startswith("style:") else ("cat" if key.startswith("cat:") else "person")
        )
        slots.append(
            {
                **visual_reference_json(assets_by_key[key]),
                "role": role,
                "purpose": "style" if role == "style" else "identity",
                "instruction": (
                    "只提取线条、材质与光线；禁止复制叶片、露珠、绿色配色和微距构图。"
                    if key == "style:line_texture"
                    else "作为固定身份视觉证据，不能被普通参考图替换。"
                ),
            }
        )
    return {
        "key": VisualPresetKey.HEALING_CHILD_CAT_LINE_TEXTURE.value,
        "canonProfileId": CANON_V3_PROFILE_ID,
        "title": "一人一猫 · 线条材质",
        "description": "固定儿童、固定猫咪与统一线条材质的治愈短片视觉预设",
        "version": 3,
        "ready": True,
        "slots": slots,
    }


def episode_visual_profile_json(
    profile: VisualProfileRevision,
) -> dict[str, Any]:
    return {
        "id": str(profile.id),
        "projectId": str(profile.production_run_id),
        "revision": profile.revision,
        "sourceProfileId": profile.source_profile_id,
        "personIdentity": profile.person_identity,
        "personHair": profile.person_hair,
        "personBody": profile.person_body,
        "catIdentity": profile.cat_identity,
        "stylePositive": profile.style_positive_json,
        "styleNegative": profile.style_negative_json,
        "referenceBindings": profile.reference_bindings_json,
        "references": profile.reference_snapshot_json,
        "lockedSemanticKeys": list(CANON_V3_REQUIRED_KEYS),
        "createdAt": profile.created_at.isoformat(),
    }


def _reference_purpose(key: str) -> str:
    if key == "person:headshot":
        return "person_identity"
    if key == "person:fullbody":
        return "person_body"
    if key.startswith("cat:"):
        return "cat_identity"
    if key.startswith("style:"):
        return "style"
    raise ValueError(f"不支持的视觉档案语义键：{key}")
