from __future__ import annotations

import pytest

from cat_video_generator.config import (
    ConfigurationError,
    DatabaseOperation,
    DatabaseSettings,
    RuntimeSettings,
)


def _database_env() -> dict[str, str]:
    return {
        "CAT_VIDEO_DB_HOST": "127.0.0.1",
        "CAT_VIDEO_DB_PORT": "5432",
        "CAT_VIDEO_DB_NAME": "vedio-appdb",
        "CAT_VIDEO_DB_USER": "postgres",
        "CAT_VIDEO_DB_PASSWORD": "secret",
        "CAT_VIDEO_DB_SSLMODE": "disable",
        "CAT_VIDEO_DB_SCHEMA": "cat_video",
    }


def test_plaintext_runtime_needs_exact_explicit_gate() -> None:
    values = _database_env()
    settings = DatabaseSettings.from_env(values)
    with pytest.raises(ConfigurationError):
        settings.validate_for(DatabaseOperation.RUNTIME)
    values["CAT_VIDEO_ALLOW_INSECURE_RUNTIME"] = "true"
    settings = DatabaseSettings.from_env(values)
    settings.validate_for(DatabaseOperation.RUNTIME)
    settings.validate_for(DatabaseOperation.READ_ONLY_SMOKE)


def test_insecure_gate_does_not_allow_another_database() -> None:
    values = _database_env()
    values["CAT_VIDEO_DB_NAME"] = "another"
    values["CAT_VIDEO_ALLOW_INSECURE_RUNTIME"] = "true"
    with pytest.raises(ConfigurationError):
        DatabaseSettings.from_env(values).validate_for(DatabaseOperation.RUNTIME)


def test_runtime_settings_require_paid_permission_and_valid_resolution() -> None:
    values = {
        "ARK_API_KEY": "test-key",
        "ARK_ACCESS_MODE": "standard",
        "ARK_BASE_URL": "https://ark.cn-beijing.volces.com/api/v3",
        "ARK_VIDEO_RESOLUTION": "720p",
        "DAILY_PLAN_CANDIDATE_COUNT": "1",
    }
    settings = RuntimeSettings.from_env(values)
    assert settings.keyframe_review_mode.value == "semantic_auto"
    assert settings.configuration_warnings == ()
    with pytest.raises(ConfigurationError, match="allow-paid-generation"):
        settings.validate_for_generation(allow_paid_generation=False)
    values["ARK_VIDEO_RESOLUTION"] = "1080p"
    with pytest.raises(ConfigurationError, match="480p"):
        RuntimeSettings.from_env(values).validate_for_ark_access()


def test_legacy_auto_review_mode_maps_with_warning() -> None:
    settings = RuntimeSettings.from_env(
        {
            "ARK_API_KEY": "test-key",
            "KEYFRAME_REVIEW_MODE": "auto",
        }
    )
    assert settings.keyframe_review_mode.value == "technical_auto"
    assert "更新.env" in settings.configuration_warnings[0]


def test_runtime_accepts_full_seedance_profile_and_rejects_unknown_model() -> None:
    values = {
        "ARK_API_KEY": "test-key",
        "ARK_VIDEO_MODEL": "doubao-seedance-2-0-260128",
    }
    RuntimeSettings.from_env(values).validate_for_ark_access()
    values["ARK_VIDEO_MODEL"] = "unknown-video-model"
    with pytest.raises(ConfigurationError, match="能力档案"):
        RuntimeSettings.from_env(values).validate_for_ark_access()


def test_event_seed_root_resolves_from_explicit_config_root(tmp_path) -> None:
    config_root = tmp_path / "project"
    seed_root = config_root / "content" / "events"
    seed_root.mkdir(parents=True)

    settings = RuntimeSettings.from_env(
        {"CAT_VIDEO_EVENT_SEED_ROOT": "content/events"},
        config_root=config_root,
    )

    assert settings.event_seed_root == seed_root.resolve()
    assert settings.configuration_warnings == ()
