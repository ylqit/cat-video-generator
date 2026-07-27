from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cat_video_generator.cli import app
from cat_video_generator.config import (
    ArkAccessMode,
    ConfigurationError,
    DatabaseOperation,
    DatabaseSettings,
    RuntimeSettings,
    load_local_env,
)
from cat_video_generator.contracts import canonical_content_hash

AGENT_PLAN_URL = "https://ark.cn-beijing.volces.com/api/plan/v3"
STANDARD_ARK_URL = "https://ark.cn-beijing.volces.com/api/v3"


def settings(**overrides: object) -> DatabaseSettings:
    values = {
        "host": "db.example.test",
        "port": 5432,
        "database": "vedio-appdb",
        "user": "postgres",
        "password": "secret-with-special:@/",
        "sslmode": "require",
    }
    values.update(overrides)
    return DatabaseSettings(**values)


def test_url_is_built_without_exposing_password() -> None:
    configured = settings()

    assert configured.url.password == "secret-with-special:@/"
    assert "secret-with-special" not in configured.redacted_url
    assert "***" in configured.redacted_url


def test_unencrypted_remote_database_is_readonly_smoke_only() -> None:
    configured = settings(
        sslmode="disable",
        allow_insecure_readonly_smoke=True,
    )

    configured.validate_for(DatabaseOperation.READ_ONLY_SMOKE)
    with pytest.raises(ConfigurationError):
        configured.validate_for(DatabaseOperation.MIGRATION)
    with pytest.raises(ConfigurationError):
        configured.validate_for(DatabaseOperation.RUNTIME)


def test_insecure_test_database_requires_loopback_and_test_name() -> None:
    configured = settings(
        host="127.0.0.1",
        database="test_cat_video",
        sslmode="disable",
        allow_insecure_local_tests=True,
    )

    configured.validate_for(DatabaseOperation.TEST)
    configured.validate_for(DatabaseOperation.MIGRATION)
    configured.validate_for(DatabaseOperation.RUNTIME)

    with pytest.raises(ConfigurationError):
        settings(
            host="203.0.113.10",
            database="test_cat_video",
            sslmode="disable",
            allow_insecure_local_tests=True,
        ).validate_for(DatabaseOperation.TEST)


def test_remote_insecure_write_permission_is_scoped_to_validation_operation() -> None:
    configured = settings(
        sslmode="disable",
        schema="cat_video_validation_abcdef123456",
        allow_insecure_remote_write_test=True,
    )

    configured.validate_for(DatabaseOperation.REMOTE_VALIDATION)
    with pytest.raises(ConfigurationError):
        configured.validate_for(DatabaseOperation.MIGRATION)
    with pytest.raises(ConfigurationError):
        configured.validate_for(DatabaseOperation.RUNTIME)

    with pytest.raises(ConfigurationError):
        settings(
            sslmode="disable",
            schema="cat_video",
            allow_insecure_remote_write_test=True,
        ).validate_for(DatabaseOperation.REMOTE_VALIDATION)


def test_insecure_runtime_requires_exact_database_schema_and_explicit_flag() -> None:
    configured = settings(
        sslmode="disable",
        allow_insecure_runtime=True,
    )

    configured.validate_for(DatabaseOperation.MIGRATION)
    configured.validate_for(DatabaseOperation.RUNTIME)
    with pytest.raises(ConfigurationError):
        configured.validate_for(DatabaseOperation.TEST)

    for unsafe in (
        settings(
            database="another-db",
            sslmode="disable",
            allow_insecure_runtime=True,
        ),
        settings(
            schema="another_schema",
            sslmode="disable",
            allow_insecure_runtime=True,
        ),
        settings(
            sslmode="disable",
            allow_insecure_runtime=False,
        ),
    ):
        with pytest.raises(ConfigurationError):
            unsafe.validate_for(DatabaseOperation.RUNTIME)


