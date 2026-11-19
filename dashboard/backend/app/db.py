"""SQLite storage: schema, indexes, connections and small helpers.

The database is the single source of truth for settings, chats, the folder tree,
files, the download index (what was already fetched), jobs and their logs.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from .config import DB_PATH

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS secrets (
    key        TEXT PRIMARY KEY,
    value      BLOB NOT NULL,
    updated_at TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_auth_sessions_expires ON auth_sessions(expires_at);

-- Folder tree. role: root | chat (a chat's home) | type (Photos/Videos/...) | user
CREATE TABLE IF NOT EXISTS folders (
    id         INTEGER PRIMARY KEY,
    parent_id  INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('root','chat','type','user')),
    chat_id    INTEGER REFERENCES chats(id) ON DELETE SET NULL,
    media_type TEXT CHECK (media_type IN ('photo','video','post','reel') OR media_type IS NULL),
    color      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_folders_parent_name ON folders(parent_id, name COLLATE NOCASE);
CREATE UNIQUE INDEX IF NOT EXISTS ux_folders_single_root ON folders(role) WHERE role = 'root';
CREATE INDEX IF NOT EXISTS ix_folders_parent ON folders(parent_id);
CREATE INDEX IF NOT EXISTS ix_folders_type_lookup ON folders(parent_id, role, media_type);

CREATE TABLE IF NOT EXISTS chats (
    id             INTEGER PRIMARY KEY,
    source         TEXT NOT NULL CHECK (source IN ('live','export')),
    external_id    TEXT NOT NULL,
    title          TEXT NOT NULL,
    is_group       INTEGER NOT NULL DEFAULT 1,
    people         INTEGER NOT NULL DEFAULT 0,
    selected       INTEGER NOT NULL DEFAULT 0,
    auto_sync      INTEGER NOT NULL DEFAULT 0,
    folder_id      INTEGER REFERENCES folders(id) ON DELETE SET NULL,
    split_by_type  INTEGER NOT NULL DEFAULT 1,
    types          TEXT NOT NULL DEFAULT 'photo,video,post,reel',
    message_limit  INTEGER,
    export_dir     TEXT,
    last_activity  TEXT,
    last_synced_at TEXT,
    last_result    TEXT,
    created_at     TEXT NOT NULL,
    UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS ix_chats_selected ON chats(selected);
CREATE INDEX IF NOT EXISTS ix_chats_auto ON chats(auto_sync) WHERE auto_sync = 1;
CREATE INDEX IF NOT EXISTS ix_chats_title ON chats(title COLLATE NOCASE);

-- The download index (replaces index.csv / .downloaded.json): one row per thing found in a chat.
CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY,
    chat_id    INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    item_key   TEXT NOT NULL,
    kind       TEXT NOT NULL,
    sender     TEXT,
    sent_at    TEXT,
    permalink  TEXT,
    note       TEXT,
    status     TEXT NOT NULL CHECK (status IN ('done','failed','listed')),
    error      TEXT,
    attempts   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (chat_id, item_key)
);
CREATE INDEX IF NOT EXISTS ix_items_chat_status ON items(chat_id, status);
CREATE INDEX IF NOT EXISTS ix_items_sent_at ON items(sent_at);

CREATE TABLE IF NOT EXISTS files (
    id           INTEGER PRIMARY KEY,
    folder_id    INTEGER REFERENCES folders(id) ON DELETE SET NULL,  -- NULL only while in the trash
    name         TEXT NOT NULL,
    media_type   TEXT NOT NULL CHECK (media_type IN ('photo','video','post','reel','other')),
    mime         TEXT,
    size         INTEGER NOT NULL DEFAULT 0,
    sha256       TEXT,
    item_id      INTEGER REFERENCES items(id) ON DELETE SET NULL,
    chat_id      INTEGER REFERENCES chats(id) ON DELETE SET NULL,
    sender       TEXT,
    sent_at      TEXT,
    permalink    TEXT,
    favorite     INTEGER NOT NULL DEFAULT 0,
    trashed_at   TEXT,
    trash_path   TEXT,
    trash_origin TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
-- Names are unique per folder among live (not trashed) files.
CREATE UNIQUE INDEX IF NOT EXISTS ux_files_folder_name ON files(folder_id, name COLLATE NOCASE) WHERE trashed_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_files_folder_live ON files(folder_id, trashed_at, created_at);
CREATE INDEX IF NOT EXISTS ix_files_type_live ON files(media_type, trashed_at);
CREATE INDEX IF NOT EXISTS ix_files_chat ON files(chat_id, trashed_at);
CREATE INDEX IF NOT EXISTS ix_files_sent_at ON files(sent_at);
CREATE INDEX IF NOT EXISTS ix_files_created ON files(created_at);
CREATE INDEX IF NOT EXISTS ix_files_sha ON files(sha256) WHERE sha256 IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_files_favorite ON files(favorite) WHERE favorite = 1;
CREATE INDEX IF NOT EXISTS ix_files_trash ON files(trashed_at) WHERE trashed_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_files_sender ON files(sender COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS ix_files_item ON files(item_id);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('queued','running','done','failed','cancelled','interrupted')),
    params      TEXT NOT NULL DEFAULT '{}',
    stats       TEXT NOT NULL DEFAULT '{}',
    progress    INTEGER NOT NULL DEFAULT 0,
    total       INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS ix_jobs_created ON jobs(created_at DESC);

CREATE INDEX IF NOT EXISTS ix_jobs_queue ON jobs(status, id);

-- State shared between processes (web workers + the background engine).
CREATE TABLE IF NOT EXISTS runtime (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
) WITHOUT ROWID;

-- Failed dashboard sign-ins, shared by all web workers (brute-force protection).
CREATE TABLE IF NOT EXISTS auth_failures (
    id INTEGER PRIMARY KEY,
    ip TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_auth_failures_ip_ts ON auth_failures(ip, ts);

CREATE TABLE IF NOT EXISTS job_logs (
    id      INTEGER PRIMARY KEY,
    job_id  INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    ts      TEXT NOT NULL,
    level   TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_job_logs_job ON job_logs(job_id, id);
"""

