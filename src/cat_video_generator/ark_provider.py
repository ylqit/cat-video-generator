from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from volcenginesdkarkruntime import Ark
from volcenginesdkarkruntime._exceptions import (
    ArkAPIConnectionError,
    ArkAPIError,
    ArkAPITimeoutError,
)

from .config import RuntimeSettings


@dataclass(frozen=True, slots=True)
class ArkImageResult:
    url: str
    model: str
    generated_images: int | None


@dataclass(frozen=True, slots=True)
class ArkVideoSubmission:
    task_id: str


@dataclass(frozen=True, slots=True)
class ArkVideoTask:
    task_id: str
    status: str
    video_url: str | None
    error_code: str | None
    error_message: str | None
    model: str | None
    duration_seconds: int | None
    resolution: str | None
    ratio: str | None
    generate_audio: bool | None


class ArkProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool,
        submission_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.submission_unknown = submission_unknown


class ArkMediaProvider:
    """The only runtime provider boundary: Volcengine Ark Seedream + Seedance."""

    def __init__(
        self,
        settings: RuntimeSettings,
        *,
        client: Any | None = None,
    ) -> None:
        if not settings.ark_api_key:
            raise ArkProviderError(
                "ARK_API_KEY is required.",
                code="missing_api_key",
                retryable=False,
            )
        self._settings = settings
        self._client = client or Ark(
            api_key=settings.ark_api_key,
            base_url=settings.ark_base_url,
        )

    def generate_keyframe(
        self,
        *,
        prompt: str,
        reference_paths: tuple[Path, ...],
    ) -> ArkImageResult:
        try:
            response = self._client.images.generate(
                model=self._settings.ark_image_model,
                prompt=prompt,
                image=[_image_data_url(path) for path in reference_paths],
                response_format="url",
                size="2K",
                watermark=False,
                output_format="png",
                sequential_image_generation="disabled",
                timeout=120.0,
            )
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=True) from exc
        if not response.data or not response.data[0].url:
            raise ArkProviderError(
                "Ark Seedream returned no downloadable image URL.",
                code="empty_image_result",
                retryable=False,
            )
        usage = getattr(response, "usage", None)
        return ArkImageResult(
            url=response.data[0].url,
            model=getattr(response, "model", self._settings.ark_image_model),
            generated_images=(
                None if usage is None else getattr(usage, "generated_images", None)
            ),
        )

    def create_video(
        self,
        *,
        prompt: str,
        duration_ms: int,
        visual_input_mode: str,
        input_paths: tuple[Path, ...],
    ) -> ArkVideoSubmission:
        if duration_ms % 1000:
            raise ArkProviderError(
                "Seedance duration must be an integer number of seconds.",
                code="invalid_duration",
                retryable=False,
            )
        roles = {
            "direct_references": ("reference_image",) * len(input_paths),
            "generated_first_frame": ("first_frame",),
            "generated_first_last_frames": ("first_frame", "last_frame"),
        }
        try:
            expected_roles = roles[visual_input_mode]
        except KeyError as exc:
            raise ArkProviderError(
                f"Unsupported visual input mode: {visual_input_mode}",
                code="invalid_visual_input_mode",
                retryable=False,
            ) from exc
        if len(input_paths) != len(expected_roles):
            raise ArkProviderError(
                f"{visual_input_mode} received {len(input_paths)} image(s), "
                f"expected {len(expected_roles)}.",
                code="invalid_visual_input_count",
                retryable=False,
            )
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": _image_data_url(path)},
                "role": role,
            }
            for path, role in zip(input_paths, expected_roles, strict=True)
        )
        try:
            response = self._client.content_generation.tasks.create(
                model=self._settings.ark_video_model,
                content=content,
                return_last_frame=False,
                generate_audio=True,
                watermark=False,
                resolution="720p",
                ratio="9:16",
                duration=duration_ms // 1000,
                timeout=120.0,
            )
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=True) from exc
        if not response.id:
            raise ArkProviderError(
                "Ark Seedance returned no task ID.",
                code="empty_task_id",
                retryable=False,
            )
        return ArkVideoSubmission(task_id=response.id)

    def get_video_task(self, task_id: str) -> ArkVideoTask:
        try:
            task = self._client.content_generation.tasks.get(
                task_id=task_id,
                timeout=120.0,
            )
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=False) from exc
        content = getattr(task, "content", None)
        error = getattr(task, "error", None)
        return ArkVideoTask(
            task_id=task.id,
            status=task.status,
            video_url=(
                None if content is None else getattr(content, "video_url", None)
            ),
            error_code=None if error is None else getattr(error, "code", None),
            error_message=(
                None if error is None else getattr(error, "message", None)
            ),
            model=getattr(task, "model", None),
            duration_seconds=getattr(task, "duration", None),
            resolution=getattr(task, "resolution", None),
            ratio=getattr(task, "ratio", None),
            generate_audio=getattr(task, "generate_audio", None),
        )


def _image_data_url(path: Path) -> str:
    suffix = path.suffix.lower()
    mime_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix)
    if mime_type is None:
        raise ArkProviderError(
            f"Unsupported Ark input image type: {suffix}",
            code="unsupported_image_type",
            retryable=False,
        )
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError as exc:
        raise ArkProviderError(
            f"Cannot read Ark input image: {path}",
            code="image_read_failed",
            retryable=False,
        ) from exc
    return f"data:{mime_type};base64,{encoded}"


def _provider_error(exc: ArkAPIError, *, submission: bool) -> ArkProviderError:
    body = getattr(exc, "body", None)
    nested_error = (
        body.get("error")
        if isinstance(body, dict) and isinstance(body.get("error"), dict)
        else {}
    )
    code = (
        getattr(exc, "code", None)
        or nested_error.get("code")
        or type(exc).__name__
    )
    message = (
        nested_error.get("message")
        or getattr(exc, "message", None)
        or "Ark request failed."
    )
    if isinstance(exc, (ArkAPIConnectionError, ArkAPITimeoutError)):
        return ArkProviderError(
            message,
            code=code,
            retryable=not submission,
            submission_unknown=submission,
        )
    status_code = getattr(exc, "status_code", None)
    normalized_code = code.lower()
    quota_error = any(
        marker in normalized_code
        for marker in ("quota", "balance", "insufficient", "account")
    )
    retryable = (
        status_code is not None
        and (status_code >= 500 or status_code == 429)
        and not quota_error
    )
    return ArkProviderError(
        message,
        code=code,
        retryable=retryable,
        submission_unknown=False,
    )
