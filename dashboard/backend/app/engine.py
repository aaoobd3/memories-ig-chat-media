"""Job handlers: connect, load chats, sync (live + export), imports and housekeeping.

Detection and downloading reuse the original CLI (ig_chat_media.py); results are
written to the SQLite index and filed into the library folder tree.
"""
from __future__ import annotations

import shutil
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import ig_chat_media as core

from . import library, settings as settings_mod
from .config import EXPORTS_DIR, STAGING_DIR
from .db import get_db, now_iso, optimize
from .instagram import RATE_LIMIT_ERRORS, explain, ig
from .jobs import JobCancelled, JobContext, handler

MAX_ATTEMPTS = 5


class StageStore:
    """Duck-typed stand-in for the CLI's Store: downloads land in a staging folder first."""

    dry_run = False

    def __init__(self, directory: Path):
        self.dir = directory

    def folder(self, kind: str) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        return self.dir

    def base_name(self, f, ident: str) -> str:
        return f"{f.when.astimezone():%Y-%m-%d_%H%M%S}_{core.safe_name(f.sender, 30)}_{core.safe_name(ident, 40)}"


def _since(value: str) -> Optional[datetime]:
    return datetime.strptime(value, "%Y-%m-%d").astimezone() if value else None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Account
# --------------------------------------------------------------------------- #
@handler("connect")
def job_connect(ctx: JobContext, params: dict) -> dict:
    ig.connect(ctx.log, force=bool(params.get("force")))
    ctx.success(f"Connected to Instagram as @{ig.username}.")
    return {"username": ig.username}


@handler("refresh_chats")
def job_refresh_chats(ctx: JobContext, params: dict) -> dict:
    cl = ig.client(ctx.log)
    with get_db() as conn:
        s = settings_mod.load(conn)
    ctx.log(f"Loading your latest {s['search_threads']} chats from Instagram...")
    try:
        threads = cl.direct_threads(amount=s["search_threads"], thread_message_limit=1)
    except Exception as e:
        ig.mark_failure(e)
        raise
    added = 0
    with get_db() as conn:
        for t in threads:
            last = getattr(t, "last_activity_at", None)
            cur = conn.execute(
                """INSERT INTO chats(source, external_id, title, is_group, people, types, split_by_type,
                                     last_activity, created_at)
                   VALUES ('live', ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source, external_id) DO UPDATE SET
                       title = excluded.title, is_group = excluded.is_group, people = excluded.people,
                       last_activity = excluded.last_activity""",
                (str(t.id), core.thread_label(t), int(bool(t.is_group)), len(t.users) + 1,
                 ",".join(s["default_types"]), int(s["split_by_type"]), _iso(last) if last else None, now_iso()))
            added += 1 if cur.rowcount and cur.lastrowid else 0
    groups = sum(1 for t in threads if t.is_group)
    ctx.success(f"Found {len(threads)} chats ({groups} group chats).")
    return {"chats": len(threads), "groups": groups}