def test_local_env_does_not_override_powershell_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CAT_VIDEO_DB_HOST=file-host\n"
        "CAT_VIDEO_DB_SCHEMA=cat_video\n"
        "ARK_ACCESS_MODE=standard\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CAT_VIDEO_DB_HOST", "powershell-host")
    monkeypatch.setenv("ARK_ACCESS_MODE", "agent_plan")
    monkeypatch.delenv("CAT_VIDEO_DB_SCHEMA", raising=False)

    assert load_local_env(env_file) is True
    assert os.environ["CAT_VIDEO_DB_HOST"] == "powershell-host"
    assert os.environ["CAT_VIDEO_DB_SCHEMA"] == "cat_video"
    assert os.environ["ARK_ACCESS_MODE"] == "agent_plan"


@pytest.mark.parametrize("tier", ["large", "max"])
def test_agent_plan_runtime_requires_supported_visual_profile(
    tier: str,
) -> None:
    configured = RuntimeSettings.from_env(
        {
            "ARK_ACCESS_MODE": "agent_plan",
            "ARK_AGENT_PLAN_TIER": tier,
            "ARK_API_KEY": "test-only-key",
            "ARK_BASE_URL": f"{AGENT_PLAN_URL}/",
            "ARK_IMAGE_MODEL": "doubao-seedream-5.0-lite",
            "ARK_VIDEO_MODEL": "doubao-seedance-2.0-mini",
        }
    )

    configured.validate_for_ark_access()

    assert configured.ark_access_mode is ArkAccessMode.AGENT_PLAN
    assert configured.ark_base_url == AGENT_PLAN_URL
    assert configured.provider_profile == "volcengine-agent-plan"
    assert configured.endpoint_profile == "agent_plan"
    assert configured.ark_configuration_issues() == ()
    assert configured.request_profile_snapshot() == {
        "accessMode": "agent_plan",
        "providerProfile": "volcengine-agent-plan",
        "agentPlanTier": tier,
    }


@pytest.mark.parametrize("tier", ["", "small", "medium"])
def test_agent_plan_rejects_tiers_without_seedance_2_support(
    tier: str,
) -> None:
    configured = RuntimeSettings.from_env(
        {
            "ARK_ACCESS_MODE": "agent_plan",
            "ARK_AGENT_PLAN_TIER": tier,
            "ARK_API_KEY": "test-only-key",
            "ARK_BASE_URL": AGENT_PLAN_URL,
            "ARK_IMAGE_MODEL": "doubao-seedream-5.0-lite",
            "ARK_VIDEO_MODEL": "doubao-seedance-2.0-mini",
        }
    )

    with pytest.raises(ConfigurationError, match="large or max"):
        configured.validate_for_ark_access()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"ARK_BASE_URL": STANDARD_ARK_URL}, "Agent Plan requires ARK_BASE_URL"),
        (
            {"ARK_IMAGE_MODEL": "doubao-seedream-5-0-pro-260628"},
            "Agent Plan requires ARK_IMAGE_MODEL",
        ),
        (
            {"ARK_VIDEO_MODEL": "doubao-seedance-2-0-mini-260615"},
            "Agent Plan requires ARK_VIDEO_MODEL",
        ),
    ],
)
def test_agent_plan_rejects_standard_endpoint_or_versioned_models(
    overrides: dict[str, str],
    message: str,
) -> None:
    values = {
        "ARK_ACCESS_MODE": "agent_plan",
        "ARK_AGENT_PLAN_TIER": "large",
        "ARK_API_KEY": "test-only-key",
        "ARK_BASE_URL": AGENT_PLAN_URL,
        "ARK_IMAGE_MODEL": "doubao-seedream-5.0-lite",
        "ARK_VIDEO_MODEL": "doubao-seedance-2.0-mini",
    }
    values.update(overrides)

    with pytest.raises(ConfigurationError, match=message):
        RuntimeSettings.from_env(values).validate_for_ark_access()


