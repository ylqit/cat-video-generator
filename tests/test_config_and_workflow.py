"""标准Ark配置与显式状态机。"""

from __future__ import annotations

import pytest

from cat_video_generator.config import ConfigurationError, RuntimeSettings
from cat_video_generator.domain.workflow import (
    EpisodeStatus,
    PromptPurpose,
    RunStatus,
    StepKind,
    StepStatus,
    transition_episode,
    transition_run,
    transition_step,
)


def settings_env(**updates: str) -> dict[str, str]:
    values = {
        "ARK_API_KEY": "test-key",
        "ARK_BASE_URL": "https://ark.cn-beijing.volces.com/api/v3",
        "ARK_IMAGE_MODEL": "doubao-seedream-5-0-260128",
        "ARK_VIDEO_MODEL": "doubao-seedance-2-0-mini-260615",
        "ARK_PLANNING_MODEL": "doubao-seed-2-1-pro-260628",
        "ARK_REVIEW_MODEL": "doubao-seed-2-1-pro-260628",
        "STORYBOARD_REVIEW_MODE": "semantic_auto",
        "ARK_VIDEO_RESOLUTION": "720p",
        "PATH": "",
    }
    values.update(updates)
    return values


def test_standard_ark_configuration_is_valid(tmp_path) -> None:
    settings = RuntimeSettings.from_env(settings_env(), config_root=tmp_path)
    settings.validate_for_ark_access()
    assert settings.provider_profile == "volcengine-ark-standard"
    assert settings.ark_video_resolution == "720p"
    assert settings.ark_image_request_timeout_seconds == 600
    assert settings.ark_director_request_timeout_seconds == 240
    assert settings.ark_review_request_timeout_seconds == 240
    assert settings.ark_video_api_timeout_seconds == 120
    assert settings.ark_image_timeout_auto_retries == 1
    assert settings.ark_image_retry_delay_seconds == 15
    assert settings.preflight_report()["arkImageRequestTimeoutSeconds"] == 600
    assert "ark_access_mode" not in settings.__dataclass_fields__


def test_image_request_timeout_must_be_positive(tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="请求超时必须大于0"):
        RuntimeSettings.from_env(
            settings_env(ARK_IMAGE_REQUEST_TIMEOUT_SECONDS="0"),
            config_root=tmp_path,
        )


@pytest.mark.parametrize(
    "name",
    [
        "ARK_DIRECTOR_REQUEST_TIMEOUT_SECONDS",
        "ARK_REVIEW_REQUEST_TIMEOUT_SECONDS",
        "ARK_VIDEO_API_TIMEOUT_SECONDS",
        "ARK_TASK_TIMEOUT_SECONDS",
        "ARK_POLL_INTERVAL_SECONDS",
        "ARK_IMAGE_RETRY_DELAY_SECONDS",
    ],
)
def test_all_runtime_timeouts_must_be_positive(name: str, tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="必须大于0"):
        RuntimeSettings.from_env(
            settings_env(**{name: "0"}),
            config_root=tmp_path,
        )


@pytest.mark.parametrize("value", ["-1", "2"])
def test_seedream_timeout_retry_count_is_bounded(value: str, tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="只允许0或1"):
        RuntimeSettings.from_env(
            settings_env(ARK_IMAGE_TIMEOUT_AUTO_RETRIES=value),
            config_root=tmp_path,
        )


@pytest.mark.parametrize("mode", ["technical_auto", "auto"])
def test_removed_storyboard_review_modes_fail(mode: str, tmp_path) -> None:
    settings = settings_env(STORYBOARD_REVIEW_MODE=mode)
    with pytest.raises(ConfigurationError, match="semantic_auto或manual"):
        RuntimeSettings.from_env(settings, config_root=tmp_path)


def test_agent_plan_url_is_rejected(tmp_path) -> None:
    settings = RuntimeSettings.from_env(
        settings_env(ARK_BASE_URL="https://ark.cn-beijing.volces.com/api/plan/v3"),
        config_root=tmp_path,
    )
    with pytest.raises(ConfigurationError, match="标准API"):
        settings.validate_for_ark_access()


def test_only_three_step_kinds_remain() -> None:
    assert {item.value for item in StepKind} == {"director", "image", "video"}


def test_prompt_purposes_match_storyboard_runtime() -> None:
    assert {item.value for item in PromptPurpose} == {
        "director",
        "storyboard",
        "storyboard_review",
        "video",
        "review",
    }


def test_explicit_workflow_transitions() -> None:
    assert transition_run(RunStatus.DRAFT, RunStatus.PLANNED) is RunStatus.PLANNED
    assert (
        transition_episode(EpisodeStatus.PLANNED, EpisodeStatus.PREPARING_VISUALS)
        is EpisodeStatus.PREPARING_VISUALS
    )
    assert transition_step(StepStatus.PENDING, StepStatus.SUBMITTING) is StepStatus.SUBMITTING
    with pytest.raises(ValueError):
        transition_step(StepStatus.SUCCEEDED, StepStatus.SUBMITTING)


def test_archived_statuses_are_removed() -> None:
    assert "archived" not in {item.value for item in RunStatus}
    assert "archived" not in {item.value for item in EpisodeStatus}
