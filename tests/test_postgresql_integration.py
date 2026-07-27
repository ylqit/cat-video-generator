from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Barrier

import pytest
from PIL import Image
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from alembic import command
from cat_video_generator.config import DatabaseOperation, DatabaseSettings
from cat_video_generator.content_service import (
    approve_daily_life_pack,
    import_daily_life_pack,
    import_reference_asset,
    retry_failed_slot,
    review_reference_asset,
)
from cat_video_generator.db import create_database_engine, create_session_factory
from cat_video_generator.doctor import inspect_database
from cat_video_generator.migration import (
    alembic_config,
    expected_alembic_head,
    upgrade_database,
)
from cat_video_generator.models import (
    ContinuityEvent,
    DailyLifePack,
    DailySlot,
    DeliveryItem,
    DeliveryPackage,
    EpisodeVariant,
    GenerationJob,
    MediaAsset,
    SlotRetryEvent,
)
from cat_video_generator.remote_validation import validate_remote_database
from cat_video_generator.repository import (
    claim_next_life_pack,
    get_or_create_generation_job,
)
from cat_video_generator.runtime_validation import validate_runtime_database

pytestmark = pytest.mark.postgres
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def add_pack(
    session,
    pack_id: str,
    *,
    status: str = "approved",
    pack_date: date = date(2026, 7, 27),
) -> DailyLifePack:
    pack = DailyLifePack(
        life_pack_id=pack_id,
        date=pack_date,
        plan_revision=1,
        content_hash="0" * 64,
        source_json={},
        day_context_json={"contextType": "travel"},
        status=status,
        is_degraded=False,
    )
    session.add(pack)
    session.flush()
    return pack


def add_slot(
    session,
    pack: DailyLifePack,
    slot: str,
    sort_order: int,
) -> DailySlot:
    record = DailySlot(
        daily_life_pack_id=pack.id,
        slot=slot,
        sort_order=sort_order,
        status="planned",
    )
    session.add(record)
    session.flush()
    return record


def add_variant(
    session,
    slot: DailySlot,
    episode_id: str,
) -> EpisodeVariant:
    variant = EpisodeVariant(
        daily_slot_id=slot.id,
        episode_id=episode_id,
        role="primary",
        episode_spec_json={"episodeId": episode_id},
        active_render_revision=1,
        status="planned",
    )
    session.add(variant)
    session.flush()
    return variant


