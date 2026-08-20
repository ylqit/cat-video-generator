"""Command-line process for durable universal media canvas jobs."""

from __future__ import annotations

import argparse
import time

from .bootstrap import build_runtime_container


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the universal media canvas worker")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not 0.1 <= args.poll_seconds <= 60:
        parser.error("--poll-seconds must be between 0.1 and 60")

    container = build_runtime_container()
    try:
        while True:
            result = container.media_canvas_worker.run_once()
            if args.once or result is not None:
                if args.once:
                    break
                continue
            time.sleep(args.poll_seconds)
    finally:
        container.close()


if __name__ == "__main__":
    main()
