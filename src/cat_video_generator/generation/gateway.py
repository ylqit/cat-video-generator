from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..ark_provider import ArkImageResult, ArkVideoSubmission, ArkVideoTask


class ArkGateway(Protocol):
    """The concrete Ark interaction boundary used by generation services."""

    def generate_keyframe(
        self,
        *,
        prompt: str,
        reference_paths: tuple[Path, ...],
    ) -> ArkImageResult: ...

    def create_video(
        self,
        *,
        prompt: str,
        duration_ms: int,
        visual_input_mode: str,
        input_paths: tuple[Path, ...],
    ) -> ArkVideoSubmission: ...

    def get_video_task(self, task_id: str) -> ArkVideoTask: ...

    def list_video_tasks(
        self,
        *,
        model: str,
        page_size: int = 100,
    ) -> tuple[ArkVideoTask, ...]: ...
