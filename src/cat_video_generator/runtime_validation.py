from __future__ import annotations

import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date
from threading import Barrier
from typing import Any

from sqlalchemy import Engine, delete, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .config import DatabaseSettings
from .doctor import inspect_database
from .migration import expected_alembic_head
from .models import (
    Base,
    ContinuityEvent,
    DailyLifePack,
    DailySlot,
    DeliveryPackage,
    EpisodeVariant,
    GenerationJob,
)
from .repository import get_or_create_generation_job


class RuntimeValidationError(RuntimeError):
    """Raised when the formal runtime schema or transaction checks fail."""


@dataclass(frozen=True, slots=True)
class RuntimeValidationReport:
    ok: bool
    database: str
    user: str
    server_version_num: int
    ssl_in_use: bool
    transport_security: str
    schema: str
    alembic_revision: str
    schema_fingerprint: str
    validation_run_id: str
    checks: dict[str, bool]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["schemaFingerprint"] = result.pop("schema_fingerprint")
        result["validationRunId"] = result.pop("validation_run_id")
        result["serverVersionNum"] = result.pop("server_version_num")
        result["sslInUse"] = result.pop("ssl_in_use")
        result["transportSecurity"] = result.pop("transport_security")
        result["alembicRevision"] = result.pop("alembic_revision")
        return result


