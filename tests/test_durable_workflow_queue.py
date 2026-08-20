from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cat_video_generator.infrastructure.db.durable_queue import is_claimable, operation_matches


def test_pending_work_is_claimable_when_retry_window_is_open() -> None:
    now = datetime(2026, 8, 20, 1, 0, tzinfo=UTC)

    assert is_claimable(
        status="pending",
        lease_expires_at=None,
        next_retry_at=None,
        now=now,
    )
    assert not is_claimable(
        status="pending",
        lease_expires_at=None,
        next_retry_at=now + timedelta(seconds=1),
        now=now,
    )


def test_expired_worker_lease_can_be_recovered_but_unknown_submission_cannot() -> None:
    now = datetime(2026, 8, 20, 1, 0, tzinfo=UTC)

    assert is_claimable(
        status="running",
        lease_expires_at=now - timedelta(seconds=1),
        next_retry_at=None,
        now=now,
    )
    assert not is_claimable(
        status="running",
        lease_expires_at=now + timedelta(seconds=30),
        next_retry_at=None,
        now=now,
    )
    assert not is_claimable(
        status="submission_unknown",
        lease_expires_at=now - timedelta(minutes=10),
        next_retry_at=None,
        now=now,
    )


def test_specialized_worker_only_claims_owned_operation_prefixes() -> None:
    prefixes = ("media:image:batch:", "video:edit-recipe:")

    assert operation_matches("media:image:batch:123:candidate:1", prefixes)
    assert operation_matches("video:edit-recipe:456", prefixes)
    assert not operation_matches("director:story_candidate", prefixes)
