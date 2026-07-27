from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from ..config import ConfigurationError, RuntimeSettings
from ..delivery import DeliveryError, deliver_life_pack
from ..doctor import DatabasePreflightError
from .common import abort_operation, database_context, echo_json


def deliver(life_pack_id: str) -> None:
    runtime_settings = RuntimeSettings.from_env()
    try:
        with database_context() as context:
            payload = deliver_life_pack(
                context.session_factory,
                runtime_settings,
                life_pack_id,
            )
    except (
        ConfigurationError,
        DatabasePreflightError,
        DeliveryError,
        SQLAlchemyError,
    ) as exc:
        abort_operation(exc)
    echo_json(payload)
