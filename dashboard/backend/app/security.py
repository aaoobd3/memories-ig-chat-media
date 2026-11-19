"""Dashboard passcode, login sessions, and encryption of stored Instagram secrets."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, Request

from . import config
from .db import db_dep, get_setting, now_iso, set_setting

COOKIE_NAME = "igcm_session"
SESSION_DAYS = 30
MIN_PASSCODE = 4


# --------------------------------------------------------------------------- #
# Passcode hashing (scrypt)
# --------------------------------------------------------------------------- #
def hash_passcode(passcode: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(passcode.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_passcode(passcode: str, stored: str) -> bool:
    try:
        _, salt_b64, digest_b64 = stored.split("$")
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
    except ValueError:
        return False
    digest = hashlib.scrypt(passcode.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return hmac.compare_digest(digest, expected)


def passcode_is_set(conn: sqlite3.Connection) -> bool:
    return bool(get_setting(conn, "auth_passcode"))


def set_passcode(conn: sqlite3.Connection, passcode: str) -> None:
    if len(passcode or "") < MIN_PASSCODE:
        raise HTTPException(400, f"The passcode must be at least {MIN_PASSCODE} characters.")
    set_setting(conn, "auth_passcode", hash_passcode(passcode))


def apply_preset_passcode(conn: sqlite3.Connection) -> None:
    if config.PRESET_PASSCODE and not passcode_is_set(conn):
        set_passcode(conn, config.PRESET_PASSCODE)


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn: sqlite3.Connection) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    conn.execute(
        "INSERT INTO auth_sessions(token_hash, created_at, expires_at) VALUES (?, ?, ?)",
        (_token_hash(token), now_iso(), expires.isoformat(timespec="seconds")),
    )
    conn.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (now_iso(),))
    return token


def session_valid(conn: sqlite3.Connection, token: Optional[str]) -> bool:
    if not token:
        return False
    row = conn.execute(
        "SELECT 1 FROM auth_sessions WHERE token_hash = ? AND expires_at > ?",
        (_token_hash(token), now_iso()),
    ).fetchone()
    return row is not None


def drop_session(conn: sqlite3.Connection, token: Optional[str]) -> None:
    if token:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (_token_hash(token),))


def drop_all_sessions(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM auth_sessions")


def require_auth(request: Request, conn: sqlite3.Connection = Depends(db_dep)) -> None:
    if not session_valid(conn, request.cookies.get(COOKIE_NAME)):
        raise HTTPException(401, "Please sign in.")


def is_https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto.split(",")[0].strip() == "https"


# --------------------------------------------------------------------------- #
# Brute-force protection for the passcode screen (per client IP). Stored in the
# database so the limit holds across all web workers; each call commits on its
# own connection so a failed attempt is recorded even though the request errors.
# --------------------------------------------------------------------------- #
MAX_ATTEMPTS, WINDOW_S = 8, 300


def check_rate(ip: str) -> None:
    from .db import get_db
    now = time.time()
    with get_db() as conn:
        conn.execute("DELETE FROM auth_failures WHERE ts < ?", (now - WINDOW_S,))
        n = conn.execute("SELECT count(*) FROM auth_failures WHERE ip = ? AND ts >= ?",
                         (ip, now - WINDOW_S)).fetchone()[0]
    if n >= MAX_ATTEMPTS:
        raise HTTPException(429, "Too many tries. Wait 5 minutes and try again.")


def record_failure(ip: str) -> None:
    from .db import get_db
    with get_db() as conn:
        conn.execute("INSERT INTO auth_failures(ip, ts) VALUES (?, ?)", (ip, time.time()))


def clear_failures(ip: str) -> None:
    from .db import get_db
    with get_db() as conn:
        conn.execute("DELETE FROM auth_failures WHERE ip = ?", (ip,))


# --------------------------------------------------------------------------- #
# Encrypted secrets (Instagram password, sessionid)
# --------------------------------------------------------------------------- #
_fernet: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = config.APP_SECRET_KEY.encode() if config.APP_SECRET_KEY else None
        if key is None:
            key = ensure_key_file()
        _fernet = Fernet(key)
    return _fernet


def ensure_key_file() -> bytes:
    """Create secret.key exactly once, even if several processes start together."""
    path = config.SECRET_KEY_FILE
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        for _ in range(50):  # another process may still be writing it
            key = path.read_bytes().strip()
            if key:
                return key
            time.sleep(0.1)
        raise RuntimeError(f"{path} is empty; delete it and restart")
    key = Fernet.generate_key()
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return key


def put_secret(conn: sqlite3.Connection, key: str, value: Optional[str]) -> None:
    if not value:
        conn.execute("DELETE FROM secrets WHERE key = ?", (key,))
        return
    token = _get_fernet().encrypt(value.encode())
    conn.execute(
        "INSERT INTO secrets(key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, token, now_iso()),
    )


def get_secret(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM secrets WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    try:
        return _get_fernet().decrypt(row["value"]).decode()
    except InvalidToken:
        return None  # key changed; the user just needs to type it again


def secret_info(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT updated_at FROM secrets WHERE key = ?", (key,)).fetchone()
    return row["updated_at"] if row else None
