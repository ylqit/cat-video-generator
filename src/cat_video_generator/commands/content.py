from __future__ import annotations

from pathlib import Path

import typer
from sqlalchemy.exc import SQLAlchemyError

from ..config import ConfigurationError, RuntimeSettings
from ..content_service import (
    ContentNotFoundError,
    approve_daily_life_pack,
    import_daily_life_pack,
    import_reference_asset,
    review_media_asset,
    review_reference_asset,
)
from ..contracts import (
    ContentConflictError,
    ContentValidationError,
    canonical_content_hash,
    load_json_object,
    validate_daily_life_pack,
)
from ..doctor import DatabasePreflightError
from .common import abort_operation, database_context, echo_json


def validate_pack(file: Path) -> None:
    value = load_json_object(file)
    validate_daily_life_pack(value)
    echo_json(
        {
            "ok": True,
            "lifePackId": value["lifePackId"],
            "planRevision": value["planRevision"],
            "contentHash": canonical_content_hash(value),
        }
    )


def import_pack(file: Path) -> None:
    value = load_json_object(file)
    try:
        with (
            database_context() as context,
            context.session_factory.begin() as session,
        ):
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
        abort_operation(exc)
    echo_json(payload)


def approve_pack(life_pack_id: str) -> None:
    try:
        with (
            database_context() as context,
            context.session_factory.begin() as session,
        ):
            pack = approve_daily_life_pack(session, life_pack_id)
            payload = {
                "lifePackId": pack.life_pack_id,
                "planRevision": pack.plan_revision,
                "status": pack.status,
                "approvedAt": (
                    None
                    if pack.approved_at is None
                    else pack.approved_at.isoformat()
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
        abort_operation(exc)
    echo_json(payload)


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
        with (
            database_context() as context,
            context.session_factory.begin() as session,
        ):
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
        abort_operation(exc)
    echo_json(payload)


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
    try:
        with (
            database_context() as context,
            context.session_factory.begin() as session,
        ):
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
        abort_operation(exc)
    echo_json(payload)
