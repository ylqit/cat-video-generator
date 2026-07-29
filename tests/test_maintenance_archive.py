from __future__ import annotations

from pathlib import Path

import pytest

from cat_video_generator.infrastructure.db.maintenance import (
    ARCHIVE_FORMAT,
    MaintenanceSafetyError,
    _sanitize,
)


def test_archive_sanitizer_keeps_prompt_but_removes_secrets() -> None:
    value = {
        "prompt": "包含 https://example.com 的导演原文",
        "apiKey": "secret",
        "videoUrl": "https://signed.example/video",
        "nested": {"authorization": "bearer secret"},
    }
    assert _sanitize(value) == {
        "prompt": value["prompt"],
        "nested": {},
    }
    assert ARCHIVE_FORMAT.endswith("-v1")


def test_archive_safety_error_is_explicit(tmp_path: Path) -> None:
    missing = tmp_path / "archive.json"
    assert not missing.exists()
    with pytest.raises(MaintenanceSafetyError):
        raise MaintenanceSafetyError(f"归档不存在：{missing}")
