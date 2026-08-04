"""环境配置与安全门。

配置只负责解析、路径发现和连接准入，不加载业务Schema，也不判断Episode
内容。PowerShell环境变量始终优先于本地`.env`。
"""

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
    """运行环境不完整或违反安全边界。"""


class DatabaseOperation(StrEnum):
    READ_ONLY_SMOKE = "read_only_smoke"
    MIGRATION = "migration"
    RUNTIME = "runtime"
    TEST = "test"


class StoryboardReviewMode(StrEnum):
    """故事板整组语义审核是否阻断后续收费视频任务。"""

    SEMANTIC_AUTO = "semantic_auto"
    MANUAL = "manual"


_STANDARD_URL = "https://ark.cn-beijing.volces.com/api/v3"
_IMAGE_MODEL = "doubao-seedream-5-0-260128"
_VIDEO_MODELS = frozenset(
    {
        "doubao-seedance-2-0-mini-260615",
        "doubao-seedance-2-0-260128",
    }
)
_SSL_MODES = {"disable", "require", "verify-ca", "verify-full"}
_SCHEMA_PATTERN = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_SOURCE_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _config_root() -> Path:
    """优先使用调用目录中的.env，否则回到源码项目根。"""

    working = Path.cwd().resolve()
    return working if (working / ".env").is_file() else _SOURCE_PROJECT_ROOT


def load_local_env(path: Path | None = None) -> bool:
    """加载本地秘密文件，但不覆盖调用者当前会话。"""

    env_path = _config_root() / ".env" if path is None else path
    return bool(
        env_path.is_file() and load_dotenv(dotenv_path=env_path, override=False)
    )


def _bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"无效布尔值: {value!r}")


def _number(
    values: Mapping[str, str],
    name: str,
    default: str,
    value_type: type[int] | type[float],
) -> int | float:
    try:
        return value_type(values.get(name, default))
    except ValueError as exc:
        raise ConfigurationError(f"{name}必须是数值") from exc


