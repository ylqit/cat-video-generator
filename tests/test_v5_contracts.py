from __future__ import annotations

import pytest
from pydantic import ValidationError

from cat_video_generator.domain import contracts


def test_v5_contract_defaults_are_backward_compatible() -> None:
    scene = contracts.SceneDraft(title="出门前", sourceText="人物和猫咪准备出门。")
    shot = contracts.ShotCardDraft(title="猫咪等候", direction="1. 中景，猫咪在门边等候。")

    assert contracts.CURRENT_CONTRACT_VERSION == 5
    assert scene.story_mode is contracts.StoryMode.SINGLE
    assert scene.target_shot_count == 1
    assert scene.look_plan is None
    assert shot.inherit_project_references is True
    assert shot.use_scene_look is True


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