# --------------------------------------------------------------------------- #
# Sync
# --------------------------------------------------------------------------- #
@handler("sync")
def job_sync(ctx: JobContext, params: dict) -> dict:
    mode = params.get("mode", "recent")          # recent | all | watch
    dry = bool(params.get("dry_run"))
    limit = params.get("limit")
    with get_db() as conn:
        s = settings_mod.load(conn)
        ids = [int(i) for i in params.get("chat_ids") or []]
        marks = ",".join("?" * len(ids)) or "NULL"
        chats = conn.execute(f"SELECT * FROM chats WHERE id IN ({marks}) ORDER BY title COLLATE NOCASE",
                             ids).fetchall()
    if not chats:
        ctx.warn("No chats chosen. Pick chats on the Chats page first.")
        return {}

    totals = {"chats": len(chats), "found": 0, "new": 0, "files": 0, "skipped": 0, "failed": 0, "listed": 0}
    preview: list[dict] = []
    ctx.set_total(len(chats))
    if dry:
        ctx.log("Preview only: nothing will be downloaded.")

    cl = None
    needs_live = any(c["source"] == "live" for c in chats)
    wants_shared = s["download_shared_from_export"] and not dry and any(c["source"] == "export" for c in chats)
    if needs_live or wants_shared:
        try:
            cl = ig.client(ctx.log)
        except Exception as e:
            if needs_live:
                raise
            ctx.warn(f"Not signed in to Instagram ({explain(e)}); shared posts/reels from exports will only be listed.")

    for chat in chats:
        ctx.check()
        try:
            if chat["source"] == "live":
                res = _sync_live(ctx, cl, chat, s, mode, limit, dry, preview)
            else:
                res = _sync_export(ctx, cl, chat, s, dry, preview)
            for k in totals:
                if k in res:
                    totals[k] += res[k]
            summary = (f"{res.get('new', 0)} new, {res.get('skipped', 0)} already had, "
                       f"{res.get('failed', 0)} failed") if not dry else f"{res.get('new', 0)} would be downloaded"
            _save_result(chat["id"], {"ok": True, "summary": summary, **{k: res.get(k, 0) for k in totals}}, dry)
            ctx.success(f"“{chat['title']}”: {summary}.")
        except JobCancelled:
            raise
        except Exception as e:  # noqa: BLE001
            ig.mark_failure(e)
            msg = explain(e)
            _save_result(chat["id"], {"ok": False, "summary": msg}, dry)
            ctx.error(f"“{chat['title']}”: {msg}")
            totals["failed"] += 1
            if type(e).__name__ in (*RATE_LIMIT_ERRORS, "LoginRequired", "ChallengeRequired"):
                ctx.warn("Stopping here to keep your account safe.")
                break
        ctx.step()

    if dry:
        ctx.stats["preview"] = preview[:500]
    word = "would be downloaded" if dry else "new files"
    count = totals["new"] if dry else totals["files"]
    ctx.success(f"All done: {count} {word}.")
    return totals


def _save_result(chat_id: int, result: dict, dry: bool) -> None:
    import json
    with get_db() as conn:
        if dry:
            conn.execute("UPDATE chats SET last_result = ? WHERE id = ?", (json.dumps({**result, "preview": True}), chat_id))
        else:
            conn.execute("UPDATE chats SET last_result = ?, last_synced_at = ? WHERE id = ?",
                         (json.dumps(result), now_iso(), chat_id))


def _sync_live(ctx: JobContext, cl, chat: sqlite3.Row, s: dict, mode: str, limit, dry: bool,
               preview: list) -> dict:
    if mode == "all":
        amount = 0
    elif limit:
        amount = int(limit)
    elif mode == "watch":
        amount = s["watch_batch"]
    else:
        amount = chat["message_limit"] or s["message_limit"]
    ctx.log(f"“{chat['title']}”: reading {'the whole history' if amount == 0 else f'the last {amount} messages'}...")
    thread = cl.direct_thread(chat["external_id"], amount=amount)
    title = core.thread_label(thread)
    if title and title != chat["title"]:
        with get_db() as conn:
            conn.execute("UPDATE chats SET title = ? WHERE id = ?", (title, chat["id"]))
    usernames = {str(u.pk): u.username for u in thread.users}
    usernames[str(cl.user_id)] = cl.username or "me"
    since = _since(s["since"])
    found = []
    for msg in sorted(reversed(thread.messages), key=lambda m: core.as_aware(m.timestamp)):
        if since and core.as_aware(msg.timestamp) < since:
            continue
        found.extend(core.detect_live(msg, usernames))
    ctx.log(f"Checked {len(thread.messages)} messages; {len(found)} photos/videos/posts/reels in them.")
    return _process(ctx, cl, chat["id"], found, dry, fetch_links=True, preview=preview)


def _sync_export(ctx: JobContext, cl, chat: sqlite3.Row, s: dict, dry: bool, preview: list) -> dict:
    root = Path(chat["export_dir"] or "")
    if not root.is_dir():
        raise FileNotFoundError(f"the export folder {root} is gone")
    match = next((c for c in core.export_chats(root) if str(c.folder.resolve()) == chat["external_id"]), None)
    if match is None:
        raise FileNotFoundError("this chat is no longer in the export folder")
    since = _since(s["since"])
    ctx.log(f"“{chat['title']}”: reading the export...")
    found = [f for f in core.detect_export(match, root) if not since or f.when >= since]
    fetch = bool(s["download_shared_from_export"] and cl is not None)
    return _process(ctx, cl, chat["id"], found, dry, fetch_links=fetch, preview=preview)


