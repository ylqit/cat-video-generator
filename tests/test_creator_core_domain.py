from __future__ import annotations

import uuid

import pytest

from cat_video_generator.domain.creator_core import (
    CreatorReference,
    CreatorShotDraft,
    GenerationSnapshotDraft,
    assert_project_reference_authority,
    generation_input_hash,
    normalized_task_status,
)


def _reference(role: str, *, provider_eligible: bool = True) -> CreatorReference:
    return CreatorReference(
        assetId=uuid.uuid4(),
        role=role,
        providerEligible=provider_eligible,
        title=role,
    )


def test_project_requires_one_fixed_identity_and_style_board_authority() -> None:
    bindings = [
        _reference("child_identity"),
        _reference("cat_identity"),
        _reference("style_board"),
    ]

    assert_project_reference_authority(bindings)

    with pytest.raises(ValueError, match="child_identity"):
        assert_project_reference_authority([bindings[1], bindings[2]])
    with pytest.raises(ValueError, match="唯一"):
        assert_project_reference_authority([*bindings, _reference("child_identity")])


def test_style_source_is_never_provider_eligible() -> None:
    with pytest.raises(ValueError, match="style_source"):
        CreatorReference(
            assetId=uuid.uuid4(),
            role="style_source",
            providerEligible=True,
            title="叶片来源",
        )


def test_shot_contract_only_requires_title_direction_and_duration() -> None:
    shot = CreatorShotDraft(
        title="窗边纸星星",
        direction="孩子发现纸星星，猫咪把它推回，最后一起贴上玻璃。",
        durationSeconds=8,
    )

    assert shot.scene_label is None
    assert shot.reference_bindings == []


def test_snapshot_hash_is_order_sensitive_and_ignores_audit_metadata() -> None:
    first = _reference("child_identity")
    second = _reference("cat_identity")
    draft = GenerationSnapshotDraft(
        kind="video",
        promptText="生成一个八秒短片",
        orderedReferences=[first, second],
        providerConfig={"provider": "ark", "model": "video-model", "durationSeconds": 8},
    )
    same = draft.model_copy(update={"audit": {"revision": 99, "taskId": "ignored"}})
    reordered = draft.model_copy(update={"ordered_references": [second, first]})

    assert generation_input_hash(draft) == generation_input_hash(same)
    assert generation_input_hash(draft) != generation_input_hash(reordered)


def test_snapshot_rejects_two_assets_claiming_the_same_reference_role() -> None:
    first = _reference("environment")
    second = _reference("environment")
    draft = GenerationSnapshotDraft(
        kind="video",
        promptText="生成一个八秒短片",
        orderedReferences=[first, second],
        providerConfig={"provider": "ark", "model": "video-model"},
    )

    with pytest.raises(ValueError, match="参考职责"):
        generation_input_hash(draft)


def test_task_status_never_uses_stale_progress_to_override_primary_status() -> None:
    assert normalized_task_status("awaiting_review", {"providerStatus": "running"}) == (
        "awaiting_selection"
    )
    assert normalized_task_status("running", {"providerStatus": "queued"}) == "provider_running"
    assert normalized_task_status("submission_unknown", {"providerStatus": "failed"}) == "unknown"
