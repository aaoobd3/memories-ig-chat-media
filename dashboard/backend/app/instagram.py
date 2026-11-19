"""The Instagram connection: login (password or browser sessionid), saved session,
force refresh, and security codes typed into the dashboard instead of a terminal."""
from __future__ import annotations

import json
import os
import random
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import unquote

from . import settings as settings_mod
from .config import SESSION_FILE
from .db import get_db, now_iso, rt_del, rt_get, rt_set
from .security import get_secret

RATE_LIMIT_ERRORS = ("PleaseWaitFewMinutes", "RateLimitError", "FeedbackRequired")
CODE_TIMEOUT_S = 600


def explain(e: BaseException) -> str:
    """Turn any exception into a sentence a non-technical person can act on."""
    name = type(e).__name__
    msg = str(e) or name
    if isinstance(e, NotConnected) and str(e):
        return str(e)
    if name == "HTTPError" and ("401" in msg or "403" in msg):
        return ("Instagram didn't accept the login. If you use the browser cookie, it has probably expired: "
                "copy a fresh sessionid from instagram.com and press Save and connect.")
    friendly = {
        "BadPassword": "Instagram says the password is wrong. Check it on the Instagram account page.",
        "BadCredentials": "Instagram didn't accept the username or password.",
        "UserNotFound": "Instagram couldn't find that username.",
        "ClientNeedsUpgradeError": (
            "Instagram blocked the password login (“your version of Instagram is out of date”). "
            "This is a known problem. Use the browser cookie method instead: Instagram account → "
            "“Log in with browser cookie”. It can also mean the password is wrong."),
        "ChallengeRequired": (
            "Instagram wants to confirm it's really you. Open the Instagram app on your phone, "
            "tap “It was me” if asked, then press Reconnect."),
        "ChallengeUnknownStep": (
            "Instagram asked for a security check we can't do from here. Open the Instagram app, "
            "confirm it was you, then press Reconnect."),
        "SelectContactPointRecoveryForm": (
            "Instagram wants to confirm it's really you. Open the Instagram app on your phone, "
            "confirm the login, then press Reconnect."),
        "RecaptchaChallengeForm": "Instagram showed a captcha. Log in once in the Instagram app, then press Reconnect.",
        "LoginRequired": "The Instagram login expired. Press Reconnect on the Instagram account page.",
        "PleaseWaitFewMinutes": "Instagram asked us to slow down. Automatic checks are paused for a while.",
        "RateLimitError": "Instagram asked us to slow down. Automatic checks are paused for a while.",
        "FeedbackRequired": "Instagram temporarily limited this account. Automatic checks are paused for a while.",
        "ProxyAddressIsBlocked": "Instagram is blocking this internet connection (IP address). Try again later.",
        "ConnectionError": "Can't reach Instagram. Check the internet connection.",
        "ReadTimeout": "Instagram took too long to answer. Try again in a minute.",
        "ConnectTimeout": "Can't reach Instagram. Check the internet connection.",
        "NotConnected": "Connect your Instagram account first (Instagram account page).",
        "CodeTimeout": "No security code was entered in time. Press Reconnect to try again.",
        "PrivateError": "Instagram refused this request. Try again later.",
        "DirectThreadNotFound": "That chat wasn't found. It may have been deleted or you left it.",
    }
    if name in friendly:
        return friendly[name]
    if isinstance(e, (PermissionError,)):
        return f"The app isn't allowed to write there: {msg}"
    if isinstance(e, FileNotFoundError):
        return f"A file or folder is missing: {msg}"
    from .library import LibError
    if isinstance(e, LibError):
        return msg
    return f"Something went wrong: {msg}"


class NotConnected(Exception):
    pass


class CodeTimeout(Exception):
    pass


def normalize_sessionid(value: str) -> str:
    value = (value or "").strip().strip('"').strip("'")
    if value.lower().startswith("sessionid="):
        value = value.split("=", 1)[1]
    value = value.split(";", 1)[0].strip()
    if "%3a" in value.lower():
        value = unquote(value)
    return value


def sessionid_looks_valid(value: str) -> bool:
    return len(value) > 30 and value.split(":", 1)[0].isdigit()


_PUBLISHED = ("state", "username", "message", "last_ok", "pending")
_SNAPSHOT_KEY = "ig"
_ANSWER_KEY = "ig_code_answer"
_PAUSE_KEY = "ig_paused_until"
_RESET_KEY = "ig_reset"


def _snapshot() -> dict:
    return rt_get(_SNAPSHOT_KEY) or {}