def test_migration_schema_and_doctor(
    migrated_database: DatabaseSettings,
) -> None:
    engine = create_database_engine(migrated_database, DatabaseOperation.TEST)
    try:
        report = inspect_database(
            engine,
            migrated_database,
            expected_revision=expected_alembic_head(),
        )
        table_names = set(inspect(engine).get_table_names(schema="cat_video"))
    finally:
        engine.dispose()

    assert report.ready_for_runtime is True
    assert report.pool_healthy is True
    assert report.alembic_revision == "0003_slot_retry_events"
    assert table_names == {
        "alembic_version",
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


def test_upgrade_is_idempotent_and_runtime_validation_cleans_its_rows(
    migrated_database: DatabaseSettings,
) -> None:
    assert upgrade_database(migrated_database) == expected_alembic_head()
    engine = create_database_engine(migrated_database, DatabaseOperation.TEST)
    try:
        report = validate_runtime_database(engine, migrated_database)
    finally:
        engine.dispose()

    assert report.ok is True
    assert report.alembic_revision == expected_alembic_head()
    assert report.schema == "cat_video"
    assert all(report.checks.values())


def test_slot_order_and_selected_variant_constraints(
    migrated_database: DatabaseSettings,
) -> None:
    engine = create_database_engine(migrated_database, DatabaseOperation.TEST)
    factory = create_session_factory(engine)
    try:
        with factory.begin() as session:
            pack = add_pack(session, "life-constraint-test")
            morning = add_slot(session, pack, "morning", 1)
            noon = add_slot(session, pack, "noon", 2)
            morning_variant = add_variant(session, morning, "ep-morning")
            noon_variant = add_variant(session, noon, "ep-noon")
            morning.selected_variant_id = morning_variant.id

        with pytest.raises(IntegrityError), factory.begin() as session:
            pack = session.execute(
                select(DailyLifePack).where(
                    DailyLifePack.life_pack_id == "life-constraint-test"
                )
            ).scalar_one()
            session.add(
                DailySlot(
                    daily_life_pack_id=pack.id,
                    slot="morning",
                    sort_order=2,
                    status="planned",
                )
            )

        with pytest.raises(IntegrityError), factory.begin() as session:
            morning = session.execute(
                select(DailySlot).where(DailySlot.slot == "morning")
            ).scalar_one()
            noon_variant = session.execute(
                select(EpisodeVariant).where(
                    EpisodeVariant.episode_id == "ep-noon"
                )
            ).scalar_one()
            morning.selected_variant_id = noon_variant.id
    finally:
        engine.dispose()


def test_pack_import_is_idempotent_and_canon_review_is_audited(
    migrated_database: DatabaseSettings,
    tmp_path: Path,
) -> None:
    value = json.loads(
        (
            PROJECT_ROOT
            / "content"
            / "examples"
            / "daily-life-pack.travel.example.json"
        ).read_text(encoding="utf-8")
    )
    image_path = tmp_path / "person.png"
    Image.new("RGB", (64, 64), "white").save(image_path)
    engine = create_database_engine(migrated_database, DatabaseOperation.TEST)
    factory = create_session_factory(engine)
    try:
        with factory.begin() as session:
            pack, created = import_daily_life_pack(session, value)
            repeated, repeated_created = import_daily_life_pack(session, value)
            assert pack.id == repeated.id
            assert created is True
            assert repeated_created is False

        with factory.begin() as session:
            approved = approve_daily_life_pack(session, value["lifePackId"])
            assert approved.status == "approved"

        with factory.begin() as session:
            asset, created = import_reference_asset(
                session,
                asset_root=tmp_path / "assets",
                role="person",
                asset_id="person-v1",
                source_path=image_path,
            )
            assert created is True
            assert Path(asset.storage_path).is_file()

        with factory.begin() as session:
            asset = review_reference_asset(
                session,
                asset_id="person-v1",
                approve=True,
                reason="identity checked",
            )
            assert asset.status == "approved"
    finally:
        engine.dispose()


def test_terminal_job_retry_is_audited_and_advances_render_revision(
    migrated_database: DatabaseSettings,
) -> None:
    engine = create_database_engine(migrated_database, DatabaseOperation.TEST)
    factory = create_session_factory(engine)
    try:
        with factory.begin() as session:
            pack = add_pack(
                session,
                "life-retry-terminal",
                status="rendering",
            )
            slot = add_slot(session, pack, "morning", 1)
            slot.status = "failed"
            variant = add_variant(session, slot, "ep-retry-terminal")
            variant.status = "failed"
            variant.last_error = "provider_failed"
            slot.last_error = "provider_failed"
            job = GenerationJob(
                episode_variant_id=variant.id,
                render_revision=1,
                job_type="video",
                clip_index=0,
                normalized_input_hash="1" * 64,
                idempotency_key="2" * 64,
                provider="volcengine-agent-plan",
                status="failed",
                attempt_no=1,
                request_snapshot_json={},
                error_code="InternalServiceError",
            )
            session.add(job)

        with factory.begin() as session:
            event = retry_failed_slot(
                session,
                life_pack_id="life-retry-terminal",
                slot_name="morning",
                reason="operator confirmed terminal provider failure",
            )
            assert event.from_render_revision == 1
            assert event.to_render_revision == 2

        with factory() as session:
            slot = session.execute(
                select(DailySlot).where(
                    DailySlot.daily_life_pack_id
                    == select(DailyLifePack.id)
                    .where(
                        DailyLifePack.life_pack_id
                        == "life-retry-terminal"
                    )
                    .scalar_subquery(),
                    DailySlot.slot == "morning",
                )
            ).scalar_one()
            variant = session.execute(
                select(EpisodeVariant).where(
                    EpisodeVariant.daily_slot_id == slot.id
                )
            ).scalar_one()
            events = session.execute(select(SlotRetryEvent)).scalars().all()
            assert slot.status == "planned"
            assert slot.last_error is None
            assert variant.status == "planned"
            assert variant.active_render_revision == 2
            assert variant.last_error is None
            assert len(events) == 1
            assert (
                events[0].reason
                == "operator confirmed terminal provider failure"
            )
    finally:
        engine.dispose()


def test_skip_locked_claims_distinct_packs(
    migrated_database: DatabaseSettings,
) -> None:
    engine = create_database_engine(migrated_database, DatabaseOperation.TEST)
    factory = create_session_factory(engine)
    try:
        with factory.begin() as session:
            add_pack(session, "life-first")
            add_pack(session, "life-second")

        first_session = factory()
        second_session = factory()
        try:
            first_session.begin()
            first = claim_next_life_pack(first_session)
            second_session.begin()
            second = claim_next_life_pack(second_session)

            assert first is not None
            assert second is not None
            assert first.id != second.id
        finally:
            first_session.rollback()
            second_session.rollback()
            first_session.close()
            second_session.close()
    finally:
        engine.dispose()


def test_claim_next_can_target_past_present_or_future_content_dates(
    migrated_database: DatabaseSettings,
) -> None:
    engine = create_database_engine(migrated_database, DatabaseOperation.TEST)
    factory = create_session_factory(engine)
    try:
        with factory.begin() as session:
            add_pack(
                session,
                "life-past",
                pack_date=date(2020, 1, 1),
            )
            add_pack(
                session,
                "life-present",
                pack_date=date(2026, 7, 27),
            )
            add_pack(
                session,
                "life-future",
                pack_date=date(2099, 12, 31),
            )

        with factory.begin() as session:
            future = claim_next_life_pack(
                session,
                target_date=date(2099, 12, 31),
            )
            assert future is not None
            assert future.life_pack_id == "life-future"

        with factory.begin() as session:
            missing = claim_next_life_pack(
                session,
                target_date=date(2050, 1, 1),
            )
            assert missing is None

        with factory.begin() as session:
            earliest = claim_next_life_pack(session)
            assert earliest is not None
            assert earliest.life_pack_id == "life-past"
    finally:
        engine.dispose()


def test_generation_job_idempotency(
    migrated_database: DatabaseSettings,
) -> None:
    engine = create_database_engine(migrated_database, DatabaseOperation.TEST)
    factory = create_session_factory(engine)
    try:
        with factory.begin() as session:
            pack = add_pack(session, "life-idempotency")
            slot = add_slot(session, pack, "morning", 1)
            variant = add_variant(session, slot, "ep-idempotency")
            variant_id = variant.id

        start_together = Barrier(2)

        def create_same_job() -> tuple[uuid.UUID, bool]:
            with factory.begin() as session:
                start_together.wait(timeout=10)
                job, created = get_or_create_generation_job(
                    session,
                    episode_variant_id=variant_id,
                    render_revision=1,
                    job_type="video",
                    clip_index=0,
                    normalized_input_hash="a" * 64,
                    idempotency_key="b" * 64,
                    provider="volcengine-ark",
                    request_snapshot_json={"duration": 10},
                )
                return job.id, created

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [
                future.result(timeout=15)
                for future in (
                    executor.submit(create_same_job),
                    executor.submit(create_same_job),
                )
            ]

        assert sorted(created for _, created in results) == [False, True]
        assert len({job_id for job_id, _ in results}) == 1
    finally:
        engine.dispose()


def test_failed_delivery_transaction_writes_no_continuity_events(
    migrated_database: DatabaseSettings,
) -> None:
    engine = create_database_engine(migrated_database, DatabaseOperation.TEST)
    factory = create_session_factory(engine)
    try:
        with factory.begin() as session:
            pack = add_pack(session, "life-delivery")
            slot = add_slot(session, pack, "morning", 1)
            variant = add_variant(session, slot, "ep-delivery")
            job = GenerationJob(
                episode_variant_id=variant.id,
                render_revision=1,
                job_type="video",
                clip_index=0,
                normalized_input_hash="c" * 64,
                idempotency_key="d" * 64,
                provider="volcengine-ark",
                status="succeeded",
                attempt_no=1,
                request_snapshot_json={},
            )
            session.add(job)
            session.flush()
            asset = MediaAsset(
                episode_variant_id=variant.id,
                generation_job_id=job.id,
                asset_kind="final_video",
                storage_path="var/assets/test.mp4",
                sha256="e" * 64,
                byte_size=1,
                container="mp4",
                video_codec="h264",
                audio_codec="aac",
                width=720,
                height=1280,
                duration_ms=10000,
                has_audio=True,
                qc_status="passed",
                qc_report_json={},
            )
            session.add(asset)

        with pytest.raises(IntegrityError), factory.begin() as session:
            pack = session.execute(
                select(DailyLifePack).where(
                    DailyLifePack.life_pack_id == "life-delivery"
                )
            ).scalar_one()
            variant = session.execute(select(EpisodeVariant)).scalar_one()
            asset = session.execute(select(MediaAsset)).scalar_one()
            delivery = DeliveryPackage(
                daily_life_pack_id=pack.id,
                delivery_revision=1,
                status="building",
                root_path="output/test",
                manifest_path="output/test/manifest.json",
                manifest_sha256="f" * 64,
            )
            session.add(delivery)
            session.flush()
            session.add_all(
                [
                    DeliveryItem(
                        delivery_package_id=delivery.id,
                        sort_order=1,
                        slot="morning",
                        episode_variant_id=variant.id,
                        media_asset_id=asset.id,
                        file_name="01-morning.mp4",
                    ),
                    DeliveryItem(
                        delivery_package_id=delivery.id,
                        sort_order=1,
                        slot="morning",
                        episode_variant_id=variant.id,
                        media_asset_id=asset.id,
                        file_name="duplicate.mp4",
                    ),
                    ContinuityEvent(
                        event_id="event-should-rollback",
                        delivery_package_id=delivery.id,
                        episode_variant_id=variant.id,
                        sort_order=1,
                        scope="day",
                        entity_id="day",
                        operation="add",
                        path="props.test",
                        value=True,
                        status="applied",
                        reason="test rollback",
                    ),
                ]
            )

        with factory() as session:
            assert session.scalar(select(ContinuityEvent.id)) is None
            assert session.scalar(select(DeliveryPackage.id)) is None
    finally:
        engine.dispose()


def test_downgrade_removes_product_tables_but_preserves_schema(
    migrated_database: DatabaseSettings,
) -> None:
    command.downgrade(alembic_config(), "base")
    engine = create_database_engine(migrated_database, DatabaseOperation.TEST)
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names(schema="cat_video"))
        schema_names = set(inspector.get_schema_names())
    finally:
        engine.dispose()

    assert table_names == {"alembic_version"}
    assert "cat_video" in schema_names


def test_isolated_remote_validation_migrates_checks_and_cleans_up(
    postgres_settings: DatabaseSettings,
) -> None:
    report = validate_remote_database(postgres_settings)
    engine = create_database_engine(postgres_settings, DatabaseOperation.TEST)
    try:
        remaining_validation_schemas = [
            name
            for name in inspect(engine).get_schema_names()
            if name.startswith("cat_video_validation_")
        ]
    finally:
        engine.dispose()

    assert report.ok is True
    assert report.ssl_in_use is False
    assert report.cleanup_succeeded is True
    assert all(report.checks.values())
    assert remaining_validation_schemas == []
