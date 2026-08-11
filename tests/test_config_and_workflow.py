"""环境配置、五阶段设置与收费恢复状态机。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cat_video_generator.config import ConfigurationError, RuntimeSettings
from cat_video_generator.domain.pipeline import PIPELINE_STAGES, PipelineSettings, StageMode
from cat_video_generator.domain.workflow import (
    PromptPurpose,
    StepKind,
    StepStatus,
    WorkflowTransitionError,
    transition_step,
    validate_prompt_purpose,
)


def runtime_env(tmp_path: Path) -> dict[str, str]:
    event_root = tmp_path / "events"
    event_root.mkdir()
    return {
        "ARK_API_KEY": "test-key",
        "ARK_IMAGE_MODEL": "doubao-seedream-5-0-260128",
        "ARK_VIDEO_MODEL": "doubao-seedance-2-0-260128",
        "ARK_PLANNING_MODEL": "doubao-seed-2-1-pro-260628",
        "ARK_REVIEW_MODEL": "doubao-seed-2-1-pro-260628",
        "ARK_VIDEO_RESOLUTION": "720p",
        "IMAGE_REVIEW_MODE": "advisory",
        "CAT_VIDEO_EVENT_SEED_ROOT": str(event_root),
        "FFMPEG_PATH": str(tmp_path / "ffmpeg"),
        "FFPROBE_PATH": str(tmp_path / "ffprobe"),
    }


def test_runtime_exposes_full_model_extension_and_timeouts(tmp_path: Path) -> None:
    settings = RuntimeSettings.from_env(runtime_env(tmp_path), config_root=tmp_path)
    report = settings.preflight_report()

    assert report["supportsVideoExtension"] is True
    assert report["arkTaskTimeoutSeconds"] == 1800.0
    assert "arkImageTimeoutAutoRetries" not in report
    assert report["imageReviewMode"] == "advisory"


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("ARK_TASK_TIMEOUT_SECONDS", "0", "必须大于0"),
        ("IMAGE_REVIEW_MODE", "technical_auto", "advisory或manual"),
    ],
)
def test_invalid_runtime_values_fail_at_startup(tmp_path, name, value, message) -> None:
    env = runtime_env(tmp_path)
    env[name] = value

    with pytest.raises(ConfigurationError, match=message):
        RuntimeSettings.from_env(env, config_root=tmp_path)


def test_pipeline_has_only_five_current_stages() -> None:
    assert PIPELINE_STAGES == ("projectOutline", "script", "visual", "video", "review")
    settings = PipelineSettings.model_validate(
        {
            "allowPaidGeneration": True,
            "projectOutline": "manual",
            "script": "auto",
            "visual": "manual",
            "video": "manual",
            "review": "manual",
        }
    )
    assert settings.stage("projectOutline") is StageMode.MANUAL
    assert settings.stage("visual") is StageMode.MANUAL


def test_prompt_purposes_are_reduced_to_four_values() -> None:
    assert {item.value for item in PromptPurpose} == {"director", "image", "video", "review"}
    assert validate_prompt_purpose(StepKind.IMAGE, PromptPurpose.IMAGE, generation_intent=True)
    assert validate_prompt_purpose(StepKind.VIDEO, PromptPurpose.REVIEW)

    with pytest.raises(ValueError, match="生成Prompt必须是image"):
        validate_prompt_purpose(StepKind.IMAGE, PromptPurpose.REVIEW, generation_intent=True)


def test_submission_unknown_cannot_return_to_submitting() -> None:
    with pytest.raises(WorkflowTransitionError):
        transition_step(StepStatus.SUBMISSION_UNKNOWN, StepStatus.SUBMITTING)

    assert transition_step(StepStatus.SUBMISSION_UNKNOWN, StepStatus.RUNNING) is StepStatus.RUNNING
