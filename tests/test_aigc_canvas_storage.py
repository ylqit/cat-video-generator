from __future__ import annotations

import uuid
from types import SimpleNamespace

from cat_video_generator.domain.aigc_canvas import CanvasNodeType
from cat_video_generator.infrastructure.db.aigc_canvas_repository import (
    _apply_canvas_node_archive_projection,
    _compile_storyboard_prompt_text,
    _preset_subject_targets_node,
)
from cat_video_generator.infrastructure.db.models import SCHEMA_NAME, Base, CanvasGraphNode
from cat_video_generator.infrastructure.db.session import ALEMBIC_HEAD


def test_canvas_v2_schema_contains_domain_truth_tables() -> None:
    expected = {
        "story_briefs",
        "subjects",
        "subject_revisions",
        "subject_references",
        "story_revisions",
        "story_scores",
        "scene_subject_bindings",
        "shot_beats",
        "shot_subject_states",
        "canvas_layouts",
        "provider_capabilities",
        "generation_attempts",
        "canvas_graph_nodes",
        "canvas_graph_edges",
        "canvas_events",
        "media_generation_batches",
        "video_edit_recipes",
        "video_edit_annotations",
        "video_edit_references",
        "subject_completion_runs",
        "node_generation_configs",
        "canvas_recovery_points",
        "canvas_node_archives",
    }

    assert expected <= {
        table.name
        for table in Base.metadata.tables.values()
        if table.schema == SCHEMA_NAME
    }


def test_workflow_steps_have_durable_lease_and_recovery_columns() -> None:
    columns = Base.metadata.tables[f"{SCHEMA_NAME}.workflow_steps"].columns

    assert {
        "lease_owner",
        "lease_expires_at",
        "heartbeat_at",
        "next_retry_at",
        "request_hash",
        "retry_chain_json",
    } <= set(columns.keys())


def test_canvas_v2_is_enabled_per_project() -> None:
    columns = Base.metadata.tables[f"{SCHEMA_NAME}.production_runs"].columns

    assert {
        "canvas_v2_enabled",
        "universal_canvas_enabled",
        "product_ad_template_enabled",
        "video_edit_v2_enabled",
    } <= set(columns.keys())


def test_prompt_records_have_full_audit_columns() -> None:
    columns = Base.metadata.tables[f"{SCHEMA_NAME}.prompt_records"].columns

    assert {
        "call_purpose",
        "node_id",
        "business_object_type",
        "business_object_id",
        "parent_prompt_id",
        "template_name",
        "template_version",
        "system_prompt",
        "user_prompt",
        "final_prompt",
        "provider_request_json",
        "provider_internal_transform",
        "input_snapshot_json",
        "raw_response_json",
        "structured_response_json",
        "accepted_response_json",
        "response_diff_json",
        "parameters_json",
        "token_usage_json",
        "cost_micros",
        "duration_ms",
        "status",
        "error_json",
        "input_hash",
        "output_hash",
        "completed_at",
    } <= set(columns.keys())


def test_canvas_v2_migration_is_current_head() -> None:
    assert ALEMBIC_HEAD == "0027_story_scene_prompts"


def test_storyboard_prompt_compiler_keeps_reference_layers_and_exclusions_separate() -> None:
    profile = SimpleNamespace(
        person_identity="固定儿童脸部身份",
        person_hair="固定短发",
        person_body="儿童全身比例",
        cat_identity="固定橘猫身份",
        style_positive_json=["细腻柔和的数字插画材质", "克制轮廓线"],
        style_negative_json=["摄影写实", "绿色污染"],
    )
    story = SimpleNamespace(
        episode_rules_json={
            "wardrobe": "黄色雨衣",
            "catBehaviorMode": "natural",
            "soundPlan": {"dialogue": False},
        }
    )
    scene = SimpleNamespace(
        title="雨后小院",
        source_text="孩子和猫咪发现一片发亮的叶子",
        context_note=(
            '{"continuity":{"location":"小院","weather":"雨后",'
            '"props":["木凳","发亮的叶子"]}}'
        ),
    )
    shot = {
        "order": 1,
        "title": "发现亮叶",
        "durationSeconds": 10,
        "action": "孩子蹲下，猫咪自然靠近叶子",
        "shotSize": "中景",
        "lighting": "雨后柔光",
        "camera": "缓慢推近",
        "soundEffect": "雨滴与猫咪脚步声",
        "temporalBeats": [
            {"label": "开始", "action": "孩子蹲下"},
            {"label": "变化", "action": "叶子反光"},
            {"label": "收尾", "action": "孩子和猫咪安静观看"},
        ],
    }
    bindings = [
        {
            "role": "identity",
            "purpose": "person_identity",
            "semanticKey": "person:headshot",
            "title": "儿童面部身份",
        },
        {
            "role": "environment",
            "purpose": "scene_look",
            "semanticKey": "scene:rainy-yard",
            "title": "雨后小院 Scene Look",
        },
    ]

    prompt = _compile_storyboard_prompt_text(
        profile=profile,
        story=story,
        scene=scene,
        shot=shot,
        reference_bindings=bindings,
        healing_recipe=True,
    )

    assert "【全局 Canon：身份与画风不变量】" in prompt
    assert "【本集造型与规则】" in prompt
    assert "【所属场景】" in prompt
    assert "【镜头画面】" in prompt
    assert "【视频运动与声音】" in prompt
    assert "【引用职责审计】" in prompt
    assert "【排除项】" in prompt
    assert '"role": "identity"' in prompt
    assert '"role": "environment"' in prompt
    assert "禁止串用其他场景素材" in prompt
    assert "猫咪出现人手、人形肢体" in prompt
    assert "无对白，不做口型" in prompt