def _process(ctx: JobContext, cl, chat_id: int, found: list, dry: bool, fetch_links: bool,
             preview: list) -> dict:
    res = {"found": 0, "new": 0, "files": 0, "skipped": 0, "failed": 0, "listed": 0}
    with get_db() as conn:
        chat = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
        types = set((chat["types"] or "").split(","))
        prior = {r["item_key"]: (r["status"], r["attempts"]) for r in conn.execute(
            "SELECT item_key, status, attempts FROM items WHERE chat_id = ?", (chat_id,))}
    stage = STAGING_DIR / f"job{ctx.job_id}_{chat_id}"
    store = StageStore(stage)
    try:
        for f in found:
            ctx.check()
            if f.kind not in types:
                continue
            res["found"] += 1
            status, attempts = prior.get(f.key, (None, 0))
            if status == "done":
                res["skipped"] += 1
                continue
            link_only = not (f.local_path or f.url or f.media is not None)
            if link_only and not fetch_links:
                res["listed"] += 1
                if not dry and status != "listed":
                    with get_db() as conn:
                        _upsert_item(conn, chat_id, f, "listed")
                continue
            if attempts >= MAX_ATTEMPTS:
                res["skipped"] += 1
                continue
            if dry:
                res["new"] += 1
                preview.append({"chat": chat["title"], "when": _iso(f.when), "sender": f.sender,
                                "kind": f.kind, "link": f.permalink})
                continue
            label = f"{f.kind} from {f.sender} ({f.when.astimezone():%d %b %Y %H:%M})"
            try:
                saved = _download(cl, f, store)
                with get_db() as conn:
                    item_id = _upsert_item(conn, chat_id, f, "done")
                    fresh = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
                    folder = library.target_folder_for(conn, fresh, f.kind)
                    for p in saved:
                        library.register_file(conn, p, folder, name=p.name, media_type=f.kind, meta={
                            "item_id": item_id, "chat_id": chat_id, "sender": f.sender,
                            "sent_at": _iso(f.when), "permalink": f.permalink})
                res["new"] += 1
                res["files"] += len(saved)
                ctx.bump("files", len(saved))
                ctx.log(f"Saved {label}", "success")
            except JobCancelled:
                raise
            except Exception as e:  # noqa: BLE001
                res["failed"] += 1
                with get_db() as conn:
                    _upsert_item(conn, chat_id, f, "failed", error=explain(e))
                ctx.error(f"Couldn't save {label}: {explain(e)}")
                if type(e).__name__ in (*RATE_LIMIT_ERRORS, "LoginRequired", "ChallengeRequired"):
                    raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return res


def _download(cl, f, store: StageStore) -> list[Path]:
    if f.local_path:
        dest = store.folder(f.kind) / (store.base_name(f, f.local_path.stem) + f.local_path.suffix)
        shutil.copy2(f.local_path, dest)
        return [dest]
    if f.url:
        return [core.http_download(f.url, store.folder(f.kind) / store.base_name(f, f.key.split(":")[0][-12:]))]
    if cl is None:
        raise RuntimeError("needs an Instagram login")
    media = f.media if f.media is not None else core.fetch_by_permalink(cl, f.permalink)
    return core.download_media_object(cl, media, store, f)


def _upsert_item(conn: sqlite3.Connection, chat_id: int, f, status: str, error: Optional[str] = None) -> int:
    ts = now_iso()
    row = conn.execute(
        """INSERT INTO items(chat_id, item_key, kind, sender, sent_at, permalink, note, status, error,
                             attempts, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(chat_id, item_key) DO UPDATE SET
               kind = excluded.kind, permalink = coalesce(excluded.permalink, items.permalink),
               note = excluded.note, status = excluded.status, error = excluded.error,
               attempts = items.attempts + excluded.attempts, updated_at = excluded.updated_at
           RETURNING id""",
        (chat_id, f.key, f.kind, f.sender, _iso(f.when), f.permalink, f.note, status, error,
         1 if status == "failed" else 0, ts, ts)).fetchone()
    return row["id"]


