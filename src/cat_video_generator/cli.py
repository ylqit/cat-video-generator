from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import typer
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from alembic import command

from .config import (
    ConfigurationError,
    DatabaseOperation,
    DatabaseSettings,
    RuntimeSettings,
)
from .content_service import (
    ContentNotFoundError,
    approve_daily_life_pack,
    import_daily_life_pack,
    import_reference_asset,
    review_media_asset,
    review_reference_asset,
)
from .contracts import (
    ContentConflictError,
    ContentValidationError,
    canonical_content_hash,
    load_json_object,
    validate_daily_life_pack,
)
from .db import create_database_engine, create_session_factory
from .delivery import DeliveryError, deliver_life_pack
from .doctor import DatabasePreflightError, inspect_database, require_runtime_database
from .migration import alembic_config, expected_alembic_head
from .models import (
    DailyLifePack,
    DailySlot,
    EpisodeVariant,
    GenerationJob,
    MediaAsset,
)
from .orchestration import ArkOrchestrator, OrchestrationError
from .remote_validation import RemoteValidationError, validate_remote_database
from .repository import claim_next_life_pack

app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)
db_app = typer.Typer(no_args_is_help=True)
canon_app = typer.Typer(no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(canon_app, name="canon")


def _load_settings() -> DatabaseSettings:
    try:
        return DatabaseSettings.from_env()
    except ConfigurationError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _abort_database_operation(exc: Exception) -> None:
    """Return a secret-safe database failure without rendering connection details."""
    typer.echo(
        json.dumps(
            {
                "ok": False,
                "errorType": type(exc).__name__,
                "error": (
                    str(exc)
                    if isinstance(
                        exc,
                        (
                            ConfigurationError,
                            DatabasePreflightError,
                            RemoteValidationError,
                            ContentValidationError,
                            ContentConflictError,
                            ContentNotFoundError,
                            OrchestrationError,
                            DeliveryError,
                        ),
                    )
                    else "Database operation failed; inspect server-side logs."
                ),
            },
            ensure_ascii=False,
        )
    )
    raise typer.Exit(code=2) from exc


@app.command()
def doctor(
    allow_insecure_readonly_smoke: bool = typer.Option(
        False,
        "--allow-insecure-readonly-smoke",
        help="Permit connectivity-only diagnostics over an unencrypted connection.",
    ),
) -> None:
    settings = _load_settings()
    runtime_settings = RuntimeSettings.from_env()
    if allow_insecure_readonly_smoke:
        settings = replace(settings, allow_insecure_readonly_smoke=True)
    try:
        engine = create_database_engine(
            settings,
            DatabaseOperation.READ_ONLY_SMOKE,
        )
        report = inspect_database(
            engine,
            settings,
            expected_revision=expected_alembic_head(),
        )
    except (ConfigurationError, DatabasePreflightError, SQLAlchemyError) as exc:
        _abort_database_operation(exc)
    finally:
        if "engine" in locals():
            engine.dispose()
    payload = report.to_dict()
    payload["runtime"] = runtime_settings.preflight_report()
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command("validate-pack")
def validate_pack(file: Path) -> None:
    value = load_json_object(file)
    validate_daily_life_pack(value)
    typer.echo(
        json.dumps(
            {
                "ok": True,
                "lifePackId": value["lifePackId"],
                "planRevision": value["planRevision"],
                "contentHash": canonical_content_hash(value),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command("import-pack")
def import_pack(file: Path) -> None:
    value = load_json_object(file)
    settings = _load_settings()
    try:
        engine = create_database_engine(settings, DatabaseOperation.RUNTIME)
        require_runtime_database(engine, settings, expected_alembic_head())
        factory = create_session_factory(engine)
        with factory.begin() as session:
            pack, created = import_daily_life_pack(session, value)
            payload = {
                "lifePackId": pack.life_pack_id,
                "planRevision": pack.plan_revision,
                "status": pack.status,
                "contentHash": pack.content_hash,
                "created": created,
            }
    except (
        ConfigurationError,
        ContentValidationError,
        ContentConflictError,
        DatabasePreflightError,
        SQLAlchemyError,
    ) as exc:
        _abort_database_operation(exc)
    finally:
        if "engine" in locals():
            engine.dispose()
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command("approve-pack")
def approve_pack(life_pack_id: str) -> None:
    settings = _load_settings()
    try:
        engine = create_database_engine(settings, DatabaseOperation.RUNTIME)
        require_runtime_database(engine, settings, expected_alembic_head())
        factory = create_session_factory(engine)
        with factory.begin() as session:
            pack = approve_daily_life_pack(session, life_pack_id)
            payload = {
                "lifePackId": pack.life_pack_id,
                "planRevision": pack.plan_revision,
                "status": pack.status,
                "approvedAt": (
                    None if pack.approved_at is None else pack.approved_at.isoformat()
                ),
            }
    except (
        ConfigurationError,
        ContentValidationError,
        ContentConflictError,
        ContentNotFoundError,
        DatabasePreflightError,
        SQLAlchemyError,
    ) as exc:
        _abort_database_operation(exc)
    finally:
        if "engine" in locals():
            engine.dispose()
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@canon_app.command("import")
def canon_import(
    role: str = typer.Option(..., "--role"),
    asset_id: str = typer.Option(..., "--asset-id"),
    file: Path = typer.Option(..., "--file"),  # noqa: B008
    crop_box: str | None = typer.Option(
        None,
        "--crop-box",
        help="Optional left,top,right,bottom pixel box for screenshot cleanup.",
    ),
) -> None:
    database_settings = _load_settings()
    runtime_settings = RuntimeSettings.from_env()
    parsed_crop_box: tuple[int, int, int, int] | None = None
    if crop_box is not None:
        try:
            values = tuple(int(part.strip()) for part in crop_box.split(","))
        except ValueError as exc:
            raise typer.BadParameter(
                "--crop-box must contain four comma-separated integers."
            ) from exc
        if len(values) != 4:
            raise typer.BadParameter(
                "--crop-box must contain left,top,right,bottom."
            )
        parsed_crop_box = values
    try:
        engine = create_database_engine(
            database_settings,
            DatabaseOperation.RUNTIME,
        )
        require_runtime_database(
            engine,
            database_settings,
            expected_alembic_head(),
        )
        factory = create_session_factory(engine)
        with factory.begin() as session:
            asset, created = import_reference_asset(
                session,
                asset_root=runtime_settings.asset_root,
                role=role,
                asset_id=asset_id,
                source_path=file,
                crop_box=parsed_crop_box,
            )
            payload = {
                "assetId": asset.asset_id,
                "role": asset.role,
                "status": asset.status,
                "sha256": asset.sha256,
                "storagePath": asset.storage_path,
                "created": created,
            }
    except (
        ConfigurationError,
        ContentValidationError,
        ContentConflictError,
        DatabasePreflightError,
        SQLAlchemyError,
    ) as exc:
        _abort_database_operation(exc)
    finally:
        if "engine" in locals():
            engine.dispose()
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command()
def review(
    asset_id: str,
    approve: bool = typer.Option(False, "--approve"),
    reject: bool = typer.Option(False, "--reject"),
    reason: str = typer.Option(..., "--reason"),
) -> None:
    if approve == reject:
        raise typer.BadParameter("Choose exactly one of --approve or --reject.")
    if not reason.strip():
        raise typer.BadParameter("--reason cannot be empty.")
    settings = _load_settings()
    try:
        engine = create_database_engine(settings, DatabaseOperation.RUNTIME)
        require_runtime_database(engine, settings, expected_alembic_head())
        factory = create_session_factory(engine)
        with factory.begin() as session:
            try:
                asset = review_reference_asset(
                    session,
                    asset_id=asset_id,
                    approve=approve,
                    reason=reason,
                )
                payload = {
                    "assetId": asset.asset_id,
                    "reviewType": "canon",
                    "status": asset.status,
                }
            except ContentNotFoundError:
                media = review_media_asset(
                    session,
                    asset_id=asset_id,
                    approve=approve,
                    reason=reason,
                )
                payload = {
                    "assetId": str(media.id),
                    "reviewType": (
                        "keyframe"
                        if media.asset_kind.startswith("scene_keyframe_")
                        else "content"
                    ),
                    "status": media.review_status,
                }
    except (
        ConfigurationError,
        ContentConflictError,
        ContentNotFoundError,
        DatabasePreflightError,
        SQLAlchemyError,
    ) as exc:
        _abort_database_operation(exc)
    finally:
        if "engine" in locals():
            engine.dispose()
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@db_app.command("upgrade")
def db_upgrade(revision: str = "head") -> None:
    settings = _load_settings()
    try:
        settings.validate_for(DatabaseOperation.MIGRATION)
        command.upgrade(alembic_config(), revision)
    except (ConfigurationError, SQLAlchemyError) as exc:
        _abort_database_operation(exc)


@db_app.command("current")
def db_current() -> None:
    settings = _load_settings()
    try:
        engine = create_database_engine(settings, DatabaseOperation.RUNTIME)
        report = inspect_database(
            engine,
            settings,
            expected_revision=expected_alembic_head(),
        )
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    except (ConfigurationError, DatabasePreflightError, SQLAlchemyError) as exc:
        _abort_database_operation(exc)
    finally:
        if "engine" in locals():
            engine.dispose()


@db_app.command("validate-remote")
def db_validate_remote(
    allow_insecure_write_test: bool = typer.Option(
        False,
        "--allow-insecure-write-test",
        help=(
            "Acknowledge one isolated unencrypted write validation. This never "
            "enables normal migrations or runtime commands."
        ),
    ),
) -> None:
    if not allow_insecure_write_test:
        raise typer.BadParameter(
            "Remote write validation requires --allow-insecure-write-test."
        )
    settings = _load_settings()
    if settings.sslmode != "disable":
        raise typer.BadParameter(
            "This command is only for a temporary sslmode=disable validation. "
            "Use normal PostgreSQL tests for secure connections."
        )
    try:
        report = validate_remote_database(settings)
    except (ConfigurationError, RemoteValidationError, SQLAlchemyError) as exc:
        _abort_database_operation(exc)
    typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))


@app.command()
def status(life_pack_id: str | None = None) -> None:
    settings = _load_settings()
    try:
        engine = create_database_engine(settings, DatabaseOperation.RUNTIME)
        require_runtime_database(engine, settings, expected_alembic_head())
        factory = create_session_factory(engine)
        with factory() as session:
            statement = select(DailyLifePack).order_by(
                DailyLifePack.date,
                DailyLifePack.created_at,
            )
            if life_pack_id:
                statement = statement.where(
                    DailyLifePack.life_pack_id == life_pack_id
                )
            packs = session.execute(statement).scalars().all()
            payload = []
            for pack in packs:
                slots = session.execute(
                    select(DailySlot)
                    .where(DailySlot.daily_life_pack_id == pack.id)
                    .order_by(DailySlot.sort_order)
                ).scalars().all()
                slot_payload = []
                for slot in slots:
                    variants = session.execute(
                        select(EpisodeVariant).where(
                            EpisodeVariant.daily_slot_id == slot.id
                        )
                    ).scalars().all()
                    variant_ids = [variant.id for variant in variants]
                    jobs = (
                        []
                        if not variant_ids
                        else session.execute(
                            select(GenerationJob)
                            .where(
                                GenerationJob.episode_variant_id.in_(
                                    variant_ids
                                )
                            )
                            .order_by(GenerationJob.created_at.desc())
                        ).scalars().all()
                    )
                    assets = (
                        []
                        if not variant_ids
                        else session.execute(
                            select(MediaAsset)
                            .where(
                                MediaAsset.episode_variant_id.in_(
                                    variant_ids
                                )
                            )
                            .order_by(MediaAsset.created_at.desc())
                        ).scalars().all()
                    )
                    slot_payload.append(
                        {
                            "slot": slot.slot,
                            "sortOrder": slot.sort_order,
                            "status": slot.status,
                            "selectedVariantId": (
                                None
                                if slot.selected_variant_id is None
                                else str(slot.selected_variant_id)
                            ),
                            "lastError": slot.last_error,
                            "variants": [
                                {
                                    "episodeId": variant.episode_id,
                                    "role": variant.role,
                                    "status": variant.status,
                                    "renderRevision": (
                                        variant.active_render_revision
                                    ),
                                }
                                for variant in variants
                            ],
                            "latestJob": (
                                None
                                if not jobs
                                else {
                                    "jobType": jobs[0].job_type,
                                    "status": jobs[0].status,
                                    "providerTaskId": (
                                        jobs[0].provider_task_id
                                    ),
                                    "errorCode": jobs[0].error_code,
                                }
                            ),
                            "reviewAssets": [
                                {
                                    "assetId": str(asset.id),
                                    "kind": asset.asset_kind,
                                    "qcStatus": asset.qc_status,
                                    "reviewStatus": asset.review_status,
                                    "path": asset.storage_path,
                                }
                                for asset in assets
                                if asset.review_status == "pending"
                            ],
                            "nextAction": _next_action(
                                pack.life_pack_id,
                                slot,
                                assets,
                            ),
                        }
                    )
                payload.append(
                    {
                        "lifePackId": pack.life_pack_id,
                        "date": pack.date.isoformat(),
                        "planRevision": pack.plan_revision,
                        "status": pack.status,
                        "degraded": pack.is_degraded,
                        "slots": slot_payload,
                    }
                )
            typer.echo(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                )
            )
    except (ConfigurationError, DatabasePreflightError, SQLAlchemyError) as exc:
        _abort_database_operation(exc)
    finally:
        if "engine" in locals():
            engine.dispose()


@app.command("run-next")
def run_next(
    target_date_value: str | None = typer.Option(
        None,
        "--target-date",
        help=(
            "Claim only an approved/frozen LifePack with this content date "
            "(YYYY-MM-DD). The wall-clock execution time is never a gate."
        ),
    ),
    allow_paid_generation: bool = typer.Option(
        False,
        "--allow-paid-generation",
        help="Explicitly acknowledge that Ark requests can incur charges.",
    ),
) -> None:
    parsed_target_date: date | None = None
    if target_date_value is not None:
        try:
            parsed_target_date = date.fromisoformat(target_date_value)
        except ValueError as exc:
            raise typer.BadParameter(
                "--target-date must use the exact YYYY-MM-DD format."
            ) from exc
    settings = _load_settings()
    runtime_settings = RuntimeSettings.from_env()
    try:
        engine = create_database_engine(settings, DatabaseOperation.RUNTIME)
        require_runtime_database(engine, settings, expected_alembic_head())
        factory = create_session_factory(engine)
        with factory() as session:
            candidate_statement = select(DailyLifePack.id).where(
                DailyLifePack.status.in_(("approved", "frozen"))
            )
            if parsed_target_date is not None:
                candidate_statement = candidate_statement.where(
                    DailyLifePack.date == parsed_target_date
                )
            candidate_exists = (
                session.execute(candidate_statement.limit(1)).scalar_one_or_none()
                is not None
            )
        if candidate_exists:
            runtime_settings.validate_for_generation(
                allow_paid_generation=allow_paid_generation
            )
        with factory.begin() as session:
            pack = claim_next_life_pack(
                session,
                target_date=parsed_target_date,
            )
            result = None if pack is None else pack.life_pack_id
        if result is None:
            payload = {
                "claimedLifePackId": None,
                "targetDate": (
                    None
                    if parsed_target_date is None
                    else parsed_target_date.isoformat()
                ),
                "slots": [],
            }
        else:
            payload = ArkOrchestrator(
                factory,
                runtime_settings,
            ).run_pack(result)
            payload["claimedLifePackId"] = result
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    except (
        ConfigurationError,
        DatabasePreflightError,
        OrchestrationError,
        SQLAlchemyError,
    ) as exc:
        _abort_database_operation(exc)
    finally:
        if "engine" in locals():
            engine.dispose()


@app.command("run-pack")
def run_pack(
    life_pack_id: str,
    slot: str | None = typer.Option(None, "--slot"),
    allow_paid_generation: bool = typer.Option(
        False,
        "--allow-paid-generation",
        help="Explicitly acknowledge that Ark requests can incur charges.",
    ),
) -> None:
    database_settings = _load_settings()
    runtime_settings = RuntimeSettings.from_env()
    try:
        runtime_settings.validate_for_generation(
            allow_paid_generation=allow_paid_generation
        )
        engine = create_database_engine(
            database_settings,
            DatabaseOperation.RUNTIME,
        )
        require_runtime_database(
            engine,
            database_settings,
            expected_alembic_head(),
        )
        factory = create_session_factory(engine)
        payload = ArkOrchestrator(
            factory,
            runtime_settings,
        ).run_pack(life_pack_id, slot_name=slot)
    except (
        ConfigurationError,
        DatabasePreflightError,
        OrchestrationError,
        SQLAlchemyError,
    ) as exc:
        _abort_database_operation(exc)
    finally:
        if "engine" in locals():
            engine.dispose()
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command()
def resume(
    life_pack_id: str | None = typer.Argument(None),
    allow_paid_generation: bool = typer.Option(
        False,
        "--allow-paid-generation",
        help=(
            "Permit Ark lookups/download recovery. Resume never blindly "
            "resubmits a submission_unknown job."
        ),
    ),
) -> None:
    database_settings = _load_settings()
    runtime_settings = RuntimeSettings.from_env()
    try:
        runtime_settings.validate_for_generation(
            allow_paid_generation=allow_paid_generation
        )
        engine = create_database_engine(
            database_settings,
            DatabaseOperation.RUNTIME,
        )
        require_runtime_database(
            engine,
            database_settings,
            expected_alembic_head(),
        )
        factory = create_session_factory(engine)
        payload = ArkOrchestrator(
            factory,
            runtime_settings,
        ).resume(life_pack_id)
    except (
        ConfigurationError,
        DatabasePreflightError,
        OrchestrationError,
        SQLAlchemyError,
    ) as exc:
        _abort_database_operation(exc)
    finally:
        if "engine" in locals():
            engine.dispose()
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command()
def deliver(life_pack_id: str) -> None:
    database_settings = _load_settings()
    runtime_settings = RuntimeSettings.from_env()
    try:
        engine = create_database_engine(
            database_settings,
            DatabaseOperation.RUNTIME,
        )
        require_runtime_database(
            engine,
            database_settings,
            expected_alembic_head(),
        )
        factory = create_session_factory(engine)
        payload = deliver_life_pack(
            factory,
            runtime_settings,
            life_pack_id,
        )
    except (
        ConfigurationError,
        DatabasePreflightError,
        DeliveryError,
        SQLAlchemyError,
    ) as exc:
        _abort_database_operation(exc)
    finally:
        if "engine" in locals():
            engine.dispose()
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _next_action(
    life_pack_id: str,
    slot: DailySlot,
    assets: list[MediaAsset],
) -> str:
    if slot.status in {"keyframe_review", "content_review"}:
        pending = next(
            (
                asset
                for asset in assets
                if asset.review_status == "pending"
            ),
            None,
        )
        if pending is not None:
            return (
                f"cvg review {pending.id} --approve "
                '--reason "approved after visual inspection"'
            )
    if slot.status == "ready":
        return "none"
    if slot.status == "failed":
        return "inspect lastError and create a new render revision"
    if slot.status == "planned":
        return (
            f"cvg run-pack {life_pack_id} --slot {slot.slot} "
            "--allow-paid-generation"
        )
    return f"cvg resume {life_pack_id} --allow-paid-generation"


def main() -> None:
    app()


if __name__ == "__main__":
    main()
