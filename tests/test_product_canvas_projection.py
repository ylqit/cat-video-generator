from __future__ import annotations

import uuid

import pytest

from cat_video_generator.domain.aigc_canvas import CanvasNodeType
from cat_video_generator.infrastructure.db.aigc_canvas_repository import (
    _canvas_json,
    _canvas_node_contract,
    _filmstrip_identity,
    _graph_node_json,
)
from cat_video_generator.infrastructure.db.models import Asset, CanvasGraphNode


def test_fresh_product_canvas_omits_narrative_projection_nodes() -> None:
    canvas = _canvas_json(
        uuid.uuid4(),
        layout=None,
        brief=None,
        subjects=[],
        stories=[],
        scenes=[],
        beats=[],
        session=object(),  # type: ignore[arg-type]
        enabled=True,
        include_narrative_projection=False,
    )

    assert not {
        "StoryPlannerNode",
        "ApprovalGateNode",
        "StoryboardDirectorNode",
    } & {node["type"] for node in canvas["nodes"]}


def test_video_asset_projection_exposes_real_actions_and_playable_content() -> None:
    project_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    node = CanvasGraphNode(
        id=uuid.uuid4(),
        production_run_id=project_id,
        node_type="VideoAssetNode",
        object_type="asset",
        object_id=asset_id,
        data_json={"title": "成品视频"},
    )

    projected = _graph_node_json(node)

    assert projected["data"]["contentUrl"] == f"/api/v1/assets/{asset_id}/content"
    actions = {item["key"]: item for item in projected["availableActions"]}
    assert actions["segment_reshoot"]["enabled"] is True
    assert actions["upscale"]["enabled"] is False
    assert "disabledReason" in actions["upscale"]


@pytest.mark.parametrize("node_type", list(CanvasNodeType))
def test_every_canvas_node_type_has_an_explicit_click_contract(
    node_type: CanvasNodeType,
) -> None:
    contract = _canvas_node_contract(node_type.value, "draft", {})

    assert contract["availableActions"]
    for action in contract["availableActions"]:
        assert action["key"]
        assert action["label"]
        assert action["execution"] in {"client", "local_worker", "provider", "unavailable"}
        if action["enabled"] is False:
            assert action.get("disabledReason")


def test_storyboard_director_declares_three_distinct_creation_flows() -> None:
    contract = _canvas_node_contract("StoryboardDirectorNode", "draft", {})

    assert [action["key"] for action in contract["availableActions"]] == [
        "storyboard_from_story",
        "storyboard_from_characters",
        "storyboard_manual",
        "review_storyboard",
    ]
    assert contract["availableActions"][-1]["enabled"] is False
    assert contract["availableActions"][-1]["disabledReason"] == "请先生成并保存镜头表"

    reviewable = _canvas_node_contract(
        "StoryboardDirectorNode",
        "awaiting_review",
        {"shotCount": 2, "storyboardApproved": False},
    )
    assert reviewable["availableActions"][-1]["enabled"] is True
    assert "disabledReason" not in reviewable["availableActions"][-1]


def test_filmstrip_timestamps_keep_a_decodable_tail_margin() -> None:
    asset = Asset(
        id=uuid.uuid4(),
        production_run_id=uuid.uuid4(),
        scene_id=None,
        shot_card_id=None,
        producing_step_id=None,
        canvas_node_id=None,
        role="final_video",
        semantic_key=None,
        scope="project",
        status="approved",
        media_type="video/mp4",
        storage_key="video.mp4",
        sha256="a" * 64,
        byte_size=1,
        metadata_json={"durationMs": 24_000},
    )

    timestamps, _filmstrip_key, _idempotency_key = _filmstrip_identity(asset, 12)

    assert timestamps[0] == 0
    assert timestamps[-1] == 23_900
    assert timestamps == tuple(sorted(set(timestamps)))