def test_standard_ark_rejects_plan_url_and_tier() -> None:
    wrong_url = RuntimeSettings.from_env(
        {
            "ARK_ACCESS_MODE": "standard",
            "ARK_API_KEY": "test-only-key",
            "ARK_BASE_URL": AGENT_PLAN_URL,
        }
    )
    with pytest.raises(ConfigurationError, match="Standard Ark requires"):
        wrong_url.validate_for_ark_access()

    wrong_tier = RuntimeSettings.from_env(
        {
            "ARK_ACCESS_MODE": "standard",
            "ARK_AGENT_PLAN_TIER": "large",
            "ARK_API_KEY": "test-only-key",
            "ARK_BASE_URL": STANDARD_ARK_URL,
        }
    )
    with pytest.raises(ConfigurationError, match="must be empty"):
        wrong_tier.validate_for_ark_access()


def test_standard_ark_requires_both_model_identifiers() -> None:
    configured = RuntimeSettings.from_env(
        {
            "ARK_ACCESS_MODE": "standard",
            "ARK_API_KEY": "test-only-key",
            "ARK_BASE_URL": STANDARD_ARK_URL,
            "ARK_IMAGE_MODEL": "",
            "ARK_VIDEO_MODEL": "",
        }
    )

    with pytest.raises(
        ConfigurationError,
        match="ARK_IMAGE_MODEL.*ARK_VIDEO_MODEL",
    ):
        configured.validate_for_ark_access()


def test_runtime_requires_prefixed_credentials_and_explicit_mode() -> None:
    configured = RuntimeSettings.from_env(
        {
            "API_KEY": "ignored-generic-key",
            "BASE_URL": AGENT_PLAN_URL,
        }
    )

    assert configured.ark_api_key is None
    assert configured.ark_access_mode is None
    with pytest.raises(ConfigurationError, match="ARK_ACCESS_MODE"):
        configured.validate_for_ark_access()


def test_preflight_reports_profile_without_secret_material() -> None:
    configured = RuntimeSettings.from_env(
        {
            "ARK_ACCESS_MODE": "agent_plan",
            "ARK_AGENT_PLAN_TIER": "large",
            "ARK_API_KEY": "never-print-agent-plan-key",
            "ARK_BASE_URL": AGENT_PLAN_URL,
            "ARK_IMAGE_MODEL": "doubao-seedream-5.0-lite",
            "ARK_VIDEO_MODEL": "doubao-seedance-2.0-mini",
        }
    )

    report = configured.preflight_report()
    rendered = str(report)

    assert report["arkAccessMode"] == "agent_plan"
    assert report["agentPlanTier"] == "large"
    assert report["endpointProfile"] == "agent_plan"
    assert report["generationConfigurationValid"] is True
    assert report["generationConfigurationIssues"] == []
    assert "never-print-agent-plan-key" not in rendered


def test_access_mode_is_part_of_secret_free_idempotency_input() -> None:
    agent_plan = RuntimeSettings.from_env(
        {
            "ARK_ACCESS_MODE": "agent_plan",
            "ARK_AGENT_PLAN_TIER": "large",
            "ARK_BASE_URL": AGENT_PLAN_URL,
            "ARK_IMAGE_MODEL": "doubao-seedream-5.0-lite",
            "ARK_VIDEO_MODEL": "doubao-seedance-2.0-mini",
        }
    )
    standard = RuntimeSettings.from_env(
        {
            "ARK_ACCESS_MODE": "standard",
            "ARK_BASE_URL": STANDARD_ARK_URL,
        }
    )
    agent_snapshot = {
        **agent_plan.request_profile_snapshot(),
        "model": "same-model-for-hash-proof",
    }
    standard_snapshot = {
        **standard.request_profile_snapshot(),
        "model": "same-model-for-hash-proof",
    }

    assert canonical_content_hash(agent_snapshot) != canonical_content_hash(
        standard_snapshot
    )
    for snapshot in (agent_snapshot, standard_snapshot):
        assert "apiKey" not in snapshot
        assert "baseUrl" not in snapshot


