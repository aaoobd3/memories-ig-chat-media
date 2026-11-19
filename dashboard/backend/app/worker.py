"""The background engine process: runs Instagram syncs, imports and the scheduler.

Started by app.serve. Exits with code 0 after ENGINE_MAX_JOBS jobs so the launcher
can start a fresh process (keeps memory flat over weeks of automatic checks).
"""
from __future__ import annotations

import os
import signal
import sys

from .bootstrap import bootstrap
from .jobs import runner
from .procutil import watch_launcher


def main() -> int:
    if os.environ.get("IGCM_SKIP_INIT") != "1":
        bootstrap()
    max_jobs = int(os.environ.get("ENGINE_MAX_JOBS", "0") or 0)

    def stop(*_):
        runner.stop_engine(timeout=20)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, stop)
        except (ValueError, OSError):
            pass

    runner.start_engine(max_jobs=max_jobs)
    watch_launcher(lambda: runner.stop_engine(timeout=10))
    try:
        while not runner.stopped.wait(1):
            pass
    except KeyboardInterrupt:
        runner.stop_engine(timeout=20)
    return 0


if __name__ == "__main__":
    sys.exit(main())
