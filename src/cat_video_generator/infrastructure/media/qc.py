"""图片和视频技术QC。

通过的视频保持供应商原MP4直通；本模块只检查，不执行无条件重编码。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image


class MediaQcError(RuntimeError):
    """媒体不可读取或不满足交付技术要求。"""


class FfprobeMediaProbe:
    """Pillow图片检查与ffprobe视频检查。"""

    def __init__(self, ffprobe_path: Path | None) -> None:
        self._ffprobe_path = ffprobe_path

    def inspect_image(self, path: Path) -> dict[str, Any]:
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                width, height = image.size
                image_format = (image.format or "").lower()
                rgb = image.convert("RGB")
                edges = (
                    [rgb.getpixel((0, y)) for y in range(height)]
                    + [rgb.getpixel((width - 1, y)) for y in range(height)]
                    + [rgb.getpixel((x, 0)) for x in range(width)]
                    + [rgb.getpixel((x, height - 1)) for x in range(width)]
                )
        except (OSError, ValueError) as exc:
            raise MediaQcError(f"图片不可读取: {exc}") from exc
        if image_format not in {"png", "jpeg", "webp"}:
            raise MediaQcError(f"不支持的图片格式: {image_format}")
        dark_edge_ratio = sum(max(pixel) <= 8 for pixel in edges) / len(edges)
        return {
            "passed": True,
            "format": image_format,
            "width": width,
            "height": height,
            "ratio": width / height,
            "darkEdgeRatio": round(dark_edge_ratio, 4),
            "blackBorderDetected": dark_edge_ratio >= 0.45,
        }

    def inspect_video(
        self,
        path: Path,
        *,
        expected_duration_seconds: int,
        expected_resolution: str,
        minimum_duration_seconds: int = 8,
        maximum_duration_seconds: int = 15,
        duration_tolerance_ms: int = 1000,
    ) -> dict[str, Any]:
        payload = self._ffprobe(path)
        streams = payload.get("streams", [])
        video = next(
            (item for item in streams if item.get("codec_type") == "video"),
            None,
        )
        audio = next(
            (item for item in streams if item.get("codec_type") == "audio"),
            None,
        )
        format_info = payload.get("format", {})
        try:
            duration_ms = round(float(format_info.get("duration")) * 1000)
        except (TypeError, ValueError):
            duration_ms = None
        width = None if video is None else video.get("width")
        height = None if video is None else video.get("height")
        expected_width = {"480p": 480, "720p": 720}.get(expected_resolution)
        failures: list[str] = []
        if video is None:
            failures.append("missing_video")
        if audio is None:
            failures.append("missing_audio")
        if "mp4" not in str(format_info.get("format_name", "")):
            failures.append("container_not_mp4")
        if video is not None and video.get("codec_name") != "h264":
            failures.append("video_codec_not_h264")
        if audio is not None and audio.get("codec_name") != "aac":
            failures.append("audio_codec_not_aac")
        if expected_width is None:
            failures.append("unsupported_expected_resolution")
        elif not isinstance(width, int) or abs(width - expected_width) > 16:
            failures.append("wrong_width")
        if (
            not isinstance(width, int)
            or not isinstance(height, int)
            or height <= 0
            or abs(width / height - 9 / 16) > 0.02
        ):
            failures.append("ratio_not_9_16")
        if (
            duration_ms is None
            or not minimum_duration_seconds * 1000
            <= duration_ms
            <= maximum_duration_seconds * 1000
            or abs(duration_ms - expected_duration_seconds * 1000)
            > duration_tolerance_ms
        ):
            failures.append("duration_invalid")
        return {
            "passed": not failures,
            "failures": failures,
            "container": format_info.get("format_name"),
            "videoCodec": None if video is None else video.get("codec_name"),
            "audioCodec": None if audio is None else audio.get("codec_name"),
            "frameRate": None if video is None else video.get("avg_frame_rate"),
            "timeBase": None if video is None else video.get("time_base"),
            "pixelFormat": None if video is None else video.get("pix_fmt"),
            "audioSampleRate": None if audio is None else audio.get("sample_rate"),
            "audioChannels": None if audio is None else audio.get("channels"),
            "audioChannelLayout": (
                None if audio is None else audio.get("channel_layout")
            ),
            "width": width,
            "height": height,
            "durationMs": duration_ms,
            "hasAudio": audio is not None,
        }

    def inspect_reference(
        self,
        path: Path,
        *,
        media_type: str,
    ) -> dict[str, Any]:
        """检查参考视频或音频可读取且时长满足Seedance输入要求。"""

        if media_type not in {"video", "audio"}:
            raise MediaQcError("参考媒体类型必须是video或audio")
        payload = self._ffprobe(path)
        streams = payload.get("streams", [])
        expected_stream = next(
            (item for item in streams if item.get("codec_type") == media_type),
            None,
        )
        format_info = payload.get("format", {})
        try:
            duration_seconds = float(format_info.get("duration"))
        except (TypeError, ValueError) as exc:
            raise MediaQcError("参考媒体缺少有效时长") from exc
        if expected_stream is None:
            raise MediaQcError(f"参考媒体缺少{media_type}轨道")
        if not 2 <= duration_seconds <= 15:
            raise MediaQcError("参考视频或音频时长必须在2至15秒")
        return {
            "passed": True,
            "mediaType": media_type,
            "durationSeconds": duration_seconds,
            "codec": expected_stream.get("codec_name"),
            "container": format_info.get("format_name"),
        }

    def _ffprobe(self, path: Path) -> dict[str, Any]:
        if self._ffprobe_path is None:
            raise MediaQcError("媒体检查需要配置ffprobe")
        try:
            completed = subprocess.run(
                [
                    str(self._ffprobe_path),
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
            return json.loads(completed.stdout)
        except (
            OSError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
        ) as exc:
            raise MediaQcError(f"ffprobe检查失败: {exc}") from exc
