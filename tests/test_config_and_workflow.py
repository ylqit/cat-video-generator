"""标准Ark配置与显式状态机。"""

from __future__ import annotations

import pytest

from cat_video_generator.config import ConfigurationError, RuntimeSettings
from cat_video_generator.domain.workflow import (
    EpisodeStatus,
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
        "KEYFRAME_REVIEW_MODE": "semantic_auto",
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
    assert "ark_access_mode" not in settings.__dataclass_fields__


@pytest.mark.parametrize("mode", ["technical_auto", "auto"])
def test_removed_keyframe_review_modes_fail(mode: str, tmp_path) -> None:
    settings = settings_env(KEYFRAME_REVIEW_MODE=mode)
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
