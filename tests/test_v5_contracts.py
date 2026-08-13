from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from cat_video_generator.domain import contracts
from cat_video_generator.domain.rendering import MediaSource, build_shot_input_plan


def test_v5_contract_defaults_are_backward_compatible() -> None:
    scene = contracts.SceneDraft(title="出门前", sourceText="人物和猫咪准备出门。")
    shot = contracts.ShotCardDraft(title="猫咪等候", direction="1. 中景，猫咪在门边等候。")

    assert contracts.CURRENT_CONTRACT_VERSION == 5
    assert scene.story_mode is contracts.StoryMode.SINGLE
    assert scene.target_shot_count == 1
    assert scene.look_plan is None
    assert shot.inherit_project_references is True
    assert shot.scene_look_usage is contracts.SceneLookUsage.APPEARANCE_ONLY
    assert shot.use_scene_look is True


def test_legacy_scene_look_boolean_maps_to_authoritative_usage() -> None:
    disabled = contracts.ShotCardDraft(
        title="猫咪观察",
        direction="1. 中景，猫咪观察门边。",
        useSceneLook=False,
    )
    explicit = contracts.ShotCardDraft(
        title="猫咪观察",
        direction="1. 中景，猫咪观察门边。",
        sceneLookUsage="full_reference",
        useSceneLook=False,
    )

    assert disabled.scene_look_usage is contracts.SceneLookUsage.OFF
    assert disabled.use_scene_look is False
    assert explicit.scene_look_usage is contracts.SceneLookUsage.FULL_REFERENCE
    assert explicit.use_scene_look is True


def test_derive_anchor_requires_generate_anchor_mode() -> None:
    with pytest.raises(ValidationError, match="derive_anchor"):
        contracts.ShotCardDraft(
            title="猫咪观察",
            direction="1. 中景，猫咪观察门边。",
            sceneLookUsage="derive_anchor",
            anchorMode="text_only",
        )

    draft = contracts.ShotCardDraft(
        title="猫咪观察",
        direction="1. 中景，猫咪观察门边。",
        sceneLookUsage="derive_anchor",
        anchorMode="generate",
    )
    assert draft.scene_look_usage is contracts.SceneLookUsage.DERIVE_ANCHOR


@pytest.mark.parametrize(
    ("story_mode", "target_shot_count"),
    [
        ("single", 2),
        ("multi", 1),
        ("multi", 7),
    ],
)
def test_scene_mode_rejects_incompatible_shot_count(
    story_mode: str, target_shot_count: int
) -> None:
    with pytest.raises(ValidationError):
        contracts.SceneDraft(
            title="出门前",
            sourceText="人物和猫咪准备出门。",
            storyMode=story_mode,
            targetShotCount=target_shot_count,
        )


def test_multi_scene_accepts_user_selected_shot_count() -> None:
    scene = contracts.SceneDraft(
        title="一天的准备",
        sourceText="人物和猫咪依次收拾装备。",
        storyMode="multi",
        targetShotCount=6,
    )

    assert scene.story_mode is contracts.StoryMode.MULTI
    assert scene.target_shot_count == 6


def test_scene_look_plan_is_strict_and_serializes_with_public_aliases() -> None:
    plan = contracts.SceneLookPlan(
        personWardrobe="浅色外套",
        personAccessories="帆布包",
        catAppearance="保持 Canon 外观",
        keyProps="钓鱼竿、小水桶",
        imageRecommended=True,
        recommendationReason="服装和关键道具贯穿整个场景",
    )

    assert plan.model_dump(mode="json", by_alias=True) == {
        "personWardrobe": "浅色外套",
        "personAccessories": "帆布包",
        "catAppearance": "保持 Canon 外观",
        "keyProps": "钓鱼竿、小水桶",
        "environmentStyle": "outdoor",
        "personPose": "",
        "catPose": "",
        "composition": "",
        "additionalInstructions": "",
        "imageRecommended": True,
        "recommendationReason": "服装和关键道具贯穿整个场景",
    }
    with pytest.raises(ValidationError):
        contracts.SceneLookPlan(
            personWardrobe="",
            personAccessories="",
            catAppearance="",
            keyProps="",
            unexpected="not allowed",
        )


def test_visual_profile_defaults_are_editable_and_reference_purposes_are_strict() -> None:
    profile = contracts.VisualProfileDraft(
        personBody="保持五至七岁儿童体型",
        referenceBindings=[
            {
                "assetId": str(uuid.uuid4()),
                "purpose": "person_identity",
                "instruction": "锁定人物脸型",
            }
        ],
    )

    assert "五至七岁" in profile.person_body
    assert profile.reference_bindings[0].purpose is contracts.LookReferencePurpose.PERSON_IDENTITY
    with pytest.raises(ValidationError, match="只允许人物、猫咪和画风"):
        contracts.VisualProfileDraft(
            referenceBindings=[
                {"assetId": str(uuid.uuid4()), "purpose": "wardrobe"}
            ]
        )


def _image_source(index: int) -> MediaSource:
    return MediaSource(
        asset_id=uuid.uuid4(),
        semantic_key=f"reference:{index}",
        media_type="image",
        sha256=f"{index:064x}",
        metadata={},
    )


@pytest.mark.parametrize("resolution", ["480p", "720p"])
def test_v5_video_input_contract_allows_nine_images_total(resolution: str) -> None:
    anchor = _image_source(1)
    references = tuple(_image_source(index) for index in range(2, 10))

    plan = build_shot_input_plan(
        resolution=resolution,
        duration_seconds=10,
        anchor=anchor,
        references=references,
    )

    assert len(plan.bindings) == 9
    assert [binding.ordinal for binding in plan.bindings] == list(range(1, 10))


def test_v5_video_input_contract_rejects_tenth_image() -> None:
    with pytest.raises(ValueError, match="最多允许8项附加参考素材"):
        build_shot_input_plan(
            resolution="480p",
            duration_seconds=10,
            anchor=_image_source(1),
            references=tuple(_image_source(index) for index in range(2, 11)),
        )