def test_from_env_rejects_missing_secrets_and_invalid_port() -> None:
    with pytest.raises(ConfigurationError, match="CAT_VIDEO_DB_PASSWORD"):
        DatabaseSettings.from_env(
            {
                "CAT_VIDEO_DB_HOST": "db.example.test",
                "CAT_VIDEO_DB_NAME": "vedio-appdb",
                "CAT_VIDEO_DB_USER": "postgres",
            }
        )

    configured = DatabaseSettings.from_env(
        {
            "CAT_VIDEO_DB_HOST": "db.example.test",
            "CAT_VIDEO_DB_NAME": "vedio-appdb",
            "CAT_VIDEO_DB_USER": "postgres",
            "CAT_VIDEO_DB_PASSWORD": "secret",
            "CAT_VIDEO_DB_SSLMODE": "disable",
            "CAT_VIDEO_DB_SCHEMA": "cat_video",
            "CAT_VIDEO_ALLOW_INSECURE_RUNTIME": "true",
        }
    )
    assert configured.schema == "cat_video"
    assert configured.allow_insecure_runtime is True

    with pytest.raises(ConfigurationError, match="must be an integer"):
        DatabaseSettings.from_env(
            {
                "CAT_VIDEO_DB_HOST": "db.example.test",
                "CAT_VIDEO_DB_PORT": "not-a-port",
                "CAT_VIDEO_DB_NAME": "vedio-appdb",
                "CAT_VIDEO_DB_USER": "postgres",
                "CAT_VIDEO_DB_PASSWORD": "secret",
            }
        )


def test_cli_never_prints_database_password_on_connection_failure() -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["doctor"],
        env={
            "CAT_VIDEO_DB_HOST": "127.0.0.1",
            "CAT_VIDEO_DB_PORT": "1",
            "CAT_VIDEO_DB_NAME": "vedio-appdb",
            "CAT_VIDEO_DB_USER": "postgres",
            "CAT_VIDEO_DB_PASSWORD": "never-print-this-password",
            "CAT_VIDEO_DB_SSLMODE": "require",
        },
    )

    assert result.exit_code == 2
    assert "never-print-this-password" not in result.stdout
    assert "Database operation failed" in result.stdout


def test_db_upgrade_refuses_remote_unencrypted_transport_before_connecting() -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["db", "upgrade"],
        env={
            "CAT_VIDEO_DB_HOST": "203.0.113.10",
            "CAT_VIDEO_DB_PORT": "5432",
            "CAT_VIDEO_DB_NAME": "vedio-appdb",
            "CAT_VIDEO_DB_USER": "postgres",
            "CAT_VIDEO_DB_PASSWORD": "unused",
            "CAT_VIDEO_DB_SSLMODE": "disable",
            "CAT_VIDEO_ALLOW_INSECURE_READONLY_SMOKE": "true",
        },
    )

    assert result.exit_code == 2
    assert "Unencrypted PostgreSQL" in result.stdout


def test_run_next_refuses_remote_unencrypted_transport_before_connecting() -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["run-next", "--target-date", "2099-12-31"],
        env={
            "CAT_VIDEO_DB_HOST": "203.0.113.10",
            "CAT_VIDEO_DB_PORT": "5432",
            "CAT_VIDEO_DB_NAME": "vedio-appdb",
            "CAT_VIDEO_DB_USER": "postgres",
            "CAT_VIDEO_DB_PASSWORD": "unused",
            "CAT_VIDEO_DB_SSLMODE": "disable",
            "CAT_VIDEO_ALLOW_INSECURE_READONLY_SMOKE": "true",
        },
    )

    assert result.exit_code == 2
    assert "Unencrypted PostgreSQL" in result.stdout


def test_remote_write_validation_requires_explicit_cli_acknowledgement() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["db", "validate-remote"])

    assert result.exit_code == 2
    assert "--allow-insecure-write-test" in result.stderr


def test_run_next_rejects_non_iso_target_date_before_database_access() -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["run-next", "--target-date", "tomorrow"],
    )

    assert result.exit_code == 2
    assert "YYYY-MM-DD" in result.stderr
