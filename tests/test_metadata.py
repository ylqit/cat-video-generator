from __future__ import annotations

from cat_video_generator.migration import expected_alembic_head
from cat_video_generator.models import SCHEMA_NAME, Base


def test_all_tables_are_isolated_in_cat_video_schema() -> None:
    expected = {
        "continuity_events",
        "daily_life_packs",
        "daily_slots",
        "delivery_items",
        "delivery_packages",
        "episode_variants",
        "generation_jobs",
        "media_assets",
        "reference_assets",
        "review_decisions",
        "slot_retry_events",
    }

    assert {table.name for table in Base.metadata.tables.values()} == expected
    assert {
        table.schema for table in Base.metadata.tables.values()
    } == {SCHEMA_NAME}


def test_slot_retry_migration_is_the_only_head() -> None:
    assert expected_alembic_head() == "0003_slot_retry_events"


def test_selected_variant_constraint_is_deferred_until_both_tables_exist() -> None:
    slot_table = Base.metadata.tables[f"{SCHEMA_NAME}.daily_slots"]
    selected_constraint = next(
        constraint
        for constraint in slot_table.foreign_key_constraints
        if constraint.name == "fk_daily_slots_selected_variant_same_slot"
    )

    assert selected_constraint.use_alter is True
    assert [column.name for column in selected_constraint.columns] == [
        "id",
        "selected_variant_id",
    ]


def test_provider_task_and_delivery_revision_indexes_match_runtime_queries() -> None:
    job_table = Base.metadata.tables[f"{SCHEMA_NAME}.generation_jobs"]
    provider_task_index = next(
        index
        for index in job_table.indexes
        if index.name == "uq_generation_jobs_provider_task"
    )
    assert provider_task_index.unique is True
    assert [column.name for column in provider_task_index.columns] == [
        "provider_task_id"
    ]
    assert provider_task_index.dialect_options["postgresql"]["where"] is not None

    delivery_table = Base.metadata.tables[f"{SCHEMA_NAME}.delivery_packages"]
    revision_index = next(
        index
        for index in delivery_table.indexes
        if index.name == "ix_delivery_packages_revision"
    )
    assert [column.name for column in revision_index.columns] == [
        "delivery_revision"
    ]
