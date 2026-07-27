from __future__ import annotations

import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL


class ConfigurationError(ValueError):
    """Raised when runtime configuration is incomplete or unsafe."""


class DatabaseOperation(StrEnum):
    READ_ONLY_SMOKE = "read_only_smoke"
    MIGRATION = "migration"
    RUNTIME = "runtime"
    TEST = "test"
    REMOTE_VALIDATION = "remote_validation"


class ArkAccessMode(StrEnum):
    AGENT_PLAN = "agent_plan"
    STANDARD = "standard"


_SECURE_SSL_MODES = frozenset({"require", "verify-ca", "verify-full"})
_SUPPORTED_SSL_MODES = _SECURE_SSL_MODES | {"disable"}
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_ARK_AGENT_PLAN_BASE_URL = "https://ark.cn-beijing.volces.com/api/plan/v3"
_ARK_STANDARD_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
_ARK_AGENT_PLAN_IMAGE_MODEL = "doubao-seedream-5.0-lite"
_ARK_AGENT_PLAN_VIDEO_MODEL = "doubao-seedance-2.0-mini"
_ARK_AGENT_PLAN_VIDEO_TIERS = frozenset({"large", "max"})
_SCHEMA_NAME_PATTERN = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_REMOTE_VALIDATION_SCHEMA_PATTERN = re.compile(
    r"cat_video_validation_[0-9a-f]{12}"
)


def _read_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"Invalid boolean value: {value!r}")


