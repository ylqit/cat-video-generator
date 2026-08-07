"""逐镜头生成所需的FFmpeg边界：抽帧、尾帧提取与同规格片段拼接。

诊断帧与尾帧的临时文件删除责任属于调用方；拼接优先无损copy，仅当编码参数
不一致时回退重编码。
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

    def extract_last_frame(self, source: Path, *, target: Path) -> Path:
        """提取视频最后一帧作为下一镜头的首帧锚点。"""

        target.parent.mkdir(parents=True, exist_ok=True)
        _run_ffmpeg(
            self._ffmpeg_path,
            [
                "-sseof",
                "-0.15",
                "-i",
                str(source.expanduser().resolve()),
                "-frames:v",
                "1",
                "-update",
                "1",
                str(target.expanduser().resolve()),
            ],
        )
        if not target.is_file() or target.stat().st_size == 0:
            raise FrameExtractionError("尾帧提取没有产出可用图片")
        return target

    def concat_videos(self, sources: tuple[Path, ...], *, target: Path) -> Path:
        """按序拼接同模型同规格的镜头片段；优先无损copy。"""

        if len(sources) < 2:
            raise ValueError("拼接至少需要两个镜头片段")
        self._work_root.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        list_file = self._work_root / f".concat-{uuid.uuid4().hex}.txt"
        list_file.write_text(
            "\n".join(
                f"file '{str(path.expanduser().resolve())}'" for path in sources
            ),
            encoding="utf-8",
        )
        resolved_target = target.expanduser().resolve()
        try:
            try:
                _run_ffmpeg(
                    self._ffmpeg_path,
                    [
                        "-f",
                        "concat",
                        "-safe",
                        "0",
                        "-i",
                        str(list_file),
                        "-c",
                        "copy",
                        str(resolved_target),
                    ],
                )
            except FrameExtractionError:
                # 编码参数不一致时回退重编码，保证拼接总能完成。
                _run_ffmpeg(
                    self._ffmpeg_path,
                    [
                        "-f",
                        "concat",
                        "-safe",
                        "0",
                        "-i",
                        str(list_file),
                        "-c:v",
                        "libx264",
                        "-crf",
                        "18",
                        "-preset",
                        "medium",
                        "-c:a",
                        "aac",
                        str(resolved_target),
                    ],
                )
        finally:
            list_file.unlink(missing_ok=True)
        if not resolved_target.is_file() or resolved_target.stat().st_size == 0:
            raise FrameExtractionError("镜头片段拼接没有产出可用视频")
        return resolved_target


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
