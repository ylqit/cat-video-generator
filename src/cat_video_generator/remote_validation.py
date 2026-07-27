from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, replace
from datetime import date
from typing import Any

from sqlalchemy import Engine, func, insert, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from alembic import command

from .config import DatabaseOperation, DatabaseSettings
from .db import create_database_engine, create_session_factory
from .doctor import inspect_database
from .migration import alembic_config, expected_alembic_head
from .models import DailyLifePack, DailySlot, EpisodeVariant
from .repository import claim_next_life_pack, get_or_create_generation_job

_VALIDATION_SCHEMA_PREFIX = "cat_video_validation_"


class RemoteValidationError(RuntimeError):
    """Raised when the isolated remote database validation cannot complete safely."""


@dataclass(frozen=True, slots=True)
class RemoteValidationReport:
    ok: bool
    database: str
    user: str
    server_version_num: int
    ssl_in_use: bool
    validation_schema: str
    alembic_revision: str
    checks: dict[str, bool]
    cleanup_succeeded: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _new_pack(pack_id: str, pack_date: date) -> DailyLifePack:
    return DailyLifePack(
        life_pack_id=pack_id,
        date=pack_date,
        plan_revision=1,
        content_hash="0" * 64,
        source_json={},
        day_context_json={"contextType": "validation"},
        status="approved",
        is_degraded=False,
    )


def _check_transaction_rollback(engine: Engine) -> bool:
    marker = f"validation-rollback-{uuid.uuid4().hex}"
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(
            insert(DailyLifePack).values(
                id=uuid.uuid4(),
                life_pack_id=marker,
                date=date(2000, 1, 1),
                plan_revision=1,
                content_hash="0" * 64,
                source_json={},
                day_context_json={"contextType": "validation"},
                status="candidate",
                is_degraded=False,
            )
        )
        transaction.rollback()
        remaining = connection.scalar(
            select(func.count())
            .select_from(DailyLifePack)
            .where(DailyLifePack.life_pack_id == marker)
        )
    return remaining == 0


def _check_slot_constraint(engine: Engine) -> bool:
    factory = create_session_factory(engine)
    rejected = False
    with factory.begin() as session:
        pack = _new_pack(
            f"validation-constraint-{uuid.uuid4().hex}",
            date(2000, 1, 2),
        )
        session.add(pack)
        session.flush()
        try:
            with session.begin_nested():
                session.add(
                    DailySlot(
                        daily_life_pack_id=pack.id,
                        slot="morning",
                        sort_order=2,
                        status="planned",
                    )
                )
                session.flush()
        except IntegrityError:
            rejected = True
    return rejected


def _check_idempotency(engine: Engine) -> bool:
    factory = create_session_factory(engine)
    suffix = uuid.uuid4().hex
    with factory.begin() as session:
        pack = _new_pack(f"validation-idempotency-{suffix}", date(2000, 1, 3))
        session.add(pack)
        session.flush()
        slot = DailySlot(
            daily_life_pack_id=pack.id,
            slot="morning",
            sort_order=1,
            status="planned",
        )
        session.add(slot)
        session.flush()
        variant = EpisodeVariant(
            daily_slot_id=slot.id,
            episode_id=f"validation-episode-{suffix}",
            role="primary",
            episode_spec_json={"episodeId": f"validation-episode-{suffix}"},
            active_render_revision=1,
            status="planned",
        )
        session.add(variant)
        session.flush()
        job_arguments = {
            "episode_variant_id": variant.id,
            "render_revision": 1,
            "job_type": "video",
            "clip_index": 0,
            "normalized_input_hash": "a" * 64,
            "idempotency_key": suffix.ljust(64, "0"),
            "provider": "validation-no-provider-call",
            "request_snapshot_json": {"validation": True},
        }
        first, first_created = get_or_create_generation_job(session, **job_arguments)
        second, second_created = get_or_create_generation_job(session, **job_arguments)
    return first.id == second.id and first_created and not second_created