def _executable(
    configured: str | None,
    command: str,
    search_path: str,
) -> Path | None:
    if configured and configured.strip():
        path = Path(configured).expanduser()
        return path if path.is_file() else None
    discovered = shutil.which(command, path=search_path)
    return None if discovered is None else Path(discovered)


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    """Ark、媒体与导演运行配置。"""

    ark_api_key: str | None
    ark_base_url: str
    ark_image_model: str
    ark_video_model: str
    ark_planning_model: str
    ark_review_model: str
    ark_structured_output_mode: str
    ark_video_resolution: str
    ark_director_request_timeout_seconds: float
    ark_review_request_timeout_seconds: float
    ark_video_api_timeout_seconds: float
    ark_poll_interval_seconds: float
    ark_task_timeout_seconds: float
    ark_image_request_timeout_seconds: float
    ark_image_timeout_auto_retries: int
    ark_image_retry_delay_seconds: float
    candidate_count: int
    storyboard_review_mode: StoryboardReviewMode
    video_semantic_review_mode: str
    configuration_warnings: tuple[str, ...]
    ffmpeg_path: Path | None
    ffprobe_path: Path | None
    work_root: Path
    asset_root: Path
    delivery_root: Path
    event_seed_root: Path

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        config_root: Path | None = None,
    ) -> RuntimeSettings:
        values = os.environ if environ is None else environ
        director_request_timeout = float(
            _number(values, "ARK_DIRECTOR_REQUEST_TIMEOUT_SECONDS", "240", float)
        )
        review_request_timeout = float(
            _number(values, "ARK_REVIEW_REQUEST_TIMEOUT_SECONDS", "240", float)
        )
        video_api_timeout = float(
            _number(values, "ARK_VIDEO_API_TIMEOUT_SECONDS", "120", float)
        )
        poll_interval = float(_number(values, "ARK_POLL_INTERVAL_SECONDS", "10", float))
        timeout = float(_number(values, "ARK_TASK_TIMEOUT_SECONDS", "1800", float))
        image_request_timeout = float(
            _number(values, "ARK_IMAGE_REQUEST_TIMEOUT_SECONDS", "600", float)
        )
        image_timeout_auto_retries = int(
            _number(values, "ARK_IMAGE_TIMEOUT_AUTO_RETRIES", "1", int)
        )
        image_retry_delay = float(
            _number(values, "ARK_IMAGE_RETRY_DELAY_SECONDS", "15", float)
        )
        candidate_count = int(_number(values, "DAILY_PLAN_CANDIDATE_COUNT", "1", int))
        if any(
            value <= 0
            for value in (
                director_request_timeout,
                review_request_timeout,
                video_api_timeout,
                poll_interval,
                timeout,
                image_request_timeout,
                image_retry_delay,
            )
        ):
            raise ConfigurationError("Ark轮询间隔和请求超时必须大于0")
        if image_timeout_auto_retries not in {0, 1}:
            raise ConfigurationError("ARK_IMAGE_TIMEOUT_AUTO_RETRIES只允许0或1")
        if candidate_count != 1:
            raise ConfigurationError("分层导演模式下DAILY_PLAN_CANDIDATE_COUNT必须为1")
        review_mode_value = (
            values.get(
                "STORYBOARD_REVIEW_MODE",
                "semantic_auto",
            )
            .strip()
            .lower()
        )
        configuration_warnings: list[str] = []
        try:
            storyboard_review_mode = StoryboardReviewMode(review_mode_value)
        except ValueError as exc:
            raise ConfigurationError(
                "STORYBOARD_REVIEW_MODE必须是semantic_auto或manual"
            ) from exc
        video_review_mode = values.get(
            "VIDEO_SEMANTIC_REVIEW_MODE",
            "diagnostic",
        ).strip().lower()
        if video_review_mode not in {"off", "diagnostic"}:
            raise ConfigurationError(
                "VIDEO_SEMANTIC_REVIEW_MODE必须是off或diagnostic"
            )
        structured_mode = values.get(
            "ARK_RESPONSES_STRUCTURED_OUTPUT_MODE",
            "json_object_schema_prompt",
        ).strip()
        if structured_mode not in {"json_schema", "json_object_schema_prompt"}:
            raise ConfigurationError("无效Ark结构化输出模式")
        resolved_config_root = (
            _config_root()
            if config_root is None
            else config_root.expanduser().resolve()
        )
        configured_seed_root = Path(
            values.get("CAT_VIDEO_EVENT_SEED_ROOT", "content/events")
        ).expanduser()
        event_seed_root = (
            configured_seed_root.resolve()
            if configured_seed_root.is_absolute()
            else (resolved_config_root / configured_seed_root).resolve()
        )
        if not event_seed_root.is_dir():
            configuration_warnings.append(
                f"事件种子目录不存在：{event_seed_root}；导演将使用原创模式"
            )
        return cls(
            ark_api_key=values.get("ARK_API_KEY") or None,
            ark_base_url=values.get("ARK_BASE_URL", _STANDARD_URL).rstrip("/"),
            ark_image_model=values.get(
                "ARK_IMAGE_MODEL",
                "doubao-seedream-5-0-260128",
            ).strip(),
            ark_video_model=values.get(
                "ARK_VIDEO_MODEL",
                "doubao-seedance-2-0-mini-260615",
            ).strip(),
            ark_planning_model=values.get(
                "ARK_PLANNING_MODEL",
                "doubao-seed-2-1-pro-260628",
            ).strip(),
            ark_review_model=values.get(
                "ARK_REVIEW_MODEL",
                "doubao-seed-2-1-pro-260628",
            ).strip(),
            ark_structured_output_mode=structured_mode,
            ark_video_resolution=values.get(
                "ARK_VIDEO_RESOLUTION",
                "720p",
            )
            .strip()
            .lower(),
            ark_director_request_timeout_seconds=director_request_timeout,
            ark_review_request_timeout_seconds=review_request_timeout,
            ark_video_api_timeout_seconds=video_api_timeout,
            ark_poll_interval_seconds=poll_interval,
            ark_task_timeout_seconds=timeout,
            ark_image_request_timeout_seconds=image_request_timeout,
            ark_image_timeout_auto_retries=image_timeout_auto_retries,
            ark_image_retry_delay_seconds=image_retry_delay,
            candidate_count=candidate_count,
            storyboard_review_mode=storyboard_review_mode,
            video_semantic_review_mode=video_review_mode,
            configuration_warnings=tuple(configuration_warnings),
            ffmpeg_path=_executable(
                values.get("FFMPEG_PATH"),
                "ffmpeg",
                values.get("PATH", ""),
            ),
            ffprobe_path=_executable(
                values.get("FFPROBE_PATH"),
                "ffprobe",
                values.get("PATH", ""),
            ),
            work_root=Path(values.get("MEDIA_WORK_ROOT", "var/work")),
            asset_root=Path(values.get("MEDIA_ASSET_ROOT", "var/assets")),
            delivery_root=Path(values.get("DELIVERY_OUTPUT_ROOT", "output")),
            event_seed_root=event_seed_root,
        )

    @property
    def provider_profile(self) -> str:
        return "volcengine-ark-standard"

    def validate_for_ark_access(self) -> None:
        """拒绝混用访问模式、Base URL或空模型。"""

        issues: list[str] = []
        if self.ark_base_url != _STANDARD_URL:
            issues.append(f"Ark标准API必须使用{_STANDARD_URL}")
        if not self.ark_api_key:
            issues.append("缺少ARK_API_KEY")
        if not all(
            (
                self.ark_image_model,
                self.ark_video_model,
                self.ark_planning_model,
                self.ark_review_model,
            )
        ):
            issues.append("Ark图片、视频和规划模型都必须配置")
        if self.ark_image_model != _IMAGE_MODEL:
            issues.append(f"当前生产只允许图片模型{_IMAGE_MODEL}")
        if self.ark_video_model not in _VIDEO_MODELS:
            issues.append(
                "ARK_VIDEO_MODEL必须使用已登记能力档案："
                + "、".join(sorted(_VIDEO_MODELS))
            )
        if self.ark_video_resolution not in {"480p", "720p"}:
            issues.append("ARK_VIDEO_RESOLUTION必须是480p或720p")
        if issues:
            raise ConfigurationError("; ".join(issues))

    def validate_for_generation(self, *, allow_paid_generation: bool) -> None:
        if not allow_paid_generation:
            raise ConfigurationError("Ark调用需要--allow-paid-generation")
        self.validate_for_ark_access()
        if self.ffprobe_path is None:
            raise ConfigurationError("视频生成要求ffprobe可用")
        if self.video_semantic_review_mode == "diagnostic" and self.ffmpeg_path is None:
            raise ConfigurationError("视频语义诊断要求ffmpeg可用以均匀抽帧")

    def preflight_report(self) -> dict[str, object]:
        try:
            self.validate_for_ark_access()
            issues: list[str] = []
        except ConfigurationError as exc:
            issues = [str(exc)]
        return {
            "provider": self.provider_profile,
            "arkApiKeyConfigured": bool(self.ark_api_key),
            "arkBaseUrlProfile": (
                "standard"
                if self.ark_base_url == _STANDARD_URL
                else "unknown"
            ),
            "arkImageModel": self.ark_image_model,
            "arkVideoModel": self.ark_video_model,
            "arkPlanningModel": self.ark_planning_model,
            "arkReviewModel": self.ark_review_model,
            "arkVideoResolution": self.ark_video_resolution,
            "arkDirectorRequestTimeoutSeconds": self.ark_director_request_timeout_seconds,
            "arkReviewRequestTimeoutSeconds": self.ark_review_request_timeout_seconds,
            "arkVideoApiTimeoutSeconds": self.ark_video_api_timeout_seconds,
            "arkPollIntervalSeconds": self.ark_poll_interval_seconds,
            "arkTaskTimeoutSeconds": self.ark_task_timeout_seconds,
            "arkImageRequestTimeoutSeconds": self.ark_image_request_timeout_seconds,
            "arkImageTimeoutAutoRetries": self.ark_image_timeout_auto_retries,
            "arkImageRetryDelaySeconds": self.ark_image_retry_delay_seconds,
            "storyboardReviewMode": self.storyboard_review_mode.value,
            "videoSemanticReviewMode": self.video_semantic_review_mode,
            "configurationWarnings": list(self.configuration_warnings),
            "eventSeedRoot": str(self.event_seed_root),
            "eventSeedRootAvailable": self.event_seed_root.is_dir(),
            "generationConfigurationValid": not issues,
            "generationConfigurationIssues": issues,
            "ffmpeg": None if self.ffmpeg_path is None else str(self.ffmpeg_path),
            "ffprobe": None if self.ffprobe_path is None else str(self.ffprobe_path),
            "workRoot": str(self.work_root),
            "assetRoot": str(self.asset_root),
            "deliveryRoot": str(self.delivery_root),
        }


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """远程PostgreSQL连接和临时明文运行许可。"""

    host: str
    port: int
    database: str
    user: str
    password: str
    sslmode: str
    schema: str
    allow_insecure_runtime: bool
    allow_insecure_readonly_smoke: bool
    minimum_server_version: int = 140000

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> DatabaseSettings:
        values = os.environ if environ is None else environ
        names = (
            "CAT_VIDEO_DB_HOST",
            "CAT_VIDEO_DB_NAME",
            "CAT_VIDEO_DB_USER",
            "CAT_VIDEO_DB_PASSWORD",
        )
        missing = [name for name in names if not values.get(name)]
        if missing:
            raise ConfigurationError("缺少数据库配置: " + ", ".join(missing))
        port = int(_number(values, "CAT_VIDEO_DB_PORT", "5432", int))
        if not 1 <= port <= 65535:
            raise ConfigurationError("CAT_VIDEO_DB_PORT超出有效范围")
        sslmode = values.get("CAT_VIDEO_DB_SSLMODE", "require").strip().lower()
        if sslmode not in _SSL_MODES:
            raise ConfigurationError("不支持的CAT_VIDEO_DB_SSLMODE")
        return cls(
            host=values["CAT_VIDEO_DB_HOST"],
            port=port,
            database=values["CAT_VIDEO_DB_NAME"],
            user=values["CAT_VIDEO_DB_USER"],
            password=values["CAT_VIDEO_DB_PASSWORD"],
            sslmode=sslmode,
            schema=values.get("CAT_VIDEO_DB_SCHEMA", "cat_video").strip(),
            allow_insecure_runtime=_bool(
                values.get("CAT_VIDEO_ALLOW_INSECURE_RUNTIME")
            ),
            allow_insecure_readonly_smoke=_bool(
                values.get("CAT_VIDEO_ALLOW_INSECURE_READONLY_SMOKE")
            ),
        )

    @property
    def url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
        )

    def validate_for(self, operation: DatabaseOperation) -> None:
        if _SCHEMA_PATTERN.fullmatch(self.schema) is None:
            raise ConfigurationError("数据库Schema名称不合法")
        if self.sslmode != "disable":
            return
        if (
            operation is DatabaseOperation.TEST
            and self.allow_insecure_runtime
            and self.database == "vedio-appdb"
            and re.fullmatch(r"cat_video_test_[a-f0-9]{12}", self.schema)
        ):
            # 远程集成测试只能进入本次随机Schema；正式cat_video和其他Schema
            # 均不会被测试迁移、约束验证或清理逻辑触碰。
            return
        if operation is DatabaseOperation.READ_ONLY_SMOKE:
            if self.allow_insecure_readonly_smoke:
                return
            # 正式明文运行许可比只读诊断许可更强；doctor可以复用该授权，
            # 但仍限定在用户明确批准的vedio-appdb.cat_video。
            if (
                self.allow_insecure_runtime
                and self.database == "vedio-appdb"
                and self.schema == "cat_video"
            ):
                return
        if (
            operation
            in {
                DatabaseOperation.MIGRATION,
                DatabaseOperation.RUNTIME,
                DatabaseOperation.TEST,
            }
            and self.allow_insecure_runtime
            and self.database == "vedio-appdb"
            and self.schema == "cat_video"
        ):
            return
        raise ConfigurationError(
            "明文PostgreSQL只允许显式授权的vedio-appdb.cat_video运行"
        )
