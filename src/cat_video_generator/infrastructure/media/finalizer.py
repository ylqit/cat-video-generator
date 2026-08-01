"""最终视频语义诊断所需的FFmpeg抽帧边界。

single-pass供应商MP4通过技术QC后直接落盘。本模块不拼接、不转码，也不修改
成片；抽取的临时帧只用于Ark语义诊断，并由调用方在请求结束后删除。
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

from ...application.ports import StoredAsset


class FrameExtractionError(RuntimeError):
    """FFmpeg无法稳定抽取指定数量的诊断帧。"""


class FfmpegFrameExtractor:
    """从一个已通过技术QC的视频均匀抽取只读诊断帧。"""

    def __init__(self, *, ffmpeg_path: Path, work_root: Path) -> None:
        self._ffmpeg_path = ffmpeg_path.expanduser().resolve()
        self._work_root = work_root.expanduser().resolve()

    def extract_review_frames(
        self,
        source: StoredAsset,
        *,
        count: int,
    ) -> tuple[Path, ...]:
        """均匀抽帧；临时图片的删除责任属于调用方。"""

        if not 4 <= count <= 12:
            raise ValueError("视频语义诊断抽帧数量必须在4至12之间")
        duration_ms = source.metadata.get("durationMs")
        if not isinstance(duration_ms, int) or duration_ms <= 0:
            raise ValueError("视频资产缺少可用于均匀抽帧的durationMs")
        self._work_root.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        pattern = self._work_root / f".review-{token}-%02d.png"
        frame_rate = count / (duration_ms / 1000)
        try:
            _run_ffmpeg(
                self._ffmpeg_path,
                [
                    "-i",
                    str(source.path),
                    "-vf",
                    f"fps={frame_rate:.8f}",
                    "-frames:v",
                    str(count),
                    str(pattern),
                ],
            )
            frames = tuple(sorted(self._work_root.glob(f".review-{token}-*.png")))
            if len(frames) != count:
                raise FrameExtractionError(
                    f"期望抽取{count}帧，实际得到{len(frames)}帧"
                )
            return frames
        except Exception:
            for frame in self._work_root.glob(f".review-{token}-*.png"):
                frame.unlink(missing_ok=True)
            raise


def _run_ffmpeg(executable: Path, arguments: list[str]) -> None:
    try:
        subprocess.run(
            [str(executable), "-hide_banner", "-loglevel", "error", "-y", *arguments],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=600,
        )
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or "").strip()[-1000:]
        raise FrameExtractionError(f"FFmpeg抽帧失败: {detail or exc}") from exc