def load_local_env(path: Path | None = None) -> bool:
    """Load the local CLI environment without overriding the caller's session."""
    env_path = Path.cwd() / ".env" if path is None else path
    if not env_path.is_file():
        return False
    return load_dotenv(dotenv_path=env_path, override=False)


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    ark_api_key: str | None
    ark_access_mode: ArkAccessMode | None
    ark_agent_plan_tier: str | None
    ark_base_url: str
    ark_image_model: str
    ark_video_model: str
    ark_poll_interval_seconds: float
    ark_task_timeout_seconds: float
    ffmpeg_path: Path | None
    ffprobe_path: Path | None
    work_root: Path
    asset_root: Path
    delivery_root: Path

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> RuntimeSettings:
        values = os.environ if environ is None else environ
        try:
            poll_interval = float(values.get("ARK_POLL_INTERVAL_SECONDS", "10"))
            task_timeout = float(values.get("ARK_TASK_TIMEOUT_SECONDS", "1800"))
        except ValueError as exc:
            raise ConfigurationError(
                "ARK_POLL_INTERVAL_SECONDS and ARK_TASK_TIMEOUT_SECONDS "
                "must be numeric."
            ) from exc
        if poll_interval <= 0 or task_timeout <= 0:
            raise ConfigurationError(
                "Ark poll interval and task timeout must be greater than zero."
            )
        access_mode_value = values.get("ARK_ACCESS_MODE", "").strip().lower()
        try:
            access_mode = (
                None
                if not access_mode_value
                else ArkAccessMode(access_mode_value)
            )
        except ValueError as exc:
            raise ConfigurationError(
                "ARK_ACCESS_MODE must be agent_plan or standard."
            ) from exc
        tier_value = values.get("ARK_AGENT_PLAN_TIER", "").strip().lower()
        return cls(
            ark_api_key=values.get("ARK_API_KEY") or None,
            ark_access_mode=access_mode,
            ark_agent_plan_tier=tier_value or None,
            ark_base_url=values.get(
                "ARK_BASE_URL", _ARK_STANDARD_BASE_URL
            ).rstrip("/"),
            ark_image_model=values.get(
                "ARK_IMAGE_MODEL", "doubao-seedream-5-0-pro-260628"
            ),
            ark_video_model=values.get(
                "ARK_VIDEO_MODEL", "doubao-seedance-2-0-260128"
            ),
            ark_poll_interval_seconds=poll_interval,
            ark_task_timeout_seconds=task_timeout,
            ffmpeg_path=_resolve_executable(
                values.get("FFMPEG_PATH"),
                "ffmpeg",
                search_path=values.get("PATH", ""),
            ),
            ffprobe_path=_resolve_executable(
                values.get("FFPROBE_PATH"),
                "ffprobe",
                search_path=values.get("PATH", ""),
            ),
            work_root=Path(values.get("MEDIA_WORK_ROOT", "var/work")),
            asset_root=Path(values.get("MEDIA_ASSET_ROOT", "var/assets")),
            delivery_root=Path(values.get("DELIVERY_OUTPUT_ROOT", "output")),
        )

    def validate_for_generation(self, *, allow_paid_generation: bool) -> None:
        if not allow_paid_generation:
            raise ConfigurationError(
                "Ark generation requires --allow-paid-generation."
            )
        self.validate_for_ark_access()
        if self.ffprobe_path is None:
            raise ConfigurationError(
                "ffprobe is required for media QC. Set FFPROBE_PATH or add "
                "ffprobe to PATH. ffmpeg is optional until a conditional media "
                "repair is requested."
            )

    def validate_for_ark_access(self) -> None:
        """Require one internally consistent endpoint, model, tier, and key."""
        issues = self.ark_configuration_issues()
        if issues:
            raise ConfigurationError("; ".join(issues))
        if not self.ark_api_key:
            raise ConfigurationError("ARK_API_KEY is required for Ark access.")

    def ark_configuration_issues(self) -> tuple[str, ...]:
        if self.ark_access_mode is None:
            return ("ARK_ACCESS_MODE must be explicitly configured.",)
        if self.ark_access_mode is ArkAccessMode.AGENT_PLAN:
            issues: list[str] = []
            if self.ark_base_url != _ARK_AGENT_PLAN_BASE_URL:
                issues.append(
                    "Agent Plan requires ARK_BASE_URL="
                    f"{_ARK_AGENT_PLAN_BASE_URL}."
                )
            if self.ark_image_model != _ARK_AGENT_PLAN_IMAGE_MODEL:
                issues.append(
                    "Agent Plan requires ARK_IMAGE_MODEL="
                    f"{_ARK_AGENT_PLAN_IMAGE_MODEL}."
                )
            if self.ark_video_model != _ARK_AGENT_PLAN_VIDEO_MODEL:
                issues.append(
                    "Agent Plan requires ARK_VIDEO_MODEL="
                    f"{_ARK_AGENT_PLAN_VIDEO_MODEL}."
                )
            if self.ark_agent_plan_tier not in _ARK_AGENT_PLAN_VIDEO_TIERS:
                issues.append(
                    "Seedance 2.0-mini on Agent Plan requires "
                    "ARK_AGENT_PLAN_TIER=large or max."
                )
            return tuple(issues)

        issues = []
        if self.ark_base_url != _ARK_STANDARD_BASE_URL:
            issues.append(
                "Standard Ark requires ARK_BASE_URL="
                f"{_ARK_STANDARD_BASE_URL}."
            )
        if self.ark_agent_plan_tier is not None:
            issues.append(
                "ARK_AGENT_PLAN_TIER must be empty in standard mode."
            )
        if not self.ark_image_model.strip():
            issues.append(
                "ARK_IMAGE_MODEL must be configured in standard mode."
            )
        if not self.ark_video_model.strip():
            issues.append(
                "ARK_VIDEO_MODEL must be configured in standard mode."
            )
        return tuple(issues)

    @property
    def provider_profile(self) -> str:
        if self.ark_access_mode is ArkAccessMode.AGENT_PLAN:
            return "volcengine-agent-plan"
        if self.ark_access_mode is ArkAccessMode.STANDARD:
            return "volcengine-ark-standard"
        return "volcengine-ark-unconfigured"

    @property
    def endpoint_profile(self) -> str:
        if self.ark_base_url == _ARK_AGENT_PLAN_BASE_URL:
            return "agent_plan"
        if self.ark_base_url == _ARK_STANDARD_BASE_URL:
            return "standard"
        return "unknown"

    def request_profile_snapshot(self) -> dict[str, str]:
        if self.ark_access_mode is None:
            raise ConfigurationError(
                "ARK_ACCESS_MODE must be explicitly configured."
            )
        snapshot = {
            "accessMode": self.ark_access_mode.value,
            "providerProfile": self.provider_profile,
        }
        if self.ark_access_mode is ArkAccessMode.AGENT_PLAN:
            if self.ark_agent_plan_tier is None:
                raise ConfigurationError(
                    "ARK_AGENT_PLAN_TIER is required in Agent Plan mode."
                )
            snapshot["agentPlanTier"] = self.ark_agent_plan_tier
        return snapshot

    def preflight_report(self) -> dict[str, object]:
        configuration_issues = self.ark_configuration_issues()
        return {
            "provider": self.provider_profile,
            "arkApiKeyConfigured": bool(self.ark_api_key),
            "arkAccessMode": (
                None
                if self.ark_access_mode is None
                else self.ark_access_mode.value
            ),
            "agentPlanTier": self.ark_agent_plan_tier,
            "endpointProfile": self.endpoint_profile,
            "generationConfigurationValid": not configuration_issues,
            "generationConfigurationIssues": list(configuration_issues),
            "arkBaseUrl": self.ark_base_url,
            "arkImageModel": self.ark_image_model,
            "arkVideoModel": self.ark_video_model,
            "arkPollIntervalSeconds": self.ark_poll_interval_seconds,
            "arkTaskTimeoutSeconds": self.ark_task_timeout_seconds,
            "ffmpeg": None if self.ffmpeg_path is None else str(self.ffmpeg_path),
            "ffprobe": None if self.ffprobe_path is None else str(self.ffprobe_path),
            "workRoot": str(self.work_root),
            "assetRoot": str(self.asset_root),
            "deliveryRoot": str(self.delivery_root),
        }


def _optional_path(value: str | None) -> Path | None:
    if value is None or not value.strip():
        return None
    return Path(value).expanduser()