def validate_runtime_database(
    engine: Engine,
    settings: DatabaseSettings,
) -> RuntimeValidationReport:
    """Validate the real schema while deleting only this run's UUID records."""
    doctor = inspect_database(
        engine,
        settings,
        expected_revision=expected_alembic_head(),
    )
    if not doctor.ready_for_runtime:
        raise RuntimeValidationError("; ".join(doctor.warnings))

    structure, structure_checks = _inspect_schema(engine, settings.schema)
    validation_run_id = uuid.uuid4().hex
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    checks = {
        **structure_checks,
        "transactionRollback": _check_transaction_rollback(
            factory, validation_run_id
        ),
        "slotConstraints": _check_slot_constraint(factory, validation_run_id),
        "idempotency": _check_idempotency(factory, validation_run_id),
        "skipLocked": _check_skip_locked(factory, validation_run_id),
        "deliveryAtomicity": _check_delivery_atomicity(
            factory, validation_run_id
        ),
    }
    checks["testDataCleanup"] = _validation_rows_absent(
        factory, validation_run_id
    )
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise RuntimeValidationError(
            "Runtime database validation failed: " + ", ".join(failed)
        )

    fingerprint = hashlib.sha256(
        json.dumps(
            structure,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return RuntimeValidationReport(
        ok=True,
        database=doctor.database,
        user=doctor.user,
        server_version_num=doctor.server_version_num,
        ssl_in_use=doctor.ssl_in_use,
        transport_security=doctor.transport_security,
        schema=settings.schema,
        alembic_revision=doctor.alembic_revision or "",
        schema_fingerprint=fingerprint,
        validation_run_id=validation_run_id,
        checks=checks,
    )


def _inspect_schema(
    engine: Engine,
    schema: str,
) -> tuple[dict[str, Any], dict[str, bool]]:
    inspector = inspect(engine)
    expected_tables = {
        table.name: table
        for table in Base.metadata.sorted_tables
    }
    actual_names = set(inspector.get_table_names(schema=schema))
    expected_names = set(expected_tables) | {"alembic_version"}
    table_names_match = actual_names == expected_names

    actual_structure: dict[str, Any] = {}
    columns_match = True
    primary_keys_match = True
    foreign_keys_match = True
    unique_constraints_match = True
    check_constraints_match = True
    indexes_match = True

    for table_name, table in expected_tables.items():
        actual_columns = inspector.get_columns(table_name, schema=schema)
        actual_column_map = {
            column["name"]: {
                "type": _type_signature(column["type"]),
                "nullable": bool(column["nullable"]),
            }
            for column in actual_columns
        }
        expected_column_map = {
            column.name: {
                "type": _type_signature(column.type),
                "nullable": bool(column.nullable),
            }
            for column in table.columns
        }
        columns_match &= actual_column_map == expected_column_map

        actual_pk = tuple(
            inspector.get_pk_constraint(
                table_name, schema=schema
            ).get("constrained_columns")
            or ()
        )
        expected_pk = tuple(column.name for column in table.primary_key.columns)
        primary_keys_match &= actual_pk == expected_pk

        actual_foreign_keys = {
            (
                tuple(item["constrained_columns"]),
                item["referred_table"],
                tuple(item["referred_columns"]),
            )
            for item in inspector.get_foreign_keys(table_name, schema=schema)
        }
        expected_foreign_keys = {
            (
                tuple(element.parent.name for element in constraint.elements),
                constraint.elements[0].column.table.name,
                tuple(element.column.name for element in constraint.elements),
            )
            for constraint in table.foreign_key_constraints
        }
        foreign_keys_match &= actual_foreign_keys == expected_foreign_keys

        actual_unique = {
            item["name"]
            for item in inspector.get_unique_constraints(
                table_name, schema=schema
            )
            if item.get("name")
        }
        expected_unique = {
            constraint.name
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
            and constraint.name
        }
        unique_constraints_match &= actual_unique == expected_unique

        actual_checks = {
            item["name"]
            for item in inspector.get_check_constraints(
                table_name, schema=schema
            )
            if item.get("name")
        }
        expected_checks = {
            constraint.name
            for constraint in table.constraints
            if constraint.__class__.__name__ == "CheckConstraint"
            and constraint.name
        }
        check_constraints_match &= actual_checks == expected_checks

        actual_indexes = {
            item["name"]
            for item in inspector.get_indexes(table_name, schema=schema)
            if item.get("name") and not item.get("duplicates_constraint")
        }
        expected_indexes = {index.name for index in table.indexes if index.name}
        indexes_match &= actual_indexes == expected_indexes

        actual_structure[table_name] = {
            "columns": actual_column_map,
            "primaryKey": actual_pk,
            "foreignKeys": sorted(actual_foreign_keys),
            "uniqueConstraints": sorted(actual_unique),
            "checkConstraints": sorted(actual_checks),
            "indexes": sorted(actual_indexes),
        }

    actual_structure["alembic_version"] = {
        "columns": {
            item["name"]: _type_signature(item["type"])
            for item in inspector.get_columns("alembic_version", schema=schema)
        }
    }
    checks = {
        "tableStructure": table_names_match,
        "columnTypes": columns_match,
        "primaryKeys": primary_keys_match,
        "foreignKeys": foreign_keys_match,
        "uniqueConstraints": unique_constraints_match,
        "checkConstraints": check_constraints_match,
        "indexes": indexes_match,
    }
    return actual_structure, checks


def _type_signature(value: Any) -> str:
    name = value.__class__.__name__.lower()
    length = getattr(value, "length", None)
    timezone = getattr(value, "timezone", None)
    if name in {"varchar", "string"}:
        return f"varchar({length})"
    if name in {"timestamp", "datetime"}:
        return "timestamptz" if timezone else "timestamp"
    aliases = {
        "uuid": "uuid",
        "jsonb": "jsonb",
        "boolean": "boolean",
        "date": "date",
        "smallinteger": "smallint",
        "integer": "integer",
        "text": "text",
    }
    return aliases.get(name, name)


def _new_pack(run_id: str, suffix: str) -> DailyLifePack:
    return DailyLifePack(
        life_pack_id=f"validation-{run_id}-{suffix}",
        date=date(2099, 12, 31),
        plan_revision=1,
        content_hash=hashlib.sha256(f"{run_id}-{suffix}".encode()).hexdigest(),
        source_json={"validationRunId": run_id},
        day_context_json={"contextType": "validation"},
        status="approved",
        is_degraded=False,
    )


def _new_slot(pack: DailyLifePack) -> DailySlot:
    return DailySlot(
        daily_life_pack_id=pack.id,
        slot="morning",
        sort_order=1,
        status="planned",
    )


def _new_variant(slot: DailySlot, run_id: str, suffix: str) -> EpisodeVariant:
    episode_id = f"validation-{run_id}-{suffix}"
    return EpisodeVariant(
        daily_slot_id=slot.id,
        episode_id=episode_id,
        role="primary",
        episode_spec_json={
            "episodeId": episode_id,
            "validationRunId": run_id,
        },
        active_render_revision=1,
        status="planned",
    )


def _check_transaction_rollback(
    factory: sessionmaker[Session],
    run_id: str,
) -> bool:
    pack = _new_pack(run_id, "rollback")
    with factory() as session:
        transaction = session.begin()
        session.add(pack)
        session.flush()
        pack_id = pack.id
        transaction.rollback()
    with factory() as session:
        return session.get(DailyLifePack, pack_id) is None


def _check_slot_constraint(
    factory: sessionmaker[Session],
    run_id: str,
) -> bool:
    pack = _new_pack(run_id, "constraint")
    rejected = False
    with factory() as session:
        session.begin()
        session.add(pack)
        session.flush()
        session.add(
            DailySlot(
                daily_life_pack_id=pack.id,
                slot="morning",
                sort_order=2,
                status="planned",
            )
        )
        try:
            session.flush()
        except IntegrityError:
            rejected = True
        finally:
            session.rollback()
    return rejected


def _check_idempotency(
    factory: sessionmaker[Session],
    run_id: str,
) -> bool:
    with factory.begin() as session:
        pack = _new_pack(run_id, "idempotency")
        session.add(pack)
        session.flush()
        slot = _new_slot(pack)
        session.add(slot)
        session.flush()
        variant = _new_variant(slot, run_id, "idempotency")
        session.add(variant)
        session.flush()
        pack_id, slot_id, variant_id = pack.id, slot.id, variant.id

    barrier = Barrier(2)
    idempotency_key = hashlib.sha256(
        f"{run_id}-idempotency".encode()
    ).hexdigest()

    def create_job() -> tuple[uuid.UUID, bool]:
        with factory.begin() as session:
            barrier.wait(timeout=10)
            job, created = get_or_create_generation_job(
                session,
                episode_variant_id=variant_id,
                render_revision=1,
                job_type="video",
                clip_index=0,
                normalized_input_hash=hashlib.sha256(run_id.encode()).hexdigest(),
                idempotency_key=idempotency_key,
                provider="volcengine-ark",
                request_snapshot_json={"validationRunId": run_id},
            )
            return job.id, created

    passed = False
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [
                future.result(timeout=20)
                for future in (
                    executor.submit(create_job),
                    executor.submit(create_job),
                )
            ]
        passed = (
            sorted(created for _, created in results) == [False, True]
            and len({job_id for job_id, _ in results}) == 1
        )
    finally:
        with factory.begin() as session:
            session.execute(
                delete(GenerationJob).where(
                    GenerationJob.episode_variant_id == variant_id
                )
            )
            session.execute(
                delete(EpisodeVariant).where(EpisodeVariant.id == variant_id)
            )
            session.execute(delete(DailySlot).where(DailySlot.id == slot_id))
            session.execute(
                delete(DailyLifePack).where(DailyLifePack.id == pack_id)
            )
    return passed


def _check_skip_locked(
    factory: sessionmaker[Session],
    run_id: str,
) -> bool:
    with factory.begin() as session:
        packs = [
            _new_pack(run_id, "lock-a"),
            _new_pack(run_id, "lock-b"),
        ]
        session.add_all(packs)
        session.flush()
        pack_ids = tuple(pack.id for pack in packs)

    first = factory()
    second = factory()
    passed = False
    try:
        first.begin()
        first_pack = first.execute(
            select(DailyLifePack)
            .where(DailyLifePack.id.in_(pack_ids))
            .order_by(DailyLifePack.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).scalar_one()
        second.begin()
        second_pack = second.execute(
            select(DailyLifePack)
            .where(DailyLifePack.id.in_(pack_ids))
            .order_by(DailyLifePack.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).scalar_one()
        passed = (
            first_pack is not None
            and second_pack is not None
            and first_pack.id != second_pack.id
            and {first_pack.id, second_pack.id} == set(pack_ids)
        )
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()
        with factory.begin() as session:
            session.execute(
                delete(DailyLifePack).where(DailyLifePack.id.in_(pack_ids))
            )
    return passed


def _check_delivery_atomicity(
    factory: sessionmaker[Session],
    run_id: str,
) -> bool:
    with factory.begin() as session:
        pack = _new_pack(run_id, "delivery")
        session.add(pack)
        session.flush()
        slot = _new_slot(pack)
        session.add(slot)
        session.flush()
        variant = _new_variant(slot, run_id, "delivery")
        session.add(variant)
        session.flush()
        pack_id, slot_id, variant_id = pack.id, slot.id, variant.id

    delivery_id = uuid.uuid4()
    event_id = uuid.uuid4()
    try:
        with factory.begin() as session:
            session.add(
                DeliveryPackage(
                    id=delivery_id,
                    daily_life_pack_id=pack_id,
                    delivery_revision=1,
                    status="building",
                    root_path=f"validation/{run_id}",
                    manifest_path=f"validation/{run_id}/manifest.json",
                    manifest_sha256=hashlib.sha256(run_id.encode()).hexdigest(),
                )
            )
            session.flush()
            session.add(
                ContinuityEvent(
                    id=event_id,
                    event_id=f"validation-{run_id}",
                    delivery_package_id=delivery_id,
                    episode_variant_id=variant_id,
                    sort_order=1,
                    scope="day",
                    entity_id="validation",
                    operation="add",
                    path="validation.run",
                    value={"validationRunId": run_id},
                    status="applied",
                    reason="runtime validation rollback",
                )
            )
            session.flush()
            raise _ForcedRollback
    except _ForcedRollback:
        pass

    with factory() as session:
        passed = (
            session.get(DeliveryPackage, delivery_id) is None
            and session.get(ContinuityEvent, event_id) is None
        )
    with factory.begin() as session:
        session.execute(
            delete(EpisodeVariant).where(EpisodeVariant.id == variant_id)
        )
        session.execute(delete(DailySlot).where(DailySlot.id == slot_id))
        session.execute(delete(DailyLifePack).where(DailyLifePack.id == pack_id))
    return passed


def _validation_rows_absent(
    factory: sessionmaker[Session],
    run_id: str,
) -> bool:
    with factory() as session:
        pack_exists = session.scalar(
            select(DailyLifePack.id)
            .where(DailyLifePack.life_pack_id.like(f"validation-{run_id}-%"))
            .limit(1)
        )
        job_exists = session.scalar(
            select(GenerationJob.id)
            .where(
                GenerationJob.request_snapshot_json["validationRunId"].astext
                == run_id
            )
            .limit(1)
        )
        return pack_exists is None and job_exists is None


class _ForcedRollback(Exception):
    pass
