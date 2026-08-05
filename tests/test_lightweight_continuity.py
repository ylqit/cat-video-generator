"""轻量连续性只拒绝真实引用错误，不模拟动作过程。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cat_video_generator.domain.continuity import SceneContinuity, validate_continuity


def _continuity(*, inside_target: str, include_container: bool) -> dict:
    entities = [
        {
            "id": "person",
            "name": "中性儿童",
            "kind": "person",
            "entity_key": "person",
            "start_state": {
                "present": True,
                "placement": {"kind": "anchor", "target_id": "ground"},
            },
            "end_state": {
                "present": True,
                "placement": {"kind": "anchor", "target_id": "ground"},
            },
            "lifecycle": "persist",
            "form_key": "neutral-child",
        },
        {
            "id": "cat",
            "name": "灰白猫",
            "kind": "cat",
            "entity_key": "cat",
            "start_state": {
                "present": True,
                "placement": {"kind": "anchor", "target_id": "ground"},
            },
            "end_state": {
                "present": True,
                "placement": {"kind": "anchor", "target_id": "ground"},
            },
            "lifecycle": "persist",
            "form_key": "gray-white-cat",
        },
    ]
    if include_container:
        entities.append(
            {
                "id": "box",
                "name": "纸盒",
                "kind": "prop",
                "entity_key": "paper-box",
                "start_state": {
                    "present": True,
                    "placement": {"kind": "anchor", "target_id": "bench"},
                },
                "end_state": {
                    "present": True,
                    "placement": {"kind": "anchor", "target_id": "bench"},
                },
                "lifecycle": "persist",
                "form_key": "paper-box",
            }
        )
    entities.append(
        {
            "id": "candy_wrapper",
            "name": "糖纸",
            "kind": "prop",
            "entity_key": "candy-wrapper",
            "start_state": {
                "present": True,
                "placement": {"kind": "inside", "target_id": inside_target},
            },
            "end_state": {
                "present": True,
                "placement": {"kind": "held_by", "target_id": "person"},
            },
            "lifecycle": "persist",
            "form_key": "red-candy-wrapper",
            "change_reason": "孩子从缝隙里捡起同一张糖纸",
        }
    )
    return {
        "anchors": [
            {"id": "ground", "name": "沙地", "type": "ground"},
            {"id": "bench", "name": "长椅缝隙", "type": "seat"},
        ],
        "entities": entities,
    }


def test_inside_scene_anchor_is_valid() -> None:
    continuity = SceneContinuity.model_validate(
        _continuity(inside_target="bench", include_container=False)
    )

    assert validate_continuity(continuity).valid


def test_inside_container_entity_is_valid() -> None:
    continuity = SceneContinuity.model_validate(
        _continuity(inside_target="box", include_container=True)
    )

    assert validate_continuity(continuity).valid


def test_inside_truly_unknown_target_is_rejected() -> None:
    with pytest.raises(ValidationError, match="未知容器或锚点missing_target"):
        SceneContinuity.model_validate(
            _continuity(inside_target="missing_target", include_container=False)
        )
