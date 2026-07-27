from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cat_video_generator.cli import app
from cat_video_generator.config import (
    ConfigurationError,
    DatabaseOperation,
    DatabaseSettings,
    load_local_env,
)


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
        "CAT_VIDEO_DB_SCHEMA=cat_video\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CAT_VIDEO_DB_HOST", "powershell-host")
    monkeypatch.delenv("CAT_VIDEO_DB_SCHEMA", raising=False)

    assert load_local_env(env_file) is True
    assert os.environ["CAT_VIDEO_DB_HOST"] == "powershell-host"
    assert os.environ["CAT_VIDEO_DB_SCHEMA"] == "cat_video"


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