def _check_skip_locked(engine: Engine) -> bool:
    factory = create_session_factory(engine)
    target_date = date(2099, 12, 30)
    suffix = uuid.uuid4().hex
    with factory.begin() as session:
        session.add_all(
            [
                _new_pack(f"validation-lock-a-{suffix}", target_date),
                _new_pack(f"validation-lock-b-{suffix}", target_date),
            ]
        )

    first_session = factory()
    second_session = factory()
    try:
        first_session.begin()
        first = claim_next_life_pack(first_session, target_date=target_date)
        second_session.begin()
        second = claim_next_life_pack(second_session, target_date=target_date)
        return first is not None and second is not None and first.id != second.id
    finally:
        first_session.rollback()
        second_session.rollback()
        first_session.close()
        second_session.close()


def validate_remote_database(settings: DatabaseSettings) -> RemoteValidationReport:
    schema = f"{_VALIDATION_SCHEMA_PREFIX}{uuid.uuid4().hex[:12]}"
    validation_settings = replace(
        settings,
        schema=schema,
        allow_insecure_remote_write_test=True,
    )
    validation_settings.validate_for(DatabaseOperation.REMOTE_VALIDATION)
    engine = create_database_engine(
        validation_settings,
        DatabaseOperation.REMOTE_VALIDATION,
    )
    schema_created = False
    report: RemoteValidationReport | None = None
    try:
        with engine.begin() as connection:
            identity = connection.execute(
                text(
                    "SELECT current_database(), current_user, "
                    "current_setting('server_version_num')::integer, "
                    "COALESCE(("
                    "  SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()"
                    "), false)"
                )
            ).one()
            if identity[0] != validation_settings.database:
                raise RemoteValidationError(
                    f"Connected database {identity[0]!r} does not match the "
                    f"configured database {validation_settings.database!r}."
                )
            if identity[2] < validation_settings.minimum_server_version:
                raise RemoteValidationError(
                    f"PostgreSQL server_version_num={identity[2]} is below "
                    f"{validation_settings.minimum_server_version}."
                )
            schema_exists = connection.scalar(
                text("SELECT to_regnamespace(:schema_name) IS NOT NULL"),
                {"schema_name": schema},
            )
            if schema_exists:
                raise RemoteValidationError(
                    f"Validation schema {schema!r} already exists; refusing to use it."
                )
            quoted_schema = connection.dialect.identifier_preparer.quote_schema(schema)
            connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))
            schema_created = True

        with engine.begin() as connection:
            command.upgrade(
                alembic_config(schema=schema, connection=connection),
                "head",
            )

        database_report = inspect_database(
            engine,
            validation_settings,
            expected_revision=expected_alembic_head(),
        )
        checks = {
            "migration": database_report.ready_for_runtime,
            "transactionRollback": _check_transaction_rollback(engine),
            "slotConstraint": _check_slot_constraint(engine),
            "idempotency": _check_idempotency(engine),
            "skipLocked": _check_skip_locked(engine),
        }
        failed_checks = [name for name, passed in checks.items() if not passed]
        if failed_checks:
            raise RemoteValidationError(
                "Remote validation checks failed: " + ", ".join(failed_checks)
            )
        report = RemoteValidationReport(
            ok=True,
            database=database_report.database,
            user=database_report.user,
            server_version_num=database_report.server_version_num,
            ssl_in_use=database_report.ssl_in_use,
            validation_schema=schema,
            alembic_revision=database_report.alembic_revision or "",
            checks=checks,
            cleanup_succeeded=True,
        )
    finally:
        cleanup_error: SQLAlchemyError | None = None
        if schema_created:
            try:
                with engine.begin() as connection:
                    quoted_schema = (
                        connection.dialect.identifier_preparer.quote_schema(schema)
                    )
                    connection.execute(text(f"DROP SCHEMA {quoted_schema} CASCADE"))
            except SQLAlchemyError as exc:
                cleanup_error = exc
        engine.dispose()
        if cleanup_error is not None:
            raise RemoteValidationError(
                f"Validation schema cleanup failed. Manually inspect and remove "
                f"{schema!r}; no other schema is safe to delete."
            ) from cleanup_error

    if report is None:
        raise RemoteValidationError("Remote validation did not produce a report.")
    return report
