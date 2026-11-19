"""Background jobs.

The job queue lives in SQLite, so any web worker can add, cancel or watch a job.
Exactly ONE background engine processes them (guarded by a lease row in the
`runtime` table), so Instagram only ever sees one task at a time no matter how
many web workers are running. The engine can recycle itself after N jobs; the
launcher (app.serve) then starts a fresh one.
"""
from __future__ import annotations

import io
import json
import os
import random
import socket
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from .db import get_db, now_iso, rt_get, rt_set

Handler = Callable[["JobContext", dict], Optional[dict]]
_handlers: dict[str, Handler] = {}
_local = threading.local()

LEASE_KEY = "engine_lease"
LEASE_TTL_S = 20          # another engine may take over this long after the holder stops renewing
LEASE_RENEW_S = 5
POLL_S = 1.0              # how often the engine looks for queued jobs


def handler(kind: str):
    def deco(fn: Handler) -> Handler:
        _handlers[kind] = fn
        return fn
    return deco


class JobCancelled(Exception):
    pass


def current_context() -> Optional["JobContext"]:
    return getattr(_local, "ctx", None)


class JobContext:
    def __init__(self, job_id: int):
        self.job_id = job_id
        self.stats: dict[str, Any] = {}
        self._cancel = threading.Event()
        self._last_flush = 0.0
        self._last_cancel_check = 0.0
        self.progress = 0
        self.total = 0

    # logging -------------------------------------------------------------
    def log(self, message: str, level: str = "info") -> None:
        message = str(message).rstrip()
        if not message:
            return
        with get_db() as conn:
            conn.execute("INSERT INTO job_logs(job_id, ts, level, message) VALUES (?, ?, ?, ?)",
                         (self.job_id, now_iso(), level, message[:4000]))

    def warn(self, message: str) -> None:
        self.log(message, "warn")

    def error(self, message: str) -> None:
        self.log(message, "error")

    def success(self, message: str) -> None:
        self.log(message, "success")

    # progress ------------------------------------------------------------
    def set_total(self, total: int) -> None:
        self.total = total
        self.flush(force=True)

    def step(self, n: int = 1) -> None:
        self.progress += n
        self.flush()

    def bump(self, key: str, n: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + n
        self.flush()

    def flush(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_flush < 0.7:
            return
        self._last_flush = now
        with get_db() as conn:
            conn.execute("UPDATE jobs SET progress = ?, total = ?, stats = ? WHERE id = ?",
                         (self.progress, self.total, json.dumps(self.stats), self.job_id))

    # cancel (requested from any web worker through the database) ----------
    @property
    def cancelled(self) -> bool:
        if self._cancel.is_set():
            return True
        now = time.monotonic()
        if now - self._last_cancel_check >= 1.0:
            self._last_cancel_check = now
            with get_db() as conn:
                row = conn.execute("SELECT cancel_requested FROM jobs WHERE id = ?", (self.job_id,)).fetchone()
            if row is None or row["cancel_requested"]:
                self._cancel.set()
        return self._cancel.is_set()

    def check(self) -> None:
        if self.cancelled:
            raise JobCancelled()

    def sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.check()
            time.sleep(min(0.5, end - time.monotonic()))


class _ThreadStdout(io.TextIOBase):
    """Routes print() from the reused CLI code into the running job's log."""

    def __init__(self, original):
        self.original = original
        self._buf = threading.local()

    def write(self, s: str) -> int:
        ctx = current_context()
        if ctx is None:
            return self.original.write(s)
        buf = getattr(self._buf, "text", "") + s
        *lines, rest = buf.split("\n")
        self._buf.text = rest
        for line in lines:
            if line.strip():
                ctx.log(line.strip(), "error" if "failed" in line.lower() else "info")
        return len(s)

    def flush(self) -> None:
        if current_context() is None:
            self.original.flush()

    @property
    def encoding(self):  # type: ignore[override]
        return getattr(self.original, "encoding", "utf-8")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobRunner:
    """Queue API (usable from any process) + the engine loop (one process only)."""

    def __init__(self) -> None:
        self.owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._started = False
        self._stop = threading.Event()
        self.stopped = threading.Event()       # set when the engine has fully stopped
        self._has_lease = False
        self.max_jobs = 0
        self.jobs_done = 0
        self._current: Optional[JobContext] = None

    # ------------------------------------------------------------------ #
    # Queue API: safe from web workers
    # ------------------------------------------------------------------ #
    def enqueue(self, kind: str, title: str, params: Optional[dict] = None, dedupe: bool = True) -> int:
        params = params or {}
        payload = json.dumps(params, sort_keys=True)
        with get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")  # make the dedupe check + insert atomic across processes
            if dedupe:
                row = conn.execute(
                    "SELECT id FROM jobs WHERE kind = ? AND params = ? AND status IN ('queued', 'running')",
                    (kind, payload)).fetchone()
                if row:
                    return row["id"]
            return conn.execute(
                "INSERT INTO jobs(kind, title, status, params, created_at) VALUES (?, ?, 'queued', ?, ?)",
                (kind, title, payload, now_iso())).lastrowid

    def cancel(self, job_id: int) -> bool:
        with get_db() as conn:
            cur = conn.execute("UPDATE jobs SET status = 'cancelled', finished_at = ? "
                               "WHERE id = ? AND status = 'queued'", (now_iso(), job_id))
            if cur.rowcount:
                return True
            cur = conn.execute("UPDATE jobs SET cancel_requested = 1 WHERE id = ? AND status = 'running'", (job_id,))
            return cur.rowcount > 0

    def busy_with(self, kind: str) -> bool:
        with get_db() as conn:
            return conn.execute("SELECT 1 FROM jobs WHERE kind = ? AND status IN ('queued','running')",
                                (kind,)).fetchone() is not None

    def poke_scheduler(self, conn=None) -> None:
        """Tell the engine's scheduler to re-plan. Pass the request's connection when it
        has uncommitted writes, so we don't wait on our own transaction."""
        if conn is None:
            rt_set("scheduler_poke", time.time())
            return
        conn.execute(
            "INSERT INTO runtime(key, value, updated_at) VALUES ('scheduler_poke', ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (json.dumps(time.time()), now_iso()))

    @staticmethod
    def next_auto_run() -> Optional[str]:
        return rt_get("next_auto_run")

    @staticmethod
    def engine_info() -> dict:
        lease = rt_get(LEASE_KEY) or {}
        alive = bool(lease) and lease.get("expires", 0) > time.time()
        return {"alive": alive, "pid": lease.get("pid"), "since": lease.get("since"),
                "jobs_done": lease.get("jobs_done", 0), "max_jobs": lease.get("max_jobs", 0)}

    # ------------------------------------------------------------------ #
    # Engine (exactly one process at a time)
    # ------------------------------------------------------------------ #
    def start_engine(self, max_jobs: int = 0) -> None:
        if self._started:
            return
        self._started = True
        self.max_jobs = max_jobs
        if not isinstance(sys.stdout, _ThreadStdout):
            sys.stdout = _ThreadStdout(sys.stdout)
        from . import engine  # noqa: F401  (registers the job handlers)
        threading.Thread(target=self._lease_loop, name="engine-lease", daemon=True).start()
        threading.Thread(target=self._worker, name="engine-worker", daemon=True).start()
        threading.Thread(target=self._scheduler, name="engine-scheduler", daemon=True).start()

    def stop_engine(self, timeout: float = 15) -> None:
        """Ask the engine to stop after the current job (used on shutdown)."""
        self._stop.set()
        if self._current:
            self._current._cancel.set()
        self.stopped.wait(timeout)

    def _try_lease(self) -> bool:
        now = time.time()
        with get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM runtime WHERE key = ?", (LEASE_KEY,)).fetchone()
            lease = json.loads(row["value"]) if row else None
            if lease and lease.get("owner") != self.owner and lease.get("expires", 0) > now:
                return False
            fresh = not (lease and lease.get("owner") == self.owner)
            value = {"owner": self.owner, "pid": os.getpid(), "expires": now + LEASE_TTL_S,
                     "since": lease.get("since") if not fresh else now_iso(),
                     "jobs_done": self.jobs_done, "max_jobs": self.max_jobs}
            conn.execute(
                "INSERT INTO runtime(key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (LEASE_KEY, json.dumps(value), now_iso()))
            if fresh:
                # Whatever the previous engine was doing died with it.
                conn.execute("UPDATE jobs SET status = 'interrupted', finished_at = ? WHERE status = 'running'",
                             (now_iso(),))
            return True

    def _release_lease(self) -> None:
        with get_db() as conn:
            row = conn.execute("SELECT value FROM runtime WHERE key = ?", (LEASE_KEY,)).fetchone()
            if row and json.loads(row["value"]).get("owner") == self.owner:
                conn.execute("DELETE FROM runtime WHERE key = ?", (LEASE_KEY,))
        self._has_lease = False

    def _lease_loop(self) -> None:
        announced = False
        while not self._stop.is_set():
            try:
                got = self._try_lease()
                if got and not self._has_lease:
                    from .instagram import ig
                    ig.engine_started()
                    print(f"[engine] running (pid {os.getpid()})", file=sys.__stderr__)
                elif not got and not announced:
                    print("[engine] another engine is running; standing by", file=sys.__stderr__)
                    announced = True
                self._has_lease = got
            except Exception:  # noqa: BLE001
                print(traceback.format_exc(), file=sys.__stderr__)
            self._stop.wait(LEASE_RENEW_S if self._has_lease else 3)
        self._release_lease()

    def _claim_next(self) -> Optional[Any]:
        with get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return None
            conn.execute("UPDATE jobs SET status = 'running', started_at = ? WHERE id = ?", (now_iso(), row["id"]))
            return row

    def _worker(self) -> None:
        try:
            while not self._stop.is_set():
                if not self._has_lease:
                    self._stop.wait(POLL_S)
                    continue
                try:
                    row = self._claim_next()
                except Exception:  # noqa: BLE001 - e.g. database briefly locked
                    print(traceback.format_exc(), file=sys.__stderr__)
                    row = None
                if row is None:
                    self._stop.wait(POLL_S)
                    continue
                self._run(row)
                self.jobs_done += 1
                if self.max_jobs and self.jobs_done >= self.max_jobs:
                    print(f"[engine] finished {self.jobs_done} jobs; recycling", file=sys.__stderr__)
                    self._stop.set()
        finally:
            self._release_lease()
            self.stopped.set()

    def _run(self, row) -> None:
        job_id = row["id"]
        ctx = JobContext(job_id)
        self._current = ctx
        _local.ctx = ctx
        status, error, result = "done", None, None
        try:
            fn = _handlers.get(row["kind"])
            if fn is None:
                raise RuntimeError(f"unknown task type {row['kind']}")
            result = fn(ctx, json.loads(row["params"]))
            if ctx.cancelled:
                status = "cancelled"
        except JobCancelled:
            status = "cancelled"
            ctx.warn("Stopped.")
        except Exception as e:  # noqa: BLE001 - surface every failure to the user
            from .instagram import explain
            status, error = "failed", explain(e)
            ctx.error(error)
            print(traceback.format_exc(), file=sys.__stderr__)
        finally:
            _local.ctx = None
            self._current = None
            if isinstance(result, dict):
                ctx.stats.update(result)
            with get_db() as conn:
                conn.execute(
                    "UPDATE jobs SET status = ?, error = ?, finished_at = ?, stats = ?, progress = ?, total = ? "
                    "WHERE id = ?",
                    (status, error, now_iso(), json.dumps(ctx.stats), ctx.progress, ctx.total, job_id))

    def _scheduler(self) -> None:
        from . import settings as settings_mod
        from .instagram import ig
        next_run: Optional[datetime] = None
        seen_poke = rt_get("scheduler_poke")
        last_housekeeping = 0.0
        while not self._stop.wait(3):
            if not self._has_lease:
                continue
            try:
                poke = rt_get("scheduler_poke")
                if poke != seen_poke:
                    seen_poke, next_run = poke, None
                with get_db() as conn:
                    s = settings_mod.load(conn)
                    auto_chats = [r["id"] for r in conn.execute(
                        "SELECT id FROM chats WHERE auto_sync = 1 AND source = 'live' ORDER BY id")]
                now = _utcnow()
                paused = ig.paused_until()
                if not s["auto_sync"] or not auto_chats:
                    next_run = None
                elif paused and paused > now:
                    next_run = paused
                elif next_run is None:
                    next_run = now + timedelta(seconds=20)
                elif now >= next_run:
                    if not self.busy_with("sync"):
                        self.enqueue("sync", "Automatic check for new media",
                                     {"chat_ids": auto_chats, "mode": "watch", "auto": True})
                    seconds = s["auto_interval_min"] * 60
                    next_run = now + timedelta(seconds=seconds + random.uniform(0, seconds * 0.25))
                rt_set("next_auto_run", next_run.isoformat(timespec="seconds") if next_run else None)
                if time.time() - last_housekeeping > 6 * 3600:
                    last_housekeeping = time.time()
                    self.enqueue("housekeeping", "Tidy up (trash, old logs)", {})
            except Exception:  # noqa: BLE001 - the scheduler must never die
                print(traceback.format_exc(), file=sys.__stderr__)


runner = JobRunner()