# Full-text search over file names, senders and chat titles (trigram = substring matching).
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(name, sender, chat, tokenize = 'trigram');

CREATE TRIGGER IF NOT EXISTS trg_files_ai AFTER INSERT ON files BEGIN
    INSERT INTO files_fts(rowid, name, sender, chat)
    VALUES (new.id, new.name, coalesce(new.sender, ''),
            coalesce((SELECT title FROM chats WHERE id = new.chat_id), ''));
END;
CREATE TRIGGER IF NOT EXISTS trg_files_au AFTER UPDATE OF name, sender, chat_id ON files BEGIN
    DELETE FROM files_fts WHERE rowid = old.id;
    INSERT INTO files_fts(rowid, name, sender, chat)
    VALUES (new.id, new.name, coalesce(new.sender, ''),
            coalesce((SELECT title FROM chats WHERE id = new.chat_id), ''));
END;
CREATE TRIGGER IF NOT EXISTS trg_files_ad AFTER DELETE ON files BEGIN
    DELETE FROM files_fts WHERE rowid = old.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_chats_title AFTER UPDATE OF title ON chats BEGIN
    UPDATE files_fts SET chat = new.title WHERE rowid IN (SELECT id FROM files WHERE chat_id = new.id);
END;
"""

_fts_available: Optional[bool] = None
_init_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    # One connection per request/task, never shared concurrently; async endpoints may
    # receive it from FastAPI's threadpool, hence check_same_thread=False.
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, roll back on error."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def db_dep() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency."""
    with get_db() as conn:
        yield conn


def init_db() -> None:
    global _fts_available
    with _init_lock:
        conn = connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            _migrate(conn)
            conn.executescript(SCHEMA)
            try:
                conn.executescript(FTS_SCHEMA)
                _fts_available = True
            except sqlite3.OperationalError:
                _fts_available = False  # very old SQLite: search falls back to LIKE
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("ANALYZE")
            conn.commit()
        finally:
            conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring databases created by older versions up to date (before SCHEMA runs)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    if cols and "cancel_requested" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0")


def fts_available() -> bool:
    return bool(_fts_available)


def optimize() -> None:
    """Keep the query planner statistics fresh (cheap; run occasionally)."""
    with get_db() as conn:
        conn.execute("PRAGMA optimize")


# --------------------------------------------------------------------------- #
# Settings (JSON values) and tiny row helpers
# --------------------------------------------------------------------------- #
def get_setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return default


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


# --------------------------------------------------------------------------- #
# Runtime state shared by every process (each call uses its own short transaction)
# --------------------------------------------------------------------------- #
def rt_get(key: str, default: Any = None) -> Any:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM runtime WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return default


def rt_set(key: str, value: Any) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO runtime(key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(value), now_iso()),
        )


def rt_del(key: str) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM runtime WHERE key = ?", (key,))


def row_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(row) if row is not None else None


def rows_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]