def _resolve_executable(
    value: str | None,
    command: str,
    *,
    search_path: str,
) -> Path | None:
    explicit = _optional_path(value)
    if explicit is not None:
        return explicit if explicit.is_file() else None
    discovered = shutil.which(command, path=search_path)
    return None if discovered is None else Path(discovered)


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    host: str
    port: int
    database: str
    user: str
    password: str
    sslmode: str = "require"
    allow_insecure_readonly_smoke: bool = False
    allow_insecure_local_tests: bool = False
    allow_insecure_remote_write_test: bool = False
    allow_insecure_runtime: bool = False
    schema: str = "cat_video"
    minimum_server_version: int = 140000

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> DatabaseSettings:
        values = os.environ if environ is None else environ
        required = {
            "host": "CAT_VIDEO_DB_HOST",
            "database": "CAT_VIDEO_DB_NAME",
            "user": "CAT_VIDEO_DB_USER",
            "password": "CAT_VIDEO_DB_PASSWORD",
        }
        missing = [env_name for env_name in required.values() if not values.get(env_name)]
        if missing:
            raise ConfigurationError(
                "Missing required database environment variables: "
                + ", ".join(sorted(missing))
            )

        try:
            port = int(values.get("CAT_VIDEO_DB_PORT", "5432"))
        except ValueError as exc:
            raise ConfigurationError("CAT_VIDEO_DB_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ConfigurationError("CAT_VIDEO_DB_PORT must be between 1 and 65535")

        sslmode = values.get("CAT_VIDEO_DB_SSLMODE", "require").strip().lower()
        if sslmode not in _SUPPORTED_SSL_MODES:
            supported = ", ".join(sorted(_SUPPORTED_SSL_MODES))
            raise ConfigurationError(
                f"CAT_VIDEO_DB_SSLMODE must be one of: {supported}"
            )

        return cls(
            host=values[required["host"]],
            port=port,
            database=values[required["database"]],
            user=values[required["user"]],
            password=values[required["password"]],
            sslmode=sslmode,
            schema=values.get("CAT_VIDEO_DB_SCHEMA", "cat_video").strip(),
            allow_insecure_readonly_smoke=_read_bool(
                values.get("CAT_VIDEO_ALLOW_INSECURE_READONLY_SMOKE")
            ),
            allow_insecure_local_tests=_read_bool(
                values.get("CAT_VIDEO_ALLOW_INSECURE_LOCAL_TESTS")
            ),
            allow_insecure_runtime=_read_bool(
                values.get("CAT_VIDEO_ALLOW_INSECURE_RUNTIME")
            ),
        )

    @property
    def url(self) -> URL:
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
        )

    @property
    def redacted_url(self) -> str:
        return self.url.render_as_string(hide_password=True)

    @property
    def secure_transport(self) -> bool:
        return self.sslmode in _SECURE_SSL_MODES

    @property
    def insecure_local_test_allowed(self) -> bool:
        return (
            self.sslmode == "disable"
            and self.allow_insecure_local_tests
            and self.host.lower() in _LOOPBACK_HOSTS
            and self.database.lower().startswith("test")
        )

    @property
    def insecure_remote_write_test_allowed(self) -> bool:
        return (
            self.sslmode == "disable"
            and self.allow_insecure_remote_write_test
            and _REMOTE_VALIDATION_SCHEMA_PATTERN.fullmatch(self.schema) is not None
        )

    @property
    def insecure_runtime_allowed(self) -> bool:
        return (
            self.sslmode == "disable"
            and self.allow_insecure_runtime
            and self.database == "vedio-appdb"
            and self.schema == "cat_video"
        )

    def validate_for(self, operation: DatabaseOperation) -> None:
        if _SCHEMA_NAME_PATTERN.fullmatch(self.schema) is None:
            raise ConfigurationError(
                "Database schema must contain only lowercase letters, digits, "
                "and underscores, start with a letter or underscore, and be at "
                "most 63 characters."
            )
        if self.secure_transport:
            return
        if (
            operation is DatabaseOperation.READ_ONLY_SMOKE
            and self.allow_insecure_readonly_smoke
        ):
            return
        if (
            operation is DatabaseOperation.REMOTE_VALIDATION
            and self.insecure_remote_write_test_allowed
        ):
            return
        if (
            operation
            in {
                DatabaseOperation.MIGRATION,
                DatabaseOperation.RUNTIME,
                DatabaseOperation.TEST,
            }
            and self.insecure_local_test_allowed
        ):
            return
        if (
            operation
            in {
                DatabaseOperation.MIGRATION,
                DatabaseOperation.RUNTIME,
            }
            and self.insecure_runtime_allowed
        ):
            return
        raise ConfigurationError(
            "Unencrypted PostgreSQL is restricted to an explicitly enabled "
            "read-only smoke test, isolated remote validation, local test, or "
            "the explicitly authorized vedio-appdb.cat_video runtime. Enable "
            "SSL/tunneling or set CAT_VIDEO_ALLOW_INSECURE_RUNTIME=true for "
            "that exact temporary runtime."
        )
