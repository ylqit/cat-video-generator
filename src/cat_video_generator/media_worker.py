"""Command-line worker for durable Creator generation tasks."""

from __future__ import annotations

import argparse
import logging
import time

from .bootstrap import build_runtime_container

LOGGER = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Creator generation worker")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not 0.1 <= args.poll_seconds <= 60:
        parser.error("--poll-seconds must be between 0.1 and 60")

    container = build_runtime_container()
    try:
        while True:
            lease = container.task_queue.claim_next(worker_id="creator-worker")
            if lease is None:
                if args.once:
                    return
                time.sleep(args.poll_seconds)
                continue
            try:
                result = container.creator_executor.execute(lease.task_id)
                container.task_queue.finish(
                    lease,
                    status=result.status,
                    next_attempt_at=result.next_attempt_at,
                    payload=result.payload,
                )
            except Exception as exc:
                container.task_queue.fail(lease, exc)
                LOGGER.exception("Creator generation task failed")
                if args.once:
                    raise
            if args.once:
                return
    finally:
        container.close()


if __name__ == "__main__":
    main()
