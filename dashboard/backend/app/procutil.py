"""Small process helpers: make child processes exit when the launcher is gone."""
from __future__ import annotations

import os
import sys
import threading
from typing import Callable


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT: still running
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def watch_launcher(on_gone: Callable[[], None], interval: float = 2.0) -> None:
    """If IGCM_PARENT_PID (set by app.serve) stops running, call on_gone() and exit.

    Without this, killing the launcher (e.g. Task Manager on Windows) would leave the
    engine running in the background as an orphan."""
    try:
        parent = int(os.environ.get("IGCM_PARENT_PID", "0"))
    except ValueError:
        return
    if parent <= 0:
        return

    def loop() -> None:
        stop = threading.Event()
        while not stop.wait(interval):
            if not pid_alive(parent):
                print(f"[{os.getpid()}] launcher {parent} is gone; exiting", file=sys.__stderr__)
                try:
                    on_gone()
                finally:
                    os._exit(0)

    threading.Thread(target=loop, name="launcher-watchdog", daemon=True).start()
