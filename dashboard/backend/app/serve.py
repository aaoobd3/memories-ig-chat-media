"""Production launcher:  python -m app.serve

Starts
  * N web workers (uvicorn) that each restart after MAX_REQUESTS requests (+ random
    jitter, so they never all restart at once). Web workers keep no state of their own.
  * ONE background engine process (app.worker) that does all Instagram work, and is
    restarted automatically if it stops (it recycles itself after ENGINE_MAX_JOBS jobs).

Environment variables (all optional):
  HOST                 127.0.0.1   (0.0.0.0 to allow other devices / Docker)
  PORT                 8000
  WEB_WORKERS          min(4, CPU cores)
  MAX_REQUESTS         2000        requests a web worker serves before it is replaced (0 = never)
  MAX_REQUESTS_JITTER  200         random extra requests, so workers restart at different times
  ENGINE_MAX_JOBS      50          tasks the engine runs before it is replaced (0 = never)
"""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


class EngineSupervisor(threading.Thread):
    """Keeps exactly one engine process alive; restarts it when it recycles or crashes."""

    def __init__(self, env: dict):
        super().__init__(name="engine-supervisor", daemon=True)
        self.env = env
        self.proc: subprocess.Popen | None = None
        self.stopping = threading.Event()

    def run(self) -> None:
        backoff = 1.0
        while not self.stopping.is_set():
            started = time.monotonic()
            self.proc = subprocess.Popen([sys.executable, "-m", "app.worker"], cwd=str(BACKEND_DIR), env=self.env)
            code = self.proc.wait()
            if self.stopping.is_set():
                break
            if code == 0:
                print("[serve] engine recycled; starting a fresh one", file=sys.stderr)
                backoff = 1.0
                continue
            # crashed: wait a little longer each time it crashes quickly
            backoff = 1.0 if time.monotonic() - started > 60 else min(backoff * 2, 30)
            print(f"[serve] engine stopped unexpectedly (code {code}); restarting in {backoff:.0f}s", file=sys.stderr)
            self.stopping.wait(backoff)

    def stop(self) -> None:
        self.stopping.set()
        p = self.proc
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(25)
            except subprocess.TimeoutExpired:
                p.kill()


def main() -> None:
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    # Children (uvicorn workers, the engine) must be able to import `app`.
    os.environ["PYTHONPATH"] = os.pathsep.join(
        [str(BACKEND_DIR)] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p])

    from app.bootstrap import bootstrap
    bootstrap()  # once, before any worker starts
    os.environ["IGCM_SKIP_INIT"] = "1"
    os.environ["IGCM_PARENT_PID"] = str(os.getpid())  # children exit if this launcher disappears

    host = os.environ.get("HOST", "127.0.0.1")
    port = _int("PORT", 8000)
    workers = _int("WEB_WORKERS", min(4, os.cpu_count() or 2)) or 1
    max_requests = _int("MAX_REQUESTS", 2000)
    jitter = _int("MAX_REQUESTS_JITTER", 200)
    engine_max_jobs = _int("ENGINE_MAX_JOBS", 50)
    os.environ.update({"WEB_WORKERS": str(workers), "MAX_REQUESTS": str(max_requests),
                       "MAX_REQUESTS_JITTER": str(jitter), "ENGINE_MAX_JOBS": str(engine_max_jobs)})

    engine = EngineSupervisor({**os.environ, "IGCM_ROLE": "engine"})
    engine.start()

    os.environ["IGCM_ROLE"] = "web"  # web workers never run jobs themselves
    import uvicorn

    kwargs = dict(host=host, port=port, workers=workers, proxy_headers=True, forwarded_allow_ips="*",
                  app_dir=str(BACKEND_DIR), log_level=os.environ.get("LOG_LEVEL", "info"))
    if max_requests:
        kwargs["limit_max_requests"] = max_requests
        if jitter and "limit_max_requests_jitter" in inspect.signature(uvicorn.Config.__init__).parameters:
            kwargs["limit_max_requests_jitter"] = jitter
    print(f"[serve] http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}  "
          f"web workers={workers}  restart each after {max_requests or 'unlimited'} requests "
          f"(+0..{jitter})  engine restarts after {engine_max_jobs or 'unlimited'} tasks", file=sys.stderr)
    try:
        uvicorn.run("app.main:app", **kwargs)
    finally:
        engine.stop()


if __name__ == "__main__":
    main()
