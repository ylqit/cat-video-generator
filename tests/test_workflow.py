from __future__ import annotations

import pytest

from cat_video_generator.domain.workflow import (
    EpisodeStatus,
    RunStatus,
    StepStatus,
    WorkflowTransitionError,
    transition_episode,
    transition_run,
    transition_step,
)


def test_normal_workflow_transitions_are_explicit() -> None:
    assert transition_run(RunStatus.DRAFT, RunStatus.PLANNED) is RunStatus.PLANNED
    assert (
        transition_episode(
            EpisodeStatus.VIDEO_GENERATING,
            EpisodeStatus.MEDIA_QC,
        )
        is EpisodeStatus.MEDIA_QC
    )
    assert (
        transition_step(StepStatus.SUBMITTING, StepStatus.QUEUED) is StepStatus.QUEUED
    )


def test_submission_unknown_cannot_return_to_submitting() -> None:
    with pytest.raises(WorkflowTransitionError):
        transition_step(
            StepStatus.SUBMISSION_UNKNOWN,
            StepStatus.SUBMITTING,
        )


def test_manual_keyframe_can_wait_then_succeed() -> None:
    assert (
        transition_step(
            StepStatus.SUBMITTING,
            StepStatus.AWAITING_REVIEW,
        )
        is StepStatus.AWAITING_REVIEW
    )
    assert (
        transition_step(
            StepStatus.AWAITING_REVIEW,
            StepStatus.SUCCEEDED,
        )
        is StepStatus.SUCCEEDED
    )


def test_archived_records_are_read_only() -> None:
    with pytest.raises(WorkflowTransitionError):
        transition_run(RunStatus.ARCHIVED, RunStatus.GENERATING)
