from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from PIL import Image


class MediaProcessingError(RuntimeError):
    """Raised when a provider result cannot be safely landed or validated."""


@dataclass(frozen=True, slots=True)
class LandedFile:
    path: Path
    sha256: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class VideoProbe:
    container: str | None
    video_codec: str | None
    audio_codec: str | None
    width: int | None
    height: int | None
    duration_ms: int | None
    has_audio: bool
    qc_status: str
    report: dict[str, Any]


def download_to_content_address(
    url: str,
    *,
    work_root: Path,
    asset_root: Path,
    suffix: str,
    client: httpx.Client | None = None,
    max_bytes: int = 2_000_000_000,
) -> LandedFile:
    work_root = work_root.expanduser().resolve()
    asset_root = asset_root.expanduser().resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    if (
        work_root.drive
        and asset_root.drive
        and work_root.drive.lower() != asset_root.drive.lower()
    ):
        raise MediaProcessingError(
            "MEDIA_WORK_ROOT and MEDIA_ASSET_ROOT must be on the same volume "
            "for atomic finalization."
        )
    temporary = work_root / f".download-{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    byte_size = 0
    owns_client = client is None
    http = client or httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(120.0, connect=15.0),
    )
    try:
        with http.stream("GET", url) as response:
            response.raise_for_status()
            with temporary.open("xb") as output:
                for chunk in response.iter_bytes():
                    if not chunk:
                        continue
                    output.write(chunk)
                    digest.update(chunk)
                    byte_size += len(chunk)
                    if byte_size > max_bytes:
                        raise MediaProcessingError(
                            f"Provider result exceeds {max_bytes} bytes."
                        )
                output.flush()
                os.fsync(output.fileno())
        if byte_size == 0:
            raise MediaProcessingError("Provider download returned an empty file.")
        sha256 = digest.hexdigest()
        normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
        destination = (
            asset_root
            / "generated"
            / "sha256"
            / sha256[:2]
            / f"{sha256}{normalized_suffix.lower()}"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            temporary.unlink()
        else:
            os.replace(temporary, destination)
        return LandedFile(
            path=destination,
            sha256=sha256,
            byte_size=byte_size,
        )
    except (OSError, httpx.HTTPError) as exc:
        raise MediaProcessingError(f"Provider result download failed: {exc}") from exc
    finally:
        if owns_client:
            http.close()
        if temporary.exists():
            temporary.unlink()


def inspect_image(path: Path) -> dict[str, Any]:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            image_format = (image.format or "").lower()
    except (OSError, ValueError) as exc:
        raise MediaProcessingError(
            f"Generated keyframe is not a valid image: {exc}"
        ) from exc
    if width < 1 or height < 1 or image_format not in {"png", "jpeg", "webp"}:
        raise MediaProcessingError(
            "Generated keyframe has unsupported media properties."
        )
    return {
        "format": image_format,
        "width": width,
        "height": height,
        "valid": True,
    }


def probe_video(
    path: Path,
    *,
    ffprobe_path: Path,
    expected_duration_ms: int,
    expected_resolution: str,
) -> VideoProbe:
    expected_widths = {
        "480p": 480,
        "720p": 720,
        "1080p": 1080,
    }
    try:
        expected_width = expected_widths[expected_resolution]
    except KeyError as exc:
        raise MediaProcessingError(
            f"Unsupported expected video resolution: {expected_resolution!r}"
        ) from exc
    try:
        completed = subprocess.run(
            [
                str(ffprobe_path),
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        payload = json.loads(completed.stdout)
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ) as exc:
        raise MediaProcessingError(
            f"ffprobe could not inspect generated video: {exc}"
        ) from exc

    streams = payload.get("streams", [])
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        None,
    )
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"),
        None,
    )
    format_value = payload.get("format", {})
    duration_raw = format_value.get("duration")
    try:
        duration_ms = (
            None if duration_raw is None else round(float(duration_raw) * 1000)
        )
    except (TypeError, ValueError):
        duration_ms = None
    container = format_value.get("format_name")
    video_codec = None if video_stream is None else video_stream.get("codec_name")
    audio_codec = None if audio_stream is None else audio_stream.get("codec_name")
    width = None if video_stream is None else video_stream.get("width")
    height = None if video_stream is None else video_stream.get("height")
    failures: list[str] = []
    if video_stream is None:
        failures.append("missing_video_stream")
    if not container or "mp4" not in container:
        failures.append("container_not_mp4")
    if video_codec != "h264":
        failures.append("video_codec_not_h264")
    if audio_stream is None:
        failures.append("missing_audio_stream")
    elif audio_codec != "aac":
        failures.append("audio_codec_not_aac")
    portrait_nine_sixteen = (
        isinstance(width, int)
        and isinstance(height, int)
        and height > 0
        and abs((width / height) - (9 / 16)) <= 0.02
    )
    codec_alignment_tolerance = 16
    if (
        not isinstance(width, int)
        or abs(width - expected_width) > codec_alignment_tolerance
        or not portrait_nine_sixteen
    ):
        failures.append(f"resolution_not_{expected_resolution}_9x16")
    if duration_ms is None or not 8000 <= duration_ms <= 15000:
        failures.append("duration_outside_product_range")
    elif abs(duration_ms - expected_duration_ms) > 1000:
        failures.append("duration_differs_from_plan")
    report = {
        "failures": failures,
        "formatName": container,
        "videoCodec": video_codec,
        "audioCodec": audio_codec,
        "width": width,
        "height": height,
        "durationMs": duration_ms,
        "expectedDurationMs": expected_duration_ms,
        "expectedResolution": expected_resolution,
        "resolutionAlignmentTolerancePx": codec_alignment_tolerance,
        "hasAudio": audio_stream is not None,
    }
    return VideoProbe(
        container=container,
        video_codec=video_codec,
        audio_codec=audio_codec,
        width=width,
        height=height,
        duration_ms=duration_ms,
        has_audio=audio_stream is not None,
        qc_status="passed" if not failures else "failed",
        report=report,
    )
