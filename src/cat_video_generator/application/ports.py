"""Provider and local-media values shared by Creator application boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class CreativeDirectorResult:
    payload: dict[str, Any] | str
    response_id: str
    model: str
    request_hash: str


@dataclass(frozen=True, slots=True)
class ImageResult:
    url: str
    model: str


@dataclass(frozen=True, slots=True)
class VideoTaskResult:
    task_id: str
    status: str
    video_url: str | None = None
    last_frame_url: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    model: str | None = None
    created_at: datetime | None = None
    duration_seconds: int | None = None
    ratio: str | None = None
    resolution: str | None = None
    generate_audio: bool | None = None


class GatewayError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool,
        submission_unknown: bool = False,
        request_id: str | None = None,
        timed_out: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.submission_unknown = submission_unknown
        self.request_id = request_id
        self.timed_out = timed_out


@dataclass(frozen=True, slots=True)
class LandedAsset:
    path: Path
    sha256: str
    byte_size: int
