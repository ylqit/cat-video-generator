from __future__ import annotations

from cat_video_generator.infrastructure.db.models import SCHEMA_NAME, Base
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
    assert ALEMBIC_HEAD == "0024_durable_task_events"


def test_assets_can_belong_to_a_universal_canvas_node() -> None:
    columns = Base.metadata.tables[f"{SCHEMA_NAME}.assets"].columns

    assert "canvas_node_id" in columns
