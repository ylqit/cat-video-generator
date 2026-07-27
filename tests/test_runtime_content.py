from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cat_video_generator.cli import app
from cat_video_generator.config import ConfigurationError, RuntimeSettings
from cat_video_generator.contracts import (
    ContentValidationError,
    canonical_content_hash,
    validate_daily_life_pack,
)
from cat_video_generator.generation import PackGenerationService
from cat_video_generator.state import StateTransitionError, transition_pack

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRAVEL_EXAMPLE = (
    PROJECT_ROOT / "content" / "examples" / "daily-life-pack.travel.example.json"
)


class Record:
    def __init__(self, status: str) -> None:
        self.status = status


def load_travel() -> dict:
    return json.loads(TRAVEL_EXAMPLE.read_text(encoding="utf-8"))


def test_runtime_settings_require_ffprobe_key_and_paid_acknowledgement(
    tmp_path: Path,
) -> None:
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffprobe = tmp_path / "ffprobe.exe"
    ffmpeg.write_bytes(b"exe")
    ffprobe.write_bytes(b"exe")
    settings = RuntimeSettings.from_env(
        {
            "ARK_ACCESS_MODE": "standard",
            "ARK_API_KEY": "secret",
            "ARK_BASE_URL": "https://ark.cn-beijing.volces.com/api/v3",
            "FFMPEG_PATH": str(tmp_path / "missing-ffmpeg.exe"),
            "FFPROBE_PATH": str(ffprobe),
        }
    )

    settings.validate_for_generation(allow_paid_generation=True)
    settings.validate_for_ark_access()
    assert settings.ffmpeg_path is None
    with pytest.raises(ConfigurationError, match="allow-paid-generation"):
        settings.validate_for_generation(allow_paid_generation=False)

    missing_key = RuntimeSettings.from_env(
        {
            "ARK_ACCESS_MODE": "standard",
            "ARK_BASE_URL": "https://ark.cn-beijing.volces.com/api/v3",
            "FFMPEG_PATH": str(ffmpeg),
            "FFPROBE_PATH": str(ffprobe),
        }
    )
    with pytest.raises(ConfigurationError, match="ARK_API_KEY"):
        missing_key.validate_for_generation(allow_paid_generation=True)

    missing_probe = RuntimeSettings.from_env(
        {
            "ARK_ACCESS_MODE": "standard",
            "ARK_API_KEY": "secret",
            "ARK_BASE_URL": "https://ark.cn-beijing.volces.com/api/v3",
            "FFMPEG_PATH": str(ffmpeg),
            "FFPROBE_PATH": str(tmp_path / "missing-ffprobe.exe"),
        }
    )
    with pytest.raises(ConfigurationError, match="ffprobe"):
        missing_probe.validate_for_generation(allow_paid_generation=True)


def test_generation_service_rejects_incompatible_agent_plan_before_database() -> None:
    incompatible = RuntimeSettings.from_env(
        {
            "ARK_ACCESS_MODE": "agent_plan",
            "ARK_AGENT_PLAN_TIER": "medium",
            "ARK_API_KEY": "test-only-key",
            "ARK_BASE_URL": "https://ark.cn-beijing.volces.com/api/plan/v3",
            "ARK_IMAGE_MODEL": "doubao-seedream-5.0-lite",
            "ARK_VIDEO_MODEL": "doubao-seedance-2.0-mini",
        }
    )

    with pytest.raises(ConfigurationError, match="large or max"):
        PackGenerationService(None, incompatible, gateway=object())  # type: ignore[arg-type]


def test_cross_object_validation_rejects_context_drift() -> None:
    value = load_travel()
    value["slots"]["noon"]["contextReads"]["weather"] = "snow"

    with pytest.raises(ContentValidationError, match="weather"):
        validate_daily_life_pack(value)


def test_content_hash_is_stable_across_json_key_order() -> None:
    value = load_travel()
    reordered = json.loads(json.dumps(value, sort_keys=True, ensure_ascii=False))

    assert canonical_content_hash(value) == canonical_content_hash(reordered)


def test_pack_state_machine_rejects_skipped_states() -> None:
    record = Record("candidate")
    transition_pack(record, "approved")
    transition_pack(record, "frozen")
    assert record.status == "frozen"

    with pytest.raises(StateTransitionError):
        transition_pack(record, "ready")


def test_validate_pack_cli_is_database_and_provider_free() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["validate-pack", str(TRAVEL_EXAMPLE)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["lifePackId"] == load_travel()["lifePackId"]
    assert len(payload["contentHash"]) == 64


def test_validate_pack_rejects_bad_dependency_without_database(
    tmp_path: Path,
) -> None:
    value = deepcopy(load_travel())
    value["slots"]["evening"]["clipKind"] = "continuation"
    value["slots"]["evening"]["continuityMode"] = "follows_previous"
    value["slots"]["evening"]["dependsOnEpisodeIds"] = ["missing"]
    value["slots"]["evening"]["fallbackEpisodeId"] = "missing-fallback"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ContentValidationError):
        validate_daily_life_pack(value)


def test_review_cli_requires_exactly_one_decision() -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["review", "asset", "--reason", "checked"],
    )

    assert result.exit_code == 2
    assert "exactly one" in result.stderr


def test_status_accepts_optional_life_pack_id_argument() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["status", "--help"])

    assert result.exit_code == 0
    assert "[life_pack_id]" in result.stdout


def test_reconcile_job_is_a_non_paid_explicit_cli_entrypoint() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["reconcile-job", "--help"])

    assert result.exit_code == 0
    assert "--provider-task-id" in result.stdout
    assert "--allow-paid-generation" not in result.stdout