class IGManager:
    """One instance per process. Only the background engine holds a live Instagram
    client; its state is published to the database so every web worker shows the
    same status, and codes/resume/sign-out requests travel back the same way."""

    def __init__(self) -> None:
        self._ready = False
        self._lock = threading.RLock()
        self.cl = None
        self.state = "disconnected"  # disconnected | connecting | connected | needs_code | error
        self.username: Optional[str] = None
        self.user_id: Optional[str] = None
        self.message = ""
        self.last_ok: Optional[str] = None
        self.pending: Optional[dict] = None
        self._reset_seen = 0
        self._ready = True

    def __setattr__(self, key, value) -> None:
        object.__setattr__(self, key, value)
        if key in _PUBLISHED and getattr(self, "_ready", False):
            self._publish()

    def _publish(self) -> None:
        rt_set(_SNAPSHOT_KEY, {k: getattr(self, k) for k in _PUBLISHED})

    def engine_started(self) -> None:
        """Called when this process becomes the engine: pick up the last known state."""
        snap = _snapshot()
        self._ready = False
        self.username = snap.get("username")
        self.last_ok = snap.get("last_ok")
        self.state = snap.get("state") or "disconnected"
        self.message = snap.get("message") or ""
        if self.state in ("connecting", "needs_code", "connected"):
            # nothing is live in this fresh process; the saved login is re-used on the next task
            self.state, self.message = "disconnected", ""
        self.pending = None
        self._reset_seen = rt_get(_RESET_KEY, 0)
        self._ready = True
        self._publish()

    # status (any process) ---------------------------------------------------
    def status(self) -> dict:
        snap = _snapshot()
        paused = self.paused_until()
        return {
            "state": snap.get("state") or "disconnected",
            "username": snap.get("username"),
            "message": snap.get("message") or "",
            "last_ok": snap.get("last_ok"),
            "session_saved": SESSION_FILE.exists(),
            "pending_code": snap.get("pending"),
            "paused_until": paused.isoformat(timespec="seconds") if paused else None,
        }

    @staticmethod
    def paused_until() -> Optional[datetime]:
        value = rt_get(_PAUSE_KEY)
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
        return dt if dt > datetime.now(timezone.utc) else None

    @staticmethod
    def pause(minutes: int = 60) -> None:
        until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        rt_set(_PAUSE_KEY, until.isoformat(timespec="seconds"))

    @staticmethod
    def resume() -> None:
        rt_set(_PAUSE_KEY, None)

    # security codes (asked by the engine, answered from any web worker) ------
    @staticmethod
    def submit_code(code: str) -> bool:
        pending = _snapshot().get("pending")
        if not pending:
            return False
        rt_set(_ANSWER_KEY, {"since": pending["since"], "code": (code or "").strip().replace(" ", "")})
        return True

    @staticmethod
    def cancel_code() -> None:
        pending = _snapshot().get("pending")
        if pending:
            rt_set(_ANSWER_KEY, {"since": pending["since"], "cancel": True})

    def _wait_code(self, kind: str, message: str) -> str:
        from .jobs import JobCancelled, current_context

        since = now_iso() + f"#{random.randint(1000, 9999)}"
        rt_del(_ANSWER_KEY)
        prev_state = self.state
        self.message = message
        self.pending = {"kind": kind, "message": message, "since": since}
        self.state = "needs_code"
        deadline = time.monotonic() + CODE_TIMEOUT_S
        try:
            while time.monotonic() < deadline:
                answer = rt_get(_ANSWER_KEY) or {}
                if answer.get("since") == since:
                    if answer.get("cancel") or not answer.get("code"):
                        raise CodeTimeout()
                    return answer["code"]
                ctx = current_context()
                if ctx is not None and ctx.cancelled:
                    raise JobCancelled()
                time.sleep(1)
            raise CodeTimeout()
        finally:
            rt_del(_ANSWER_KEY)
            self.pending = None
            self.state = prev_state if prev_state != "needs_code" else "connecting"

    def _new_client(self, delay_min: float, delay_max: float):
        from instagrapi import Client

        cl = Client()
        cl.delay_range = [delay_min, delay_max]

        def challenge_handler(username, choice):
            where = getattr(choice, "name", str(choice)).lower()
            where = {"email": "email", "sms": "phone (text message)"}.get(where, where)
            return self._wait_code("challenge", f"Instagram sent a security code to your {where}. Type it here.")

        cl.challenge_code_handler = challenge_handler
        return cl

    def connect(self, log, force: bool = False) -> None:
        """Log in (runs inside a job). force=True throws away the saved session first."""
        from instagrapi.exceptions import ClientNeedsUpgradeError, LoginRequired, TwoFactorRequired

        with self._lock:
            with get_db() as conn:
                s = settings_mod.load(conn)
                password = get_secret(conn, "ig_password")
                sessionid = get_secret(conn, "ig_sessionid")
            username = s["ig_username"]
            method = s["login_method"]
            self.state, self.message = "connecting", "Connecting to Instagram..."
            cl = self._new_client(s["delay_min"], s["delay_max"])
            uuids = None
            try:
                if SESSION_FILE.exists():
                    try:
                        saved = json.loads(SESSION_FILE.read_text("utf-8"))
                        uuids = saved.get("uuids")
                    except (OSError, ValueError):
                        saved = None
                    if force:
                        log("Forgetting the saved login and signing in again...")
                        SESSION_FILE.unlink(missing_ok=True)
                    elif saved:
                        try:
                            cl.load_settings(SESSION_FILE)
                            cl.get_timeline_feed()
                            self._connected(cl, log, "Using the saved login.")
                            return
                        except LoginRequired:
                            log("The saved login expired; signing in again...")
                        except Exception as e:  # noqa: BLE001
                            if type(e).__name__ in RATE_LIMIT_ERRORS:
                                raise
                            log(f"Couldn't reuse the saved login ({type(e).__name__}); signing in again...")
                        cl = self._new_client(s["delay_min"], s["delay_max"])
                if uuids:
                    cl.set_uuids(uuids)  # same "device" as before, so Instagram isn't alarmed

                use_cookie = sessionid and method in ("auto", "sessionid")
                if method == "sessionid" and not sessionid:
                    raise NotConnected("Paste your browser cookie (sessionid) on the Instagram account page first.")
                if use_cookie:
                    log("Signing in with the browser cookie...")
                    cl.login_by_sessionid(sessionid)
                else:
                    if not username or not password:
                        raise NotConnected("Enter your Instagram username and password (or a browser cookie) first.")
                    log(f"Signing in as @{username}...")
                    try:
                        try:
                            cl.login(username, password)
                        except TwoFactorRequired:
                            code = self._wait_code(
                                "two_factor",
                                "Enter the 6-digit code from your authenticator app or the text message Instagram sent.")
                            log("Code received, finishing sign-in...")
                            cl.login(username, password, verification_code=code)
                    except ClientNeedsUpgradeError:
                        if sessionid and method == "auto":
                            log("Instagram blocked the password login; trying the browser cookie instead...")
                            cl = self._new_client(s["delay_min"], s["delay_max"])
                            if uuids:
                                cl.set_uuids(uuids)
                            cl.login_by_sessionid(sessionid)
                        else:
                            raise
                SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
                cl.dump_settings(SESSION_FILE)
                try:
                    os.chmod(SESSION_FILE, 0o600)
                except OSError:
                    pass
                self._connected(cl, log, "Signed in and saved the login.")
            except NotConnected as e:
                self.cl, self.state, self.message = None, "disconnected", str(e)
                raise
            except Exception as e:  # noqa: BLE001
                self.cl, self.state, self.message = None, "error", explain(e)
                if type(e).__name__ in RATE_LIMIT_ERRORS:
                    self.pause(60)
                raise

    def _connected(self, cl, log, note: str) -> None:
        name = cl.username
        if not name:  # a re-used saved session doesn't remember the username
            with get_db() as conn:
                name = settings_mod.get(conn, "ig_username") or None
        if not name and cl.user_id:
            try:
                name = cl.account_info().username
            except Exception:  # noqa: BLE001
                name = None
        cl.username = name or cl.username
        self.cl = cl
        self.username = name or self.username
        self.user_id = str(cl.user_id) if cl.user_id else None
        self.state, self.message, self.last_ok = "connected", "", now_iso()
        if self.username:
            with get_db() as conn:
                if not settings_mod.get(conn, "ig_username"):
                    settings_mod.update(conn, {"ig_username": self.username})
        log(f"{note} Connected as @{self.username}.")

    def client(self, log):
        """The live Instagram client (engine only). Signs in on first use."""
        with self._lock:
            reset = rt_get(_RESET_KEY, 0)
            if reset != self._reset_seen:  # signed out from the dashboard meanwhile
                self._reset_seen, self.cl = reset, None
            if self.cl is None:
                self.connect(log)
            return self.cl

    def mark_failure(self, e: BaseException) -> None:
        """Called when a request fails after login (e.g. session expired)."""
        name = type(e).__name__
        if name == "LoginRequired":
            self.cl, self.state, self.message = None, "error", explain(e)
        elif name in RATE_LIMIT_ERRORS:
            self.pause(60 + random.randint(0, 30))
            self.message = explain(e)

    @staticmethod
    def logout(forget_secrets: bool = False) -> None:
        """Sign out (callable from any web worker; the engine drops its client on its next task)."""
        SESSION_FILE.unlink(missing_ok=True)
        if forget_secrets:
            from .security import put_secret
            with get_db() as conn:
                put_secret(conn, "ig_password", None)
                put_secret(conn, "ig_sessionid", None)
        rt_set(_RESET_KEY, (rt_get(_RESET_KEY, 0) or 0) + 1)
        snap = _snapshot()
        rt_set(_SNAPSHOT_KEY, {**snap, "state": "disconnected", "message": "", "username": None, "pending": None})


ig = IGManager()