def test_approved_story_scenes_and_compiled_prompts_have_version_pins() -> None:
    scene_columns = Base.metadata.tables[f"{SCHEMA_NAME}.scenes"].columns
    beat_columns = Base.metadata.tables[f"{SCHEMA_NAME}.shot_beats"].columns

    assert {"story_revision_id", "scene_key", "active", "stale_reason"} <= set(
        scene_columns.keys()
    )
    assert {"story_revision_id", "prompt_id", "temporal_beats_json"} <= set(
        beat_columns.keys()
    )


def test_assets_can_belong_to_a_universal_canvas_node() -> None:
    columns = Base.metadata.tables[f"{SCHEMA_NAME}.assets"].columns

    assert "canvas_node_id" in columns


def test_archived_canvas_projection_filters_nodes_edges_and_group_members() -> None:
    canvas = {
        "nodes": [
            {
                "id": "candidate",
                "type": "StoryCandidateNode",
                "data": {"status": "candidate"},
                "availableActions": [],
            },
            {
                "id": "brief",
                "type": "BriefNode",
                "data": {"status": "ready"},
                "availableActions": [],
            },
        ],
        "edges": [
            {"sourceNodeId": "candidate", "targetNodeId": "brief"},
        ],
        "groups": [{"memberNodeIds": ["candidate", "brief"]}],
    }

    _apply_canvas_node_archive_projection(canvas, {"candidate"})

    assert [node["id"] for node in canvas["nodes"]] == ["brief"]
    assert canvas["edges"] == []
    assert canvas["groups"][0]["memberNodeIds"] == ["brief"]
    archive_action = next(
        action
        for action in canvas["nodes"][0]["availableActions"]
        if action["key"] == "archive_node"
    )
    assert archive_action["enabled"] is False
    assert "六阶段" in archive_action["disabledReason"]


def test_unapproved_story_candidate_can_be_archived_but_approved_story_cannot() -> None:
    canvas = {
        "nodes": [
            {
                "id": "candidate",
                "type": "StoryCandidateNode",
                "data": {"status": "candidate"},
                "availableActions": [],
            },
            {
                "id": "approved",
                "type": "StoryCandidateNode",
                "data": {"status": "approved"},
                "availableActions": [],
            },
        ],
        "edges": [],
        "groups": [{"memberNodeIds": ["candidate", "approved"]}],
    }

    _apply_canvas_node_archive_projection(canvas, set())

    actions = {
        node["id"]: next(
            action for action in node["availableActions"] if action["key"] == "archive_node"
        )
        for node in canvas["nodes"]
    }
    assert actions["candidate"]["enabled"] is True
    assert actions["approved"]["enabled"] is False


def test_visual_preset_subject_edges_follow_character_design_slots() -> None:
    child = CanvasGraphNode(
        production_run_id=uuid.uuid4(),
        node_type=CanvasNodeType.CHARACTER_DESIGN.value,
        object_type="character_design_slot",
        status="pending",
        data_json={"slot": "child"},
    )
    cat = CanvasGraphNode(
        production_run_id=child.production_run_id,
        node_type=CanvasNodeType.CHARACTER_DESIGN.value,
        object_type="character_design_slot",
        status="pending",
        data_json={"slot": "cat"},
    )
    pair = CanvasGraphNode(
        production_run_id=child.production_run_id,
        node_type=CanvasNodeType.CHARACTER_DESIGN.value,
        object_type="character_design_slot",
        status="pending",
        data_json={"slot": "pair_scale"},
    )

    assert _preset_subject_targets_node("protagonist", child) is True
    assert _preset_subject_targets_node("co_protagonist", child) is False
    assert _preset_subject_targets_node("protagonist", cat) is False
    assert _preset_subject_targets_node("co_protagonist", cat) is True
    assert _preset_subject_targets_node("protagonist", pair) is True
    assert _preset_subject_targets_node("co_protagonist", pair) is True
