from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from cat_video_generator.application.ports import GatewayError
from cat_video_generator.interfaces.jobs import (
    JobConflictError,
    JobRegistry,
)


def test_inline_job_runs_to_success() -> None:
    registry = JobRegistry(inline=True)
    record = registry.submit(
        kind="run_day",
        dedup_key="run:1:morning",
        fn=lambda: {"runId": "1"},
    )
    assert record.status == "succeeded"
    assert record.result == {"runId": "1"}
    assert record.error is None
    assert record.started_at is not None
    assert record.finished_at is not None


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ValueError("bad input"), "invalid_request"),
        (
            GatewayError("quota", code="quota_exceeded", retryable=False),
            "quota_exceeded",
        ),
        (TimeoutError("too slow"), "provider_timeout"),
        (RuntimeError("boom"), "internal"),
    ],
)
def test_inline_job_failure_is_classified(error: Exception, code: str) -> None:
    registry = JobRegistry(inline=True)

    def fail() -> None:
        raise error

    record = registry.submit(kind="resume", dedup_key="resume:1", fn=fail)
    assert record.status == "failed"
    assert record.error is not None
    assert record.error["code"] == code
    assert record.error["message"] == str(error)


def test_conflicting_dedup_key_is_rejected() -> None:
    release = threading.Event()
    with ThreadPoolExecutor(max_workers=2) as executor:
        registry = JobRegistry(executor=executor)
        first = registry.submit(
            kind="resume",
            dedup_key="resume:1",
            fn=lambda: release.wait(timeout=10),
        )
        with pytest.raises(JobConflictError) as captured:
            registry.submit(
                kind="resume",
                dedup_key="resume:1",
                fn=lambda: None,
            )
        assert captured.value.job_id == first.job_id
        release.set()


def test_different_dedup_key_is_accepted() -> None:
    release = threading.Event()
    with ThreadPoolExecutor(max_workers=2) as executor:
        registry = JobRegistry(executor=executor)
        registry.submit(
            kind="resume",
            dedup_key="resume:1",
            fn=lambda: release.wait(timeout=10),
        )
        second = registry.submit(
            kind="resume",
            dedup_key="resume:2",
            fn=lambda: None,
        )
        release.set()
    assert second.job_id in {record.job_id for record in registry.list()}


def test_paid_jobs_run_serially_through_the_gate() -> None:
    entered = threading.Event()
    release = threading.Event()
    order: list[str] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        registry = JobRegistry(executor=executor)

        def first() -> None:
            entered.set()
            release.wait(timeout=10)
            order.append("first")

        def second() -> None:
            order.append("second")

        registry.submit(kind="run_day", dedup_key="run:1:all", fn=first)
        assert entered.wait(timeout=10)
        registry.submit(kind="plan_day", dedup_key="plan:2026-07-30", fn=second)
        release.set()
    assert order == ["first", "second"]


def test_unknown_job_lookup_raises() -> None:
    registry = JobRegistry(inline=True)
    with pytest.raises(LookupError):
        registry.get("missing")


def test_list_returns_newest_first() -> None:
    registry = JobRegistry(inline=True)
    registry.submit(kind="resume", dedup_key="resume:1", fn=lambda: 1)
    registry.submit(kind="resume", dedup_key="resume:2", fn=lambda: 2)
    records = registry.list()
    assert [record.dedup_key for record in records] == ["resume:2", "resume:1"]
    assert records[0].to_dict()["dedupKey"] == "resume:2"
