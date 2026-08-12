"""Immutable local media storage and non-destructive FFmpeg operations."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import httpx
from PIL import Image

from ...application.ports import LandedAsset


class AssetStorageError(RuntimeError):
    pass


class LocalAssetStore:
    def __init__(
        self,
        *,
        work_root: Path,
        asset_root: Path,
        ffmpeg_path: Path | None = None,
        max_bytes: int = 2_000_000_000,
    ) -> None:
        self._work_root = work_root.expanduser().resolve()
        self._asset_root = asset_root.expanduser().resolve()
        self._ffmpeg_path = None if ffmpeg_path is None else ffmpeg_path.expanduser().resolve()
        self._max_bytes = max_bytes
        if (
            self._work_root.drive
            and self._asset_root.drive
            and self._work_root.drive.lower() != self._asset_root.drive.lower()
        ):
            raise AssetStorageError("work and asset roots must be on the same volume")

    def download(self, url: str, *, suffix: str) -> LandedAsset:
        self._work_root.mkdir(parents=True, exist_ok=True)
        temporary = self._work_root / f".download-{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        byte_size = 0
        try:
            with (
                httpx.Client(
                    follow_redirects=True, timeout=httpx.Timeout(120, connect=15)
                ) as client,
                client.stream("GET", url) as response,
            ):
                response.raise_for_status()
                with temporary.open("xb") as output:
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue
                        output.write(chunk)
                        digest.update(chunk)
                        byte_size += len(chunk)
                        if byte_size > self._max_bytes:
                            raise AssetStorageError("provider media exceeds configured size limit")
                    output.flush()
                    os.fsync(output.fileno())
            if byte_size == 0:
                raise AssetStorageError("provider returned an empty media file")
            extension = suffix if suffix.startswith(".") else f".{suffix}"
            return self._land_temp(temporary, digest.hexdigest(), byte_size, extension)
        except (OSError, httpx.HTTPError) as exc:
            raise AssetStorageError(f"media download failed: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)

    def import_local(self, path: Path) -> LandedAsset:
        source = path.expanduser().resolve()
        if not source.is_file():
            raise AssetStorageError(f"local media does not exist: {source}")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        destination = (
            self._asset_root
            / "imported"
            / "sha256"
            / digest[:2]
            / f"{digest}{source.suffix.lower()}"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary = destination.with_suffix(destination.suffix + ".part")
            shutil.copy2(source, temporary)
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
                temporary.unlink(missing_ok=True)
                raise AssetStorageError("copied media hash does not match source")
            os.replace(temporary, destination)
        return LandedAsset(destination, digest, destination.stat().st_size)

    def crop_local(self, path: Path, *, box: tuple[int, int, int, int]) -> LandedAsset:
        source = path.expanduser().resolve()
        self._work_root.mkdir(parents=True, exist_ok=True)
        temporary = self._work_root / f".crop-{uuid.uuid4().hex}.png"
        try:
            with Image.open(source) as image:
                left, top, right, bottom = box
                if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
                    raise AssetStorageError("crop box is outside the source image")
                image.crop(box).save(temporary, format="PNG")
            return self.import_local(temporary)
        finally:
            temporary.unlink(missing_ok=True)

    def concatenate_videos(self, paths: tuple[Path, ...]) -> LandedAsset:
        """Build an arbitrary-length single-track master with native clip audio.

        Video uses stable hard cuts.  Each source audio stream receives an 80ms
        edge fade before concat, avoiding clicks without changing EDL duration.
        """

        ffmpeg = self._require_ffmpeg()
        if not paths:
            raise AssetStorageError("a project sequence needs at least one clip")
        resolved = tuple(path.expanduser().resolve() for path in paths)
        if any(not path.is_file() for path in resolved):
            raise AssetStorageError("a project sequence source clip is missing")
        self._work_root.mkdir(parents=True, exist_ok=True)
        output = self._work_root / f".sequence-{uuid.uuid4().hex}.mp4"
        command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y"]
        for path in resolved:
            command.extend(("-i", str(path)))
        filters: list[str] = []
        inputs: list[str] = []
        for index in range(len(resolved)):
            filters.append(f"[{index}:v]setpts=PTS-STARTPTS,setsar=1,format=yuv420p[v{index}]")
            filters.append(
                f"[{index}:a]asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.08,"
                f"areverse,afade=t=in:st=0:d=0.08,areverse[a{index}]"
            )
            inputs.append(f"[v{index}][a{index}]")
        filters.append(f"{''.join(inputs)}concat=n={len(resolved)}:v=1:a=1[vout][aout]")
        command.extend(
            (
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[vout]",
                "-map",
                "[aout]",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(output),
            )
        )
        try:
            _run(command, timeout=1800, label="project sequence")
            return self.import_local(output)
        finally:
            output.unlink(missing_ok=True)

    def render_range_replacement(
        self,
        *,
        base_path: Path,
        replacement_path: Path,
        replacement_duration_ms: int,
        start_ms: int,
        end_ms: int,
    ) -> LandedAsset:
        """Replace image content for one interval and preserve the base audio track."""

        ffmpeg = self._require_ffmpeg()
        base = base_path.expanduser().resolve()
        replacement = replacement_path.expanduser().resolve()
        if not base.is_file() or not replacement.is_file():
            raise AssetStorageError("range edit source or replacement is missing")
        if not 0 <= start_ms < end_ms or replacement_duration_ms <= 0:
            raise AssetStorageError("range edit timing is invalid")
        self._work_root.mkdir(parents=True, exist_ok=True)
        output = self._work_root / f".range-edit-{uuid.uuid4().hex}.mp4"
        target_seconds = (end_ms - start_ms) / 1000
        replacement_seconds = replacement_duration_ms / 1000
        filters: list[str] = []
        inputs: list[str] = []
        if start_ms > 0:
            filters.append(
                f"[0:v]trim=0:{start_ms / 1000:.3f},setpts=PTS-STARTPTS,"
                "settb=AVTB,fps=30,setsar=1,format=yuv420p[vpre]"
            )
            inputs.append("[vpre]")
        ratio = target_seconds / replacement_seconds
        filters.append(
            f"[1:v]trim=0:{replacement_seconds:.3f},setpts={ratio:.9f}*(PTS-STARTPTS),"
            "settb=AVTB,fps=30,setsar=1,format=yuv420p[vreplace]"
        )
        inputs.append("[vreplace]")
        filters.append(
            f"[0:v]trim=start={end_ms / 1000:.3f},setpts=PTS-STARTPTS,"
            "settb=AVTB,fps=30,setsar=1,format=yuv420p[vpost]"
        )
        inputs.append("[vpost]")
        filters.append(f"{''.join(inputs)}concat=n={len(inputs)}:v=1:a=0[vout]")
        command = [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(base),
            "-i",
            str(replacement),
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[vout]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ]
        try:
            _run(command, timeout=900, label="range edit")
            return self.import_local(output)
        finally:
            output.unlink(missing_ok=True)

    def _land_temp(
        self, temporary: Path, sha256: str, byte_size: int, extension: str
    ) -> LandedAsset:
        destination = (
            self._asset_root / "generated" / "sha256" / sha256[:2] / f"{sha256}{extension.lower()}"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            temporary.unlink(missing_ok=True)
        else:
            os.replace(temporary, destination)
        return LandedAsset(destination, sha256, byte_size)

    def _require_ffmpeg(self) -> Path:
        if self._ffmpeg_path is None or not self._ffmpeg_path.is_file():
            raise AssetStorageError("FFmpeg is required for local video editing")
        return self._ffmpeg_path


def _run(command: list[str], *, timeout: int, label: str) -> None:
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = (
            exc.stderr.strip()[-1200:]
            if isinstance(exc, subprocess.CalledProcessError)
            else str(exc)
        )
        raise AssetStorageError(f"{label} failed: {detail}") from exc
