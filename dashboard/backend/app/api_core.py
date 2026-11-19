"""API: dashboard sign-in, Instagram account, chats, exports, jobs, settings and stats."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel

from . import library, security, settings as settings_mod
from .config import EXPORTS_DIR, LIBRARY_DIR, SESSION_FILE
from .db import db_dep, rows_dicts
from .instagram import ig, normalize_sessionid, sessionid_looks_valid
from .jobs import runner

public = APIRouter(prefix="/api")
router = APIRouter(prefix="/api", dependencies=[Depends(security.require_auth)])


# --------------------------------------------------------------------------- #
# Dashboard sign-in
# --------------------------------------------------------------------------- #
class Passcode(BaseModel):
    passcode: str


class ChangePasscode(BaseModel):
    current: str
    new: str


def _set_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(security.COOKIE_NAME, token, max_age=security.SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=security.is_https(request), path="/")


@public.get("/health")
def health() -> dict:
    return {"ok": True}


@public.get("/auth/state")
def auth_state(request: Request, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    return {"setup_needed": not security.passcode_is_set(conn),
            "authenticated": security.session_valid(conn, request.cookies.get(security.COOKIE_NAME))}


@public.post("/auth/setup")
def auth_setup(body: Passcode, request: Request, response: Response,
               conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    if security.passcode_is_set(conn):
        raise HTTPException(409, "A passcode is already set. Sign in instead.")
    security.set_passcode(conn, body.passcode)
    _set_cookie(request, response, security.create_session(conn))
    return {"ok": True}


@public.post("/auth/login")
def auth_login(body: Passcode, request: Request, response: Response,
               conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    ip = request.client.host if request.client else "?"
    security.check_rate(ip)
    from .db import get_setting
    stored = get_setting(conn, "auth_passcode")
    if not stored or not security.verify_passcode(body.passcode, stored):
        security.record_failure(ip)
        raise HTTPException(401, "That passcode isn't right.")
    security.clear_failures(ip)
    _set_cookie(request, response, security.create_session(conn))
    return {"ok": True}


@public.post("/auth/logout")
def auth_logout(request: Request, response: Response, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    security.drop_session(conn, request.cookies.get(security.COOKIE_NAME))
    response.delete_cookie(security.COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/auth/change")
def auth_change(body: ChangePasscode, request: Request, response: Response,
                conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    from .db import get_setting
    if not security.verify_passcode(body.current, get_setting(conn, "auth_passcode") or ""):
        raise HTTPException(400, "The current passcode isn't right.")
    security.set_passcode(conn, body.new)
    security.drop_all_sessions(conn)  # sign out every other browser
    _set_cookie(request, response, security.create_session(conn))
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Instagram account
# --------------------------------------------------------------------------- #
class AccountUpdate(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    sessionid: Optional[str] = None
    login_method: Optional[Literal["auto", "password", "sessionid"]] = None
    clear_password: bool = False
    clear_sessionid: bool = False
    connect: bool = False


def _account(conn: sqlite3.Connection) -> dict:
    s = settings_mod.load(conn)
    return {
        "username": s["ig_username"],
        "login_method": s["login_method"],
        "has_password": security.secret_info(conn, "ig_password") is not None,
        "password_updated": security.secret_info(conn, "ig_password"),
        "has_sessionid": security.secret_info(conn, "ig_sessionid") is not None,
        "sessionid_updated": security.secret_info(conn, "ig_sessionid"),
        "status": ig.status(),
    }


@router.get("/account")
def get_account(conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    return _account(conn)


@router.put("/account")
def update_account(body: AccountUpdate, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    changes = {}
    if body.username is not None:
        changes["ig_username"] = body.username
    if body.login_method is not None:
        changes["login_method"] = body.login_method
    username_changed = body.username is not None and \
        body.username.strip().lstrip("@").casefold() != (settings_mod.get(conn, "ig_username") or "").casefold()
    if changes:
        settings_mod.update(conn, changes)
    if body.password:
        security.put_secret(conn, "ig_password", body.password)
    if body.clear_password:
        security.put_secret(conn, "ig_password", None)
    if body.sessionid:
        sid = normalize_sessionid(body.sessionid)
        if not sessionid_looks_valid(sid):
            raise HTTPException(400, "That doesn't look like a sessionid. It should start with numbers, "
                                     "like 1234567890%3AAbCd... or 1234567890:AbCd...")
        security.put_secret(conn, "ig_sessionid", sid)
    if body.clear_sessionid:
        security.put_secret(conn, "ig_sessionid", None)
    result = _account(conn)
    if body.connect:
        conn.commit()
        # New credentials or another account: throw away the old saved login.
        force = bool(body.password or body.sessionid or username_changed)
        result["job_id"] = runner.enqueue("connect", "Connect to Instagram", {"force": force}, dedupe=False)
    return result


class ConnectBody(BaseModel):
    force: bool = False


@router.post("/account/connect")
def connect(body: ConnectBody) -> dict:
    title = "Reconnect to Instagram (fresh login)" if body.force else "Connect to Instagram"
    return {"job_id": runner.enqueue("connect", title, {"force": body.force})}


class LogoutBody(BaseModel):
    forget: bool = False


@router.post("/account/logout")
def logout(body: LogoutBody) -> dict:
    ig.logout(forget_secrets=body.forget)
    return {"ok": True}


class CodeBody(BaseModel):
    code: str


@router.post("/account/code")
def submit_code(body: CodeBody) -> dict:
    if not ig.submit_code(body.code):
        raise HTTPException(409, "Instagram isn't waiting for a code right now.")
    return {"ok": True}


@router.post("/account/code/cancel")
def cancel_code() -> dict:
    ig.cancel_code()
    return {"ok": True}


@router.post("/account/resume")
def resume() -> dict:
    ig.resume()
    runner.poke_scheduler()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Chats
# --------------------------------------------------------------------------- #
def _chat_rows(conn: sqlite3.Connection, where: str = "1", args: tuple = ()) -> list[dict]:
    rows = rows_dicts(conn.execute(
        f"""SELECT c.*,
                   (SELECT count(*) FROM files f WHERE f.chat_id = c.id AND f.trashed_at IS NULL) AS file_count,
                   (SELECT count(*) FROM items i WHERE i.chat_id = c.id AND i.status = 'failed') AS failed_count,
                   (SELECT count(*) FROM items i WHERE i.chat_id = c.id AND i.status = 'listed') AS listed_count
            FROM chats c WHERE {where}
            ORDER BY c.selected DESC, coalesce(c.last_activity, c.created_at) DESC""", args).fetchall())
    for r in rows:
        r["types"] = [t for t in (r["types"] or "").split(",") if t]
        r["last_result"] = json.loads(r["last_result"]) if r["last_result"] else None
        r["folder_label"] = None
        if r["folder_id"] and conn.execute("SELECT 1 FROM folders WHERE id = ?", (r["folder_id"],)).fetchone():
            r["folder_label"] = " / ".join(b["name"] for b in library.breadcrumbs(conn, r["folder_id"]))
    return rows


@router.get("/chats")
def list_chats(conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    include_private = settings_mod.get(conn, "include_private")
    where = "1" if include_private else "(c.is_group = 1 OR c.selected = 1)"
    return {"chats": _chat_rows(conn, where), "include_private": include_private}


class ChatUpdate(BaseModel):
    selected: Optional[bool] = None
    auto_sync: Optional[bool] = None
    folder_id: Optional[int] = None
    split_by_type: Optional[bool] = None
    types: Optional[list[str]] = None
    message_limit: Optional[int] = None
    title: Optional[str] = None


def _apply_chat_update(conn: sqlite3.Connection, chat_id: int, body: ChatUpdate) -> None:
    chat = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
    if chat is None:
        raise HTTPException(404, "That chat isn't in the list anymore.")
    sets, args = [], []
    if body.selected is not None:
        sets.append("selected = ?"); args.append(int(body.selected))
        if not body.selected:
            sets.append("auto_sync = 0")
    if body.auto_sync is not None:
        if body.auto_sync and chat["source"] != "live":
            raise HTTPException(400, "Automatic checks only work for live chats, not exports.")
        sets.append("auto_sync = ?"); args.append(int(body.auto_sync))
        if body.auto_sync:
            sets.append("selected = 1")
    if body.folder_id is not None:
        library.get_folder(conn, body.folder_id)
        sets.append("folder_id = ?"); args.append(body.folder_id)
    if body.split_by_type is not None:
        sets.append("split_by_type = ?"); args.append(int(body.split_by_type))
    if body.types is not None:
        types = [t for t in library.KINDS if t in body.types]
        if not types:
            raise HTTPException(400, "Pick at least one type to download.")
        sets.append("types = ?"); args.append(",".join(types))
    if body.message_limit is not None:
        if not 0 <= body.message_limit <= 20000:
            raise HTTPException(400, "Messages to read must be between 1 and 20000 (0 = use the default).")
        sets.append("message_limit = ?"); args.append(body.message_limit or None)
    if body.title:
        sets.append("title = ?"); args.append(body.title.strip()[:120])
    if sets:
        conn.execute(f"UPDATE chats SET {', '.join(sets)} WHERE id = ?", (*args, chat_id))


@router.patch("/chats/{chat_id}")
def update_chat(chat_id: int, body: ChatUpdate, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    _apply_chat_update(conn, chat_id, body)
    runner.poke_scheduler(conn)
    return _chat_rows(conn, "c.id = ?", (chat_id,))[0]


class ChatBulk(ChatUpdate):
    ids: list[int]


@router.post("/chats/bulk")
def bulk_update(body: ChatBulk, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    for cid in body.ids:
        _apply_chat_update(conn, cid, body)
    runner.poke_scheduler(conn)
    return {"ok": True}


@router.post("/chats/{chat_id}/folder/new")
def new_chat_folder(chat_id: int, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    """Give a chat its own fresh folder (useful after pointing it somewhere else)."""
    conn.execute("UPDATE chats SET folder_id = NULL WHERE id = ?", (chat_id,))
    chat = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
    return {"folder_id": library.chat_home(conn, chat)}


@router.delete("/chats/{chat_id}")
def forget_chat(chat_id: int, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    return {"ok": True}


@router.post("/chats/{chat_id}/reset")
def reset_chat_history(chat_id: int, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    """Forget what was downloaded so the next sync fetches everything again."""
    cur = conn.execute("DELETE FROM items WHERE chat_id = ?", (chat_id,))
    return {"forgotten": cur.rowcount}


@router.post("/chats/refresh")
def refresh_chats() -> dict:
    return {"job_id": runner.enqueue("refresh_chats", "Load chats from Instagram")}


class SyncBody(BaseModel):
    chat_ids: Optional[list[int]] = None
    mode: Literal["recent", "all", "watch"] = "recent"
    dry_run: bool = False
    limit: Optional[int] = None


@router.post("/sync")
def sync(body: SyncBody, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    ids = body.chat_ids or [r["id"] for r in conn.execute("SELECT id FROM chats WHERE selected = 1")]
    if not ids:
        raise HTTPException(400, "No chats are switched on yet. Go to Chats and switch on the ones you want.")
    if body.limit is not None and not 1 <= body.limit <= 20000:
        raise HTTPException(400, "Messages to read must be between 1 and 20000.")
    names = [r["title"] for r in conn.execute(
        f"SELECT title FROM chats WHERE id IN ({','.join('?' * len(ids))})", ids)]
    what = names[0] if len(names) == 1 else f"{len(names)} chats"
    verb = "Preview" if body.dry_run else ("Download full history of" if body.mode == "all" else "Sync")
    job = runner.enqueue("sync", f"{verb} {what}", {"chat_ids": sorted(ids), "mode": body.mode,
                                                    "dry_run": body.dry_run, "limit": body.limit})
    return {"job_id": job}


# --------------------------------------------------------------------------- #
# Instagram data exports
# --------------------------------------------------------------------------- #
class ExportPath(BaseModel):
    path: str


@router.post("/exports/scan")
def export_scan(body: ExportPath) -> dict:
    p = Path(body.path.strip().strip('"')).expanduser()
    if not p.exists():
        raise HTTPException(404, "That folder doesn't exist on the computer running this app.")
    return {"job_id": runner.enqueue("export_scan", f"Read Instagram export {p.name}", {"path": str(p)})}


@router.post("/exports/upload")
async def export_upload(file: UploadFile = File(...)) -> dict:
    name = library.safe_file_name(Path(file.filename or "export.zip").name)
    if not name.lower().endswith(".zip"):
        raise HTTPException(400, "Upload the .zip file you downloaded from Instagram.")
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    dest = EXPORTS_DIR / name
    with open(dest, "wb") as fh:
        while chunk := await file.read(1 << 20):
            fh.write(chunk)
    return {"job_id": runner.enqueue("export_scan", f"Read Instagram export {name}", {"path": str(dest)},
                                     dedupe=False)}


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #
def _job(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["params"] = json.loads(d["params"] or "{}")
    d["stats"] = json.loads(d["stats"] or "{}")
    return d


@router.get("/jobs")
def list_jobs(limit: int = 40, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    rows = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (min(limit, 200),)).fetchall()
    return {"jobs": [_job(r) for r in rows]}


@router.get("/jobs/{job_id}")
def get_job(job_id: int, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No such task.")
    return _job(row)


@router.get("/jobs/{job_id}/logs")
def job_logs(job_id: int, after: int = 0, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    rows = conn.execute("SELECT id, ts, level, message FROM job_logs WHERE job_id = ? AND id > ? ORDER BY id LIMIT 500",
                        (job_id, after)).fetchall()
    return {"logs": rows_dicts(rows)}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int) -> dict:
    if not runner.cancel(job_id):
        raise HTTPException(409, "That task already finished.")
    ig.cancel_code()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Settings, status and stats
# --------------------------------------------------------------------------- #
@router.get("/settings")
def get_settings(conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    return {"settings": settings_mod.load(conn), "library_path": str(LIBRARY_DIR)}


@router.put("/settings")
def put_settings(changes: dict, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    result = settings_mod.update(conn, changes)
    runner.poke_scheduler(conn)
    return {"settings": result, "library_path": str(LIBRARY_DIR)}


@router.get("/status")
def status(conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    s = settings_mod.load(conn)
    active = conn.execute("SELECT * FROM jobs WHERE status IN ('running', 'queued') ORDER BY id").fetchall()
    counts = conn.execute(
        """SELECT count(*) AS files, coalesce(sum(size), 0) AS bytes,
                  sum(media_type = 'photo') AS photos, sum(media_type = 'video') AS videos,
                  sum(media_type = 'post') AS posts, sum(media_type = 'reel') AS reels
           FROM files WHERE trashed_at IS NULL""").fetchone()
    chats = conn.execute("SELECT count(*) AS total, coalesce(sum(selected), 0) AS selected, "
                         "coalesce(sum(auto_sync), 0) AS auto FROM chats").fetchone()
    trash = conn.execute("SELECT count(*) FROM files WHERE trashed_at IS NOT NULL").fetchone()[0]
    last = conn.execute("SELECT * FROM jobs WHERE kind = 'sync' AND status != 'queued' "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    disk = shutil.disk_usage(LIBRARY_DIR)
    return {
        "instagram": ig.status(),
        "has_credentials": bool(s["ig_username"] and security.secret_info(conn, "ig_password"))
                           or security.secret_info(conn, "ig_sessionid") is not None,
        "session_saved": SESSION_FILE.exists(),
        "auto_sync": s["auto_sync"],
        "auto_interval_min": s["auto_interval_min"],
        "next_auto_run": runner.next_auto_run(),
        "engine": runner.engine_info(),
        "server": {
            "pid": os.getpid(),
            "mode": "multi-worker" if os.environ.get("IGCM_ROLE") == "web" else "single process",
            "web_workers": int(os.environ.get("WEB_WORKERS", "1") or 1),
            "max_requests": int(os.environ.get("MAX_REQUESTS", "0") or 0),
            "max_requests_jitter": int(os.environ.get("MAX_REQUESTS_JITTER", "0") or 0),
            "engine_max_jobs": int(os.environ.get("ENGINE_MAX_JOBS", "0") or 0),
        },
        "active_jobs": [_job(r) for r in active],
        "last_sync": _job(last) if last else None,
        "library": dict(counts),
        "chats": dict(chats),
        "trash": trash,
        "disk": {"free": disk.free, "total": disk.total},
    }


@router.get("/stats")
def stats(conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    by_chat = rows_dicts(conn.execute(
        """SELECT c.id, c.title, count(f.id) AS files, coalesce(sum(f.size), 0) AS bytes
           FROM chats c JOIN files f ON f.chat_id = c.id AND f.trashed_at IS NULL
           GROUP BY c.id ORDER BY files DESC LIMIT 12"""))
    senders = rows_dicts(conn.execute(
        """SELECT sender, count(*) AS files FROM files
           WHERE trashed_at IS NULL AND sender IS NOT NULL AND sender != ''
           GROUP BY sender COLLATE NOCASE ORDER BY files DESC LIMIT 8"""))
    months = rows_dicts(conn.execute(
        """SELECT substr(coalesce(sent_at, created_at), 1, 7) AS month, count(*) AS files,
                  sum(media_type = 'photo') AS photo, sum(media_type = 'video') AS video,
                  sum(media_type = 'post') AS post, sum(media_type = 'reel') AS reel
           FROM files WHERE trashed_at IS NULL
           GROUP BY month ORDER BY month DESC LIMIT 12"""))
    dupes = conn.execute(
        """SELECT count(*) AS groups, coalesce(sum(extra), 0) AS extra_files, coalesce(sum(waste), 0) AS bytes FROM (
               SELECT count(*) - 1 AS extra, (count(*) - 1) * max(size) AS waste FROM files
               WHERE trashed_at IS NULL AND sha256 IS NOT NULL GROUP BY sha256 HAVING count(*) > 1)""").fetchone()
    recent = rows_dicts(conn.execute(
        """SELECT f.id, f.name, f.media_type, f.mime, f.sender, f.sent_at, f.created_at, f.folder_id,
                  c.title AS chat_title
           FROM files f LEFT JOIN chats c ON c.id = f.chat_id
           WHERE f.trashed_at IS NULL AND f.media_type != 'other'
           ORDER BY f.created_at DESC LIMIT 18"""))
    on_this_day = rows_dicts(conn.execute(
        """SELECT f.id, f.name, f.media_type, f.mime, f.sender, f.sent_at, c.title AS chat_title
           FROM files f LEFT JOIN chats c ON c.id = f.chat_id
           WHERE f.trashed_at IS NULL AND f.sent_at IS NOT NULL AND f.media_type != 'other'
             AND substr(f.sent_at, 6, 5) = strftime('%m-%d', 'now')
             AND substr(f.sent_at, 1, 4) < strftime('%Y', 'now')
           ORDER BY f.sent_at DESC LIMIT 12"""))
    return {"by_chat": by_chat, "senders": senders, "months": list(reversed(months)),
            "duplicates": dict(dupes), "recent": recent, "on_this_day": on_this_day}
