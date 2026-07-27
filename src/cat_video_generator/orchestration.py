"""Compatibility exports for the generation application boundary."""

from .generation.continuity import resolve_continuation
from .generation.errors import OrchestrationError
from .generation.gateway import ArkGateway
from .generation.service import PackGenerationService

ProviderBoundary = ArkGateway


class ArkOrchestrator(PackGenerationService):
    """Retain the original constructor while callers move to ArkGateway."""

    def __init__(self, session_factory, settings, *, provider=None) -> None:
        super().__init__(session_factory, settings, gateway=provider)

__all__ = [
    "ArkGateway",
    "ArkOrchestrator",
    "OrchestrationError",
    "PackGenerationService",
    "ProviderBoundary",
    "resolve_continuation",
]
