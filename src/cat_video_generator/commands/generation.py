from __future__ import annotations

import uuid
from datetime import date

import typer
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from ..ark_provider import ArkMediaProvider
from ..config import ConfigurationError, RuntimeSettings
from ..content_service import (
    ContentNotFoundError,
    retry_failed_slot,
)
from ..contracts import ContentConflictError, ContentValidationError
from ..doctor import DatabasePreflightError
from ..generation import OrchestrationError, PackGenerationService
from ..generation.jobs import ArkJobExecutor
from ..models import DailyLifePack
from ..repository import claim_next_life_pack
from ..status_query import query_status
from .common import abort_operation, database_context, echo_json


def status(
    life_pack_id: str | None = typer.Argument(None),
) -> None:
    try:
        with (
            database_context() as context,
            context.session_factory() as session,
        ):
            payload = query_status(session, life_pack_id)
    except (ConfigurationError, DatabasePreflightError, SQLAlchemyError) as exc:
        abort_operation(exc)
    echo_json(payload)


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
    runtime_settings = RuntimeSettings.from_env()
    try:
        with database_context() as context:
            with context.session_factory() as session:
                candidate_statement = select(DailyLifePack.id).where(
                    DailyLifePack.status.in_(("approved", "frozen"))
                )
                if parsed_target_date is not None:
                    candidate_statement = candidate_statement.where(
                        DailyLifePack.date == parsed_target_date
                    )
                candidate_exists = (
                    session.execute(
                        candidate_statement.limit(1)
                    ).scalar_one_or_none()
                    is not None
                )
            if candidate_exists:
                runtime_settings.validate_for_generation(
                    allow_paid_generation=allow_paid_generation
                )
            with context.session_factory.begin() as session:
                pack = claim_next_life_pack(
                    session,
                    target_date=parsed_target_date,
                )
                selected_id = None if pack is None else pack.life_pack_id
            if selected_id is None:
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
                payload = PackGenerationService(
                    context.session_factory,
                    runtime_settings,
                ).run_pack(selected_id)
                payload["claimedLifePackId"] = selected_id
    except (
        ConfigurationError,
        DatabasePreflightError,
        OrchestrationError,
        SQLAlchemyError,
    ) as exc:
        abort_operation(exc)
    echo_json(payload)


def run_pack(
    life_pack_id: str,
    slot: str | None = typer.Option(None, "--slot"),
    allow_paid_generation: bool = typer.Option(
        False,
        "--allow-paid-generation",
        help="Explicitly acknowledge that Ark requests can incur charges.",
    ),
) -> None:
    runtime_settings = RuntimeSettings.from_env()
    try:
        runtime_settings.validate_for_generation(
            allow_paid_generation=allow_paid_generation
        )
        with database_context() as context:
            payload = PackGenerationService(
                context.session_factory,
                runtime_settings,
            ).run_pack(life_pack_id, slot_name=slot)
    except (
        ConfigurationError,
        DatabasePreflightError,
        OrchestrationError,
        SQLAlchemyError,
    ) as exc:
        abort_operation(exc)
    echo_json(payload)


def retry_slot(
    life_pack_id: str,
    slot: str = typer.Option(..., "--slot"),
    reason: str = typer.Option(..., "--reason"),
) -> None:
    try:
        with (
            database_context() as context,
            context.session_factory.begin() as session,
        ):
            event = retry_failed_slot(
                session,
                life_pack_id=life_pack_id,
                slot_name=slot,
                reason=reason,
            )
            payload = {
                "lifePackId": life_pack_id,
                "slot": slot,
                "fromRenderRevision": event.from_render_revision,
                "toRenderRevision": event.to_render_revision,
                "retryEventId": str(event.id),
                "status": "planned",
            }
    except (
        ConfigurationError,
        ContentConflictError,
        ContentNotFoundError,
        ContentValidationError,
        DatabasePreflightError,
        SQLAlchemyError,
    ) as exc:
        abort_operation(exc)
    echo_json(payload)


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
    runtime_settings = RuntimeSettings.from_env()
    try:
        runtime_settings.validate_for_generation(
            allow_paid_generation=allow_paid_generation
        )
        with database_context() as context:
            payload = PackGenerationService(
                context.session_factory,
                runtime_settings,
            ).resume(life_pack_id)
    except (
        ConfigurationError,
        DatabasePreflightError,
        OrchestrationError,
        SQLAlchemyError,
    ) as exc:
        abort_operation(exc)
    echo_json(payload)


def reconcile_job(
    job_id: str,
    provider_task_id: str | None = typer.Option(
        None,
        "--provider-task-id",
        help=(
            "Bind one manually verified Ark task. Omit this option to list "
            "safe candidate metadata without changing the job."
        ),
    ),
) -> None:
    try:
        parsed_job_id = uuid.UUID(job_id)
    except ValueError as exc:
        raise typer.BadParameter("job_id must be a UUID.") from exc

    runtime_settings = RuntimeSettings.from_env()
    try:
        runtime_settings.validate_for_ark_access()
        with database_context() as context:
            executor = ArkJobExecutor(
                context.session_factory,
                runtime_settings,
                ArkMediaProvider(runtime_settings),
            )
            payload = executor.reconcile_video_job(
                parsed_job_id,
                provider_task_id=provider_task_id,
            )
    except (
        ConfigurationError,
        DatabasePreflightError,
        OrchestrationError,
        SQLAlchemyError,
    ) as exc:
        abort_operation(exc)
    echo_json(payload)