# --------------------------------------------------------------------------- #
# Instagram data export ("Download your information")
# --------------------------------------------------------------------------- #
@handler("export_scan")
def job_export_scan(ctx: JobContext, params: dict) -> dict:
    root = Path(params["path"]).expanduser()
    if root.is_file() and root.suffix.lower() == ".zip":
        root = _unzip(ctx, root)
    if not root.is_dir():
        raise FileNotFoundError(f"folder not found: {root}")
    ctx.log(f"Looking for chats in {root} ...")
    chats = core.export_chats(root)
    if not chats:
        raise FileNotFoundError("no chats in this export. Request it in JSON format with “Messages” included")
    with get_db() as conn:
        s = settings_mod.load(conn)
        for c in chats:
            conn.execute(
                """INSERT INTO chats(source, external_id, title, is_group, people, types, split_by_type,
                                     export_dir, created_at)
                   VALUES ('export', ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source, external_id) DO UPDATE SET
                       title = excluded.title, people = excluded.people, export_dir = excluded.export_dir""",
                (str(c.folder.resolve()), c.title, int(len(c.participants) > 2), len(c.participants),
                 ",".join(s["default_types"]), int(s["split_by_type"]), str(root.resolve()), now_iso()))
    ctx.success(f"Found {len(chats)} chats in the export. Pick the ones you want on the Chats page.")
    return {"chats": len(chats)}


def _unzip(ctx: JobContext, zip_path: Path) -> Path:
    dest = EXPORTS_DIR / zip_path.stem
    dest.mkdir(parents=True, exist_ok=True)
    ctx.log(f"Unpacking {zip_path.name} ...")
    with zipfile.ZipFile(zip_path) as zf:
        members = zf.infolist()
        ctx.set_total(len(members))
        base = dest.resolve()
        for m in members:
            ctx.check()
            target = (dest / m.filename).resolve()
            if base != target and base not in target.parents:
                continue  # never write outside the destination ("zip slip")
            zf.extract(m, dest)
            ctx.step()
    if zip_path.parent.resolve() == EXPORTS_DIR.resolve():
        zip_path.unlink(missing_ok=True)
    return dest


# --------------------------------------------------------------------------- #
# Library maintenance
# --------------------------------------------------------------------------- #
@handler("rescan")
def job_rescan(ctx: JobContext, params: dict) -> dict:
    ctx.log("Checking the library folder for changes made outside the app...")
    with get_db() as conn:
        gone = library.drop_missing(conn)
        added = library.import_untracked(conn, library.root_id(conn), log=ctx.log)
    ctx.success(f"Added {added['files']} files and {added['folders']} folders; "
                f"removed {gone['files']} missing files and {gone['folders']} missing folders.")
    return {"added_files": added["files"], "added_folders": added["folders"],
            "removed_files": gone["files"], "removed_folders": gone["folders"]}


@handler("import_path")
def job_import_path(ctx: JobContext, params: dict) -> dict:
    src = Path(params["path"])
    move = bool(params.get("move"))
    ctx.log(f"{'Moving' if move else 'Copying'} {src} into the library...")
    with get_db() as conn:
        stats = library.import_external(conn, src, int(params["folder_id"]), move, ctx.log, lambda: ctx.cancelled)
    ctx.success(f"Imported {stats['files']} files and {stats['folders']} folders.")
    return stats


@handler("housekeeping")
def job_housekeeping(ctx: JobContext, params: dict) -> dict:
    with get_db() as conn:
        days = settings_mod.get(conn, "trash_retention_days")
        purged = library.purge_old_trash(conn, days)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
        conn.execute("DELETE FROM jobs WHERE finished_at IS NOT NULL AND finished_at < ?", (cutoff,))
        conn.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (now_iso(),))
    for d in STAGING_DIR.glob("job*"):
        shutil.rmtree(d, ignore_errors=True)
    optimize()
    if purged:
        ctx.log(f"Emptied {purged} files that were in the trash longer than {days} days.")
    return {"purged": purged}
