from __future__ import annotations

from pathlib import Path

import pytest

from cat_video_generator.config import ConfigurationError, RuntimeSettings


def _runtime_env(tmp_path: Path) -> dict[str, str]:
    return {
        "ARK_API_KEY": "test-key",
        "PATH": str(tmp_path),
        "MEDIA_WORK_ROOT": str(tmp_path / "work"),
        "MEDIA_ASSET_ROOT": str(tmp_path / "assets"),
    }


def test_invalid_explicit_ffmpeg_paths_fall_back_to_path(tmp_path: Path) -> None:
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffprobe = tmp_path / "ffprobe.exe"
    ffmpeg.write_bytes(b"")
    ffprobe.write_bytes(b"")
    env = {
        **_runtime_env(tmp_path),
        "FFMPEG_PATH": str(tmp_path / "old" / "ffmpeg.exe"),
        "FFPROBE_PATH": str(tmp_path / "old" / "ffprobe.exe"),
    }

    settings = RuntimeSettings.from_env(env)

    assert settings.ffmpeg_path == ffmpeg
    assert settings.ffprobe_path == ffprobe
    assert len(settings.configuration_warnings) == 2


def test_runtime_report_distinguishes_generation_and_composition_readiness(
    tmp_path: Path,
) -> None:
    env = _runtime_env(tmp_path)
    settings = RuntimeSettings.from_env(env)

    report = settings.preflight_report()

    assert report["arkReady"] is True
    assert report["ffmpegAvailable"] is False
    assert report["ffprobeAvailable"] is False
    assert report["videoGenerationReady"] is False
    assert report["localCompositionReady"] is False


def test_video_preflight_rejects_missing_media_tools() -> None:
    settings = RuntimeSettings.from_env({"ARK_API_KEY": "test-key", "PATH": ""})

    with pytest.raises(ConfigurationError, match="ffprobe"):
        settings.validate_for_video_generation(allow_paid_generation=True)
