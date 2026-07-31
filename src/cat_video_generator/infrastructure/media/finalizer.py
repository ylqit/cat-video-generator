"""条件式 FFmpeg 后期。

该模块只拥有多片段兼容性判断、最小编码策略和尾帧抽取。单次成片技术 QC
通过时不会进入这里，避免无条件重编码供应商原始视频。
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

from ...application.ports import FinalizedMedia, StoredAsset


class MediaFinalizationError(RuntimeError):
    """FFmpeg 无法在不丢失片段或音轨的前提下完成后期。"""


class FfmpegMediaFinalizer:
    """根据两段媒体的真实属性选择 stream copy、音频重编码或完整转码。"""

    def __init__(self, *, ffmpeg_path: Path, work_root: Path) -> None:
        self._ffmpeg_path = ffmpeg_path.expanduser().resolve()
        self._work_root = work_root.expanduser().resolve()

    def concat(
        self,
        parts: tuple[StoredAsset, StoredAsset],
        *,
        target_duration_seconds: int,
    ) -> FinalizedMedia:
        self._work_root.mkdir(parents=True, exist_ok=True)
        durations = [item.metadata.get("durationMs") for item in parts]
        if not all(isinstance(value, int) and value > 0 for value in durations):
            raise MediaFinalizationError("片段缺少可验证的durationMs")
        total_duration_ms = sum(int(value) for value in durations)
        target_duration_ms = target_duration_seconds * 1000
        if total_duration_ms < target_duration_ms - 800:
            raise MediaFinalizationError("两个片段累计时长明显短于Episode计划时长")
        duration_trimmed = total_duration_ms > min(15000, target_duration_ms)
        token = uuid.uuid4().hex
        concat_file = self._work_root / f".concat-{token}.txt"
        partial = self._work_root / f".concat-{token}.part"
        output = self._work_root / f".concat-{token}.mp4"
        concat_file.write_text(
            "\n".join(f"file '{_concat_path(item.path)}'" for item in parts),
            encoding="utf-8",
        )
        failures: list[str] = []
        try:
            for policy in _policy_candidates(parts, force_transcode=duration_trimmed):
                codec_args = {
                    "stream_copy": ["-c", "copy"],
                    "audio_transcode": [
                        "-c:v",
                        "copy",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "192k",
                    ],
                    "transcode": [
                        "-c:v",
                        "libx264",
                        "-preset",
                        "medium",
                        "-crf",
                        "18",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "192k",
                    ],
                }[policy]
                trim_args = (
                    ["-t", str(target_duration_seconds)]
                    if duration_trimmed
                    else []
                )
                try:
                    _run_ffmpeg(
                        self._ffmpeg_path,
                        [
                            "-f",
                            "concat",
                            "-safe",
                            "0",
                            "-i",
                            str(concat_file),
                            *codec_args,
                            *trim_args,
                            "-movflags",
                            "+faststart",
                            "-f",
                            "mp4",
                            str(partial),
                        ],
                    )
                    partial.replace(output)
                    return FinalizedMedia(
                        path=output,
                        policy=policy,
                        duration_trimmed=duration_trimmed,
                    )
                except MediaFinalizationError as exc:
                    failures.append(f"{policy}: {exc}")
                    partial.unlink(missing_ok=True)
            raise MediaFinalizationError(
                "FFmpeg全部后期策略失败: " + " | ".join(failures)
            )
        except OSError as exc:
            raise MediaFinalizationError(f"多片段成片落盘失败: {exc}") from exc
        finally:
            concat_file.unlink(missing_ok=True)
            partial.unlink(missing_ok=True)

    def extract_last_frame(self, source: StoredAsset) -> Path:
        """从已审核片段提取真实尾帧，供需要连续空间的下一段作为首帧。"""

        self._work_root.mkdir(parents=True, exist_ok=True)
        output = self._work_root / f".tail-{uuid.uuid4().hex}.png"
        try:
            _run_ffmpeg(
                self._ffmpeg_path,
                [
                    "-sseof",
                    "-0.08",
                    "-i",
                    str(source.path),
                    "-frames:v",
                    "1",
                    "-f",
                    "image2",
                    str(output),
                ],
            )
            return output
        except OSError as exc:
            output.unlink(missing_ok=True)
            raise MediaFinalizationError(f"片段尾帧落盘失败: {exc}") from exc

    def extract_review_frames(
        self,
        source: StoredAsset,
        *,
        count: int,
    ) -> tuple[Path, ...]:
        """均匀抽取诊断帧；返回的临时图片由调用者在审核请求后删除。"""

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
            frames = tuple(
                sorted(self._work_root.glob(f".review-{token}-*.png"))
            )
            if len(frames) != count:
                raise MediaFinalizationError(
                    f"期望抽取{count}帧，实际得到{len(frames)}帧"
                )
            return frames
        except Exception:
            for frame in self._work_root.glob(f".review-{token}-*.png"):
                frame.unlink(missing_ok=True)
            raise


def _policy_candidates(
    parts: tuple[StoredAsset, StoredAsset],
    *,
    force_transcode: bool,
) -> tuple[str, ...]:
    """按最少重编码优先返回升级链；裁剪时直接转码保证时长边界。"""

    if force_transcode:
        return ("transcode",)
    video_keys = ("videoCodec", "width", "height", "frameRate", "timeBase", "pixelFormat")
    audio_keys = ("audioCodec", "audioSampleRate", "audioChannels", "audioChannelLayout")
    first, second = (item.metadata for item in parts)
    video_compatible = all(first.get(key) == second.get(key) for key in video_keys)
    audio_compatible = all(first.get(key) == second.get(key) for key in audio_keys)
    if video_compatible and audio_compatible:
        return ("stream_copy", "audio_transcode", "transcode")
    if video_compatible:
        return ("audio_transcode", "transcode")
    return ("transcode",)


def _concat_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "'\\''")


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
        raise MediaFinalizationError(f"FFmpeg执行失败: {detail or exc}") from exc
