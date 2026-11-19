"""The media library: a folder tree stored in SQLite and mirrored on disk.

Every folder row is a real directory under LIBRARY_DIR and every live file row is
a real file inside its folder's directory, so the library can also be browsed
with Explorer/Finder. Deleted files go to a trash directory and can be restored.
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from .config import LIBRARY_DIR, TRASH_DIR
from .db import now_iso

KINDS = ("photo", "video", "post", "reel")
TYPE_FOLDER_NAMES = {"photo": "Photos", "video": "Videos", "post": "Posts", "reel": "Reels"}
_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}

mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/heic", ".heic")
mimetypes.add_type("video/mp4", ".m4v")


class LibError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- #
# Names
# --------------------------------------------------------------------------- #
def clean_name(name: str) -> str:
    name = (name or "").strip().rstrip(". ")
    if not name or name in (".", ".."):
        raise LibError("Please type a name.")
    if _INVALID.search(name):
        raise LibError('Names can\'t contain any of these characters:  < > : " / \\ | ? *')
    if len(name) > 150:
        raise LibError("That name is too long (150 characters max).")
    if name.split(".")[0].upper() in _RESERVED:
        raise LibError("That name is reserved by Windows. Please pick another one.")
    return name


def safe_folder_name(text: str) -> str:
    """Turn any chat title into a valid folder name."""
    text = _INVALID.sub("_", text or "").strip().rstrip(". ") or "Chat"
    if text.split(".")[0].upper() in _RESERVED:
        text = f"_{text}"
    return text[:80]


def _split_ext(name: str) -> tuple[str, str]:
    p = Path(name)
    return (p.stem, p.suffix) if p.suffix and p.stem else (name, "")


# --------------------------------------------------------------------------- #
# Folders
# --------------------------------------------------------------------------- #
def root_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM folders WHERE role = 'root'").fetchone()
    if row:
        return row["id"]
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO folders(parent_id, name, role, created_at, updated_at) VALUES (NULL, '', 'root', ?, ?)",
        (ts, ts),
    )
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    return cur.lastrowid


def get_folder(conn: sqlite3.Connection, folder_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)).fetchone()
    if row is None:
        raise LibError("That folder doesn't exist anymore.", 404)
    return row


def folder_parts(conn: sqlite3.Connection, folder_id: int) -> list[str]:
    """Folder names from just below the root down to folder_id."""
    rows = conn.execute(
        """
        WITH RECURSIVE anc(id, parent_id, name, role, depth) AS (
            SELECT id, parent_id, name, role, 0 FROM folders WHERE id = ?
            UNION ALL
            SELECT f.id, f.parent_id, f.name, f.role, anc.depth + 1
            FROM folders f JOIN anc ON f.id = anc.parent_id
        )
        SELECT name, role FROM anc ORDER BY depth DESC
        """,
        (folder_id,),
    ).fetchall()
    if not rows:
        raise LibError("That folder doesn't exist anymore.", 404)
    return [r["name"] for r in rows if r["role"] != "root"]


def folder_path(conn: sqlite3.Connection, folder_id: int) -> Path:
    return LIBRARY_DIR.joinpath(*folder_parts(conn, folder_id))


def breadcrumbs(conn: sqlite3.Connection, folder_id: int) -> list[dict]:
    rows = conn.execute(
        """
        WITH RECURSIVE anc(id, parent_id, name, role, depth) AS (
            SELECT id, parent_id, name, role, 0 FROM folders WHERE id = ?
            UNION ALL
            SELECT f.id, f.parent_id, f.name, f.role, anc.depth + 1
            FROM folders f JOIN anc ON f.id = anc.parent_id
        )
        SELECT id, name, role FROM anc ORDER BY depth DESC
        """,
        (folder_id,),
    ).fetchall()
    return [{"id": r["id"], "name": r["name"] or "Library", "role": r["role"]} for r in rows]


def descendant_ids(conn: sqlite3.Connection, folder_id: int) -> list[int]:
    rows = conn.execute(
        """
        WITH RECURSIVE sub(id) AS (
            SELECT id FROM folders WHERE id = ?
            UNION ALL
            SELECT f.id FROM folders f JOIN sub ON f.parent_id = sub.id
        )
        SELECT id FROM sub
        """,
        (folder_id,),
    ).fetchall()
    return [r["id"] for r in rows]


def _name_taken(conn: sqlite3.Connection, folder_id: int, name: str,
                skip_file: Optional[int] = None, skip_folder: Optional[int] = None) -> bool:
    if conn.execute(
        "SELECT 1 FROM files WHERE folder_id = ? AND trashed_at IS NULL AND name = ? COLLATE NOCASE AND id IS NOT ?",
        (folder_id, name, skip_file),
    ).fetchone():
        return True
    if conn.execute(
        "SELECT 1 FROM folders WHERE parent_id = ? AND name = ? COLLATE NOCASE AND id IS NOT ?",
        (folder_id, name, skip_folder),
    ).fetchone():
        return True
    if skip_file is None and skip_folder is None:
        return (folder_path(conn, folder_id) / name).exists()
    return False


def unique_name(conn: sqlite3.Connection, folder_id: int, name: str,
                skip_file: Optional[int] = None, skip_folder: Optional[int] = None,
                is_folder: bool = False) -> str:
    if not _name_taken(conn, folder_id, name, skip_file, skip_folder):
        return name
    stem, ext = (name, "") if is_folder else _split_ext(name)
    m = re.match(r"^(.*) \((\d+)\)$", stem)
    if m:
        stem = m.group(1)
    for i in range(2, 10000):
        candidate = f"{stem} ({i}){ext}"
        if not _name_taken(conn, folder_id, candidate, skip_file, skip_folder):
            return candidate
    raise LibError("Too many items with the same name in this folder.")


def _reserve(conn: sqlite3.Connection, folder_id: int, name: str) -> tuple[str, Path]:
    """Pick a free file name and claim it on disk atomically (O_EXCL), so two web
    workers saving into the same folder at the same moment can't overwrite each other."""
    base = folder_path(conn, folder_id)
    base.mkdir(parents=True, exist_ok=True)
    for _ in range(100):
        name = unique_name(conn, folder_id, name)
        path = base / name
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return name, path
        except FileExistsError:
            continue  # someone else just took it; unique_name now sees it on disk
    raise LibError("Couldn't find a free name in this folder. Try again.")


def _place(src: Path, dest: Path, copy: bool) -> None:
    """Fill a reserved placeholder with the real file (atomic rename when possible)."""
    try:
        if copy:
            shutil.copy2(src, dest)
        else:
            try:
                os.replace(src, dest)
            except OSError:  # different drive
                shutil.copy2(src, dest)
                os.unlink(src)
    except BaseException:
        if dest.exists() and dest.stat().st_size == 0:
            dest.unlink(missing_ok=True)
        raise


def create_folder(conn: sqlite3.Connection, parent_id: int, name: str, role: str = "user",
                  chat_id: Optional[int] = None, media_type: Optional[str] = None,
                  auto_rename: bool = False) -> int:
    get_folder(conn, parent_id)
    name = clean_name(name)
    if auto_rename:
        name = unique_name(conn, parent_id, name, is_folder=True)
    elif _name_taken(conn, parent_id, name):
        raise LibError(f"There's already something called “{name}” here.", 409)
    path = folder_path(conn, parent_id) / name
    path.mkdir(parents=True, exist_ok=True)
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO folders(parent_id, name, role, chat_id, media_type, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (parent_id, name, role, chat_id, media_type, ts, ts),
    )
    return cur.lastrowid


def rename_folder(conn: sqlite3.Connection, folder_id: int, new_name: str) -> None:
    folder = get_folder(conn, folder_id)
    if folder["role"] == "root":
        raise LibError("The main Library folder can't be renamed.")
    new_name = clean_name(new_name)
    if new_name == folder["name"]:
        return
    if new_name.casefold() != folder["name"].casefold() and _name_taken(conn, folder["parent_id"], new_name):
        raise LibError(f"There's already something called “{new_name}” here.", 409)
    old_path = folder_path(conn, folder_id)
    new_path = old_path.with_name(new_name)
    if old_path.exists():
        old_path.rename(new_path)
    else:
        new_path.mkdir(parents=True, exist_ok=True)
    conn.execute("UPDATE folders SET name = ?, updated_at = ? WHERE id = ?", (new_name, now_iso(), folder_id))


def move_folder(conn: sqlite3.Connection, folder_id: int, target_id: int) -> None:
    folder = get_folder(conn, folder_id)
    if folder["role"] == "root":
        raise LibError("The main Library folder can't be moved.")
    get_folder(conn, target_id)
    if folder["parent_id"] == target_id:
        return
    if target_id in descendant_ids(conn, folder_id):
        raise LibError("A folder can't be moved inside itself.")
    name = unique_name(conn, target_id, folder["name"], is_folder=True)
    src = folder_path(conn, folder_id)
    dst = folder_path(conn, target_id) / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.move(str(src), str(dst))
    else:
        dst.mkdir(parents=True, exist_ok=True)
    conn.execute("UPDATE folders SET parent_id = ?, name = ?, updated_at = ? WHERE id = ?",
                 (target_id, name, now_iso(), folder_id))


def copy_folder(conn: sqlite3.Connection, folder_id: int, target_id: int) -> int:
    folder = get_folder(conn, folder_id)
    if folder["role"] == "root":
        raise LibError("The main Library folder can't be copied. Open it and copy what's inside.")
    get_folder(conn, target_id)
    if target_id in descendant_ids(conn, folder_id):
        raise LibError("A folder can't be copied inside itself.")
    return _copy_folder_rec(conn, folder_id, target_id)


def _copy_folder_rec(conn: sqlite3.Connection, folder_id: int, target_id: int) -> int:
    folder = get_folder(conn, folder_id)
    # Copies are plain user folders; they don't receive new downloads.
    new_id = create_folder(conn, target_id, folder["name"], role="user",
                           media_type=folder["media_type"] if folder["role"] == "type" else None,
                           auto_rename=True)
    for f in conn.execute("SELECT id FROM files WHERE folder_id = ? AND trashed_at IS NULL", (folder_id,)).fetchall():
        copy_file(conn, f["id"], new_id)
    for child in conn.execute("SELECT id FROM folders WHERE parent_id = ? ORDER BY name", (folder_id,)).fetchall():
        _copy_folder_rec(conn, child["id"], new_id)
    return new_id


def delete_folder(conn: sqlite3.Connection, folder_id: int) -> int:
    """Move every file inside to the trash, then remove the folder(s)."""
    folder = get_folder(conn, folder_id)
    if folder["role"] == "root":
        raise LibError("The main Library folder can't be deleted.")
    path = folder_path(conn, folder_id)
    # Files that are on disk but not yet known (e.g. copied in with Explorer) are adopted first,
    # so nothing is lost when the directory is removed.
    import_untracked(conn, folder_id)
    ids = descendant_ids(conn, folder_id)
    marks = ",".join("?" * len(ids))
    trashed = 0
    for f in conn.execute(
        f"SELECT id FROM files WHERE folder_id IN ({marks}) AND trashed_at IS NULL", ids
    ).fetchall():
        trash_file(conn, f["id"])
        trashed += 1
    conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))  # children cascade
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    return trashed


def ensure_child(conn: sqlite3.Connection, parent_id: int, name: str, role: str = "user",
                 chat_id: Optional[int] = None, media_type: Optional[str] = None) -> int:
    """Find a matching child folder (by type, else by name) or create it."""
    if role == "type" and media_type:
        row = conn.execute(
            "SELECT id FROM folders WHERE parent_id = ? AND role = 'type' AND media_type = ?",
            (parent_id, media_type),
        ).fetchone()
        if row:
            return row["id"]
    row = conn.execute(
        "SELECT id FROM folders WHERE parent_id = ? AND name = ? COLLATE NOCASE", (parent_id, name)
    ).fetchone()
    if row:
        if role == "type" and media_type:  # adopt an existing folder named "Photos" etc.
            conn.execute("UPDATE folders SET role = 'type', media_type = ? WHERE id = ? AND role = 'user'",
                         (media_type, row["id"]))
        folder_path(conn, row["id"]).mkdir(parents=True, exist_ok=True)
        return row["id"]
    return create_folder(conn, parent_id, name, role=role, chat_id=chat_id, media_type=media_type,
                         auto_rename=True)


def chat_home(conn: sqlite3.Connection, chat: sqlite3.Row) -> int:
    """The chat's download folder; created under the Library root the first time."""
    if chat["folder_id"]:
        row = conn.execute("SELECT id FROM folders WHERE id = ?", (chat["folder_id"],)).fetchone()
        if row:
            folder_path(conn, row["id"]).mkdir(parents=True, exist_ok=True)
            return row["id"]
    fid = ensure_child(conn, root_id(conn), safe_folder_name(chat["title"]), role="chat", chat_id=chat["id"])
    conn.execute("UPDATE folders SET chat_id = ? WHERE id = ? AND chat_id IS NULL", (chat["id"], fid))
    conn.execute("UPDATE chats SET folder_id = ? WHERE id = ?", (fid, chat["id"]))
    return fid


def target_folder_for(conn: sqlite3.Connection, chat: sqlite3.Row, kind: str) -> int:
    home = chat_home(conn, chat)
    if not chat["split_by_type"]:
        return home
    return ensure_child(conn, home, TYPE_FOLDER_NAMES[kind], role="type", chat_id=chat["id"], media_type=kind)


# --------------------------------------------------------------------------- #
# Files
# --------------------------------------------------------------------------- #
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def guess_mime(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def media_type_for_mime(mime: str) -> str:
    if mime.startswith("image/"):
        return "photo"
    if mime.startswith("video/"):
        return "video"
    return "other"


def get_file(conn: sqlite3.Connection, file_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    if row is None:
        raise LibError("That file doesn't exist anymore.", 404)
    return row


def file_path(conn: sqlite3.Connection, row: sqlite3.Row) -> Path:
    if row["trashed_at"]:
        return TRASH_DIR / row["trash_path"]
    return folder_path(conn, row["folder_id"]) / row["name"]


def _folder_media_type(conn: sqlite3.Connection, folder_id: int) -> Optional[str]:
    row = conn.execute("SELECT role, media_type FROM folders WHERE id = ?", (folder_id,)).fetchone()
    return row["media_type"] if row and row["role"] == "type" else None


def register_file(conn: sqlite3.Connection, src: Path, folder_id: int, name: Optional[str] = None,
                  media_type: Optional[str] = None, meta: Optional[dict] = None, copy: bool = False) -> int:
    """Put a file on disk into a library folder and record it."""
    meta = meta or {}
    get_folder(conn, folder_id)
    name, dest = _reserve(conn, folder_id, clean_name(name or src.name))
    _place(src, dest, copy)
    try:
        mime = guess_mime(name)
        mt = media_type or _folder_media_type(conn, folder_id) or media_type_for_mime(mime)
        ts = now_iso()
        cur = conn.execute(
            """INSERT INTO files(folder_id, name, media_type, mime, size, sha256, item_id, chat_id,
                                 sender, sent_at, permalink, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (folder_id, name, mt, mime, dest.stat().st_size, sha256_file(dest), meta.get("item_id"),
             meta.get("chat_id"), meta.get("sender"), meta.get("sent_at"), meta.get("permalink"), ts, ts),
        )
        return cur.lastrowid
    except Exception:
        if copy:
            dest.unlink(missing_ok=True)
        else:
            _place(dest, src, copy=False)
        raise


def move_file(conn: sqlite3.Connection, file_id: int, target_id: int) -> None:
    row = get_file(conn, file_id)
    if row["trashed_at"]:
        raise LibError("Restore the file from the trash first.")
    get_folder(conn, target_id)
    if row["folder_id"] == target_id:
        return
    src = file_path(conn, row)
    if not src.exists():
        raise LibError(f"“{row['name']}” is missing on disk. Try Settings → Rescan library.", 404)
    name, dst = _reserve(conn, target_id, row["name"])
    _place(src, dst, copy=False)
    new_type = _folder_media_type(conn, target_id)
    if new_type and row["media_type"] == "other":
        new_type = None  # documents dropped into "Photos" keep their type
    conn.execute(
        "UPDATE files SET folder_id = ?, name = ?, media_type = coalesce(?, media_type), updated_at = ? WHERE id = ?",
        (target_id, name, new_type, now_iso(), file_id),
    )


def copy_file(conn: sqlite3.Connection, file_id: int, target_id: int) -> int:
    row = get_file(conn, file_id)
    if row["trashed_at"]:
        raise LibError("Restore the file from the trash first.")
    src = file_path(conn, row)
    if not src.exists():
        raise LibError(f"“{row['name']}” is missing on disk. Try Settings → Rescan library.", 404)
    get_folder(conn, target_id)
    name, dst = _reserve(conn, target_id, row["name"])
    _place(src, dst, copy=True)
    new_type = _folder_media_type(conn, target_id)
    ts = now_iso()
    cur = conn.execute(
        """INSERT INTO files(folder_id, name, media_type, mime, size, sha256, item_id, chat_id, sender,
                             sent_at, permalink, favorite, created_at, updated_at)
           SELECT ?, ?, coalesce(?, media_type), mime, size, sha256, item_id, chat_id, sender,
                  sent_at, permalink, favorite, ?, ? FROM files WHERE id = ?""",
        (target_id, name, None if row["media_type"] == "other" else new_type, ts, ts, file_id),
    )
    return cur.lastrowid


def rename_file(conn: sqlite3.Connection, file_id: int, new_name: str) -> str:
    row = get_file(conn, file_id)
    if row["trashed_at"]:
        raise LibError("Restore the file from the trash first.")
    new_name = clean_name(new_name)
    old_ext = Path(row["name"]).suffix
    if old_ext and not Path(new_name).suffix:
        new_name += old_ext  # keep the extension if the user dropped it
    if new_name == row["name"]:
        return new_name
    if new_name.casefold() != row["name"].casefold() and _name_taken(
            conn, row["folder_id"], new_name, skip_file=file_id):
        raise LibError(f"There's already something called “{new_name}” here.", 409)
    src = file_path(conn, row)
    src.rename(src.with_name(new_name))
    conn.execute("UPDATE files SET name = ?, mime = ?, updated_at = ? WHERE id = ?",
                 (new_name, guess_mime(new_name), now_iso(), file_id))
    return new_name


def trash_file(conn: sqlite3.Connection, file_id: int) -> None:
    row = get_file(conn, file_id)
    if row["trashed_at"]:
        return
    src = file_path(conn, row)
    origin = "/".join(folder_parts(conn, row["folder_id"]))
    trash_name = f"{file_id}_{row['name']}"
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.move(str(src), str(TRASH_DIR / trash_name))
    conn.execute(
        "UPDATE files SET trashed_at = ?, trash_path = ?, trash_origin = ?, updated_at = ? WHERE id = ?",
        (now_iso(), trash_name, origin, now_iso(), file_id),
    )


def restore_file(conn: sqlite3.Connection, file_id: int) -> int:
    """Put a trashed file back where it was (recreating folders if needed). Returns its folder id."""
    row = get_file(conn, file_id)
    if not row["trashed_at"]:
        return row["folder_id"]
    folder_id = row["folder_id"]
    if folder_id is None or not conn.execute("SELECT 1 FROM folders WHERE id = ?", (folder_id,)).fetchone():
        folder_id = root_id(conn)
        for part in [p for p in (row["trash_origin"] or "").split("/") if p]:
            folder_id = ensure_child(conn, folder_id, part)
    src = TRASH_DIR / row["trash_path"]
    if not src.exists():
        raise LibError(f"“{row['name']}” is no longer in the trash folder on disk.", 404)
    name, dst = _reserve(conn, folder_id, row["name"])
    _place(src, dst, copy=False)
    conn.execute(
        "UPDATE files SET folder_id = ?, name = ?, trashed_at = NULL, trash_path = NULL, trash_origin = NULL, "
        "updated_at = ? WHERE id = ?",
        (folder_id, name, now_iso(), file_id),
    )
    return folder_id


def purge_file(conn: sqlite3.Connection, file_id: int) -> None:
    row = get_file(conn, file_id)
    if not row["trashed_at"]:
        trash_file(conn, file_id)
        row = get_file(conn, file_id)
    (TRASH_DIR / row["trash_path"]).unlink(missing_ok=True)
    conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
    from .thumbs import drop_thumb
    drop_thumb(file_id)


def purge_old_trash(conn: sqlite3.Connection, days: int) -> int:
    if days <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM files WHERE trashed_at IS NOT NULL AND trashed_at < ?", (cutoff,)).fetchall()]
    for fid in ids:
        purge_file(conn, fid)
    return len(ids)


def set_media_type(conn: sqlite3.Connection, file_id: int, media_type: str) -> None:
    """Change a file's type; if it sits in a chat's type folder, move it to the matching one."""
    if media_type not in (*KINDS, "other"):
        raise LibError("Unknown type.")
    row = get_file(conn, file_id)
    if row["trashed_at"]:
        raise LibError("Restore the file from the trash first.")
    folder = get_folder(conn, row["folder_id"])
    if folder["role"] == "type" and media_type in KINDS and folder["media_type"] != media_type:
        sibling = ensure_child(conn, folder["parent_id"], TYPE_FOLDER_NAMES[media_type], role="type",
                               chat_id=folder["chat_id"], media_type=media_type)
        move_file(conn, file_id, sibling)
    conn.execute("UPDATE files SET media_type = ?, updated_at = ? WHERE id = ?", (media_type, now_iso(), file_id))


# --------------------------------------------------------------------------- #
# Disk <-> database reconciliation
# --------------------------------------------------------------------------- #
def _is_hidden(p: Path) -> bool:
    return p.name.startswith(".") or p.name in _SKIP_NAMES or p.suffix == ".part"


def import_untracked(conn: sqlite3.Connection, folder_id: int, recursive: bool = True,
                     log: Optional[Callable[[str], None]] = None) -> dict:
    """Adopt files/folders that exist on disk but not in the database."""
    stats = {"folders": 0, "files": 0}
    base = folder_path(conn, folder_id)
    if not base.is_dir():
        return stats
    known_files = {r["name"].casefold() for r in conn.execute(
        "SELECT name FROM files WHERE folder_id = ? AND trashed_at IS NULL", (folder_id,))}
    known_dirs = {r["name"].casefold(): r["id"] for r in conn.execute(
        "SELECT id, name FROM folders WHERE parent_id = ?", (folder_id,))}
    for entry in sorted(base.iterdir(), key=lambda p: p.name.casefold()):
        if _is_hidden(entry):
            continue
        if entry.is_dir():
            child = known_dirs.get(entry.name.casefold())
            if child is None:
                try:
                    clean_name(entry.name)
                except LibError:
                    continue
                ts = now_iso()
                child = conn.execute(
                    "INSERT INTO folders(parent_id, name, role, created_at, updated_at) VALUES (?, ?, 'user', ?, ?)",
                    (folder_id, entry.name, ts, ts)).lastrowid
                stats["folders"] += 1
            if recursive:
                sub = import_untracked(conn, child, True, log)
                stats["folders"] += sub["folders"]
                stats["files"] += sub["files"]
        elif entry.is_file() and entry.name.casefold() not in known_files:
            try:
                clean_name(entry.name)
            except LibError:
                continue
            mime = guess_mime(entry.name)
            ts = now_iso()
            conn.execute(
                "INSERT INTO files(folder_id, name, media_type, mime, size, sha256, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (folder_id, entry.name, _folder_media_type(conn, folder_id) or media_type_for_mime(mime), mime,
                 entry.stat().st_size, sha256_file(entry), ts, ts))
            stats["files"] += 1
            if log and stats["files"] % 100 == 0:
                log(f"Added {stats['files']} files so far...")
    return stats


def drop_missing(conn: sqlite3.Connection) -> dict:
    """Forget folders/files that were removed from disk outside the app."""
    stats = {"folders": 0, "files": 0}
    root = root_id(conn)
    for r in conn.execute("SELECT id FROM folders WHERE role != 'root' ORDER BY id").fetchall():
        if not conn.execute("SELECT 1 FROM folders WHERE id = ?", (r["id"],)).fetchone():
            continue  # already removed with its parent
        if not folder_path(conn, r["id"]).is_dir():
            for f in conn.execute("SELECT id FROM files WHERE folder_id IN (%s)" % ",".join(
                    str(i) for i in descendant_ids(conn, r["id"]))).fetchall():
                conn.execute("DELETE FROM files WHERE id = ? AND trashed_at IS NULL", (f["id"],))
                stats["files"] += 1
            conn.execute("DELETE FROM folders WHERE id = ?", (r["id"],))
            stats["folders"] += 1
    for r in conn.execute("SELECT * FROM files WHERE trashed_at IS NULL").fetchall():
        if not file_path(conn, r).is_file():
            conn.execute("DELETE FROM files WHERE id = ?", (r["id"],))
            stats["files"] += 1
    _ = root
    return stats


def import_external(conn: sqlite3.Connection, src: Path, folder_id: int, move: bool,
                    log: Callable[[str], None], cancelled: Callable[[], bool]) -> dict:
    """Bring a file or a whole folder from anywhere on this computer into the library."""
    stats = {"folders": 0, "files": 0}
    lib = LIBRARY_DIR.resolve()
    src = src.expanduser().resolve()
    if src == lib or lib in src.parents:
        raise LibError("That's already inside the library. Use Move/Copy in the Library page instead.")
    if not src.exists():
        raise LibError("That file or folder doesn't exist on this computer.", 404)

    def walk(path: Path, dest: int) -> None:
        if cancelled():
            return
        if path.is_file():
            if not _is_hidden(path):
                register_file(conn, path, dest, safe_file_name(path.name), copy=not move)
                stats["files"] += 1
                if stats["files"] % 50 == 0:
                    log(f"Imported {stats['files']} files...")
                    conn.commit()
            return
        new_dest = create_folder(conn, dest, safe_folder_name(path.name), auto_rename=True)
        stats["folders"] += 1
        for child in sorted(path.iterdir(), key=lambda p: (p.is_dir(), p.name.casefold())):
            walk(child, new_dest)

    walk(src, folder_id)
    if move and src.is_dir():
        shutil.rmtree(src, ignore_errors=True)
    return stats


def safe_file_name(name: str) -> str:
    stem, ext = _split_ext(name)
    stem = _INVALID.sub("_", stem).strip().rstrip(". ") or "file"
    if stem.upper() in _RESERVED:
        stem = f"_{stem}"
    return (stem[:140] + _INVALID.sub("", ext)[:10])


def iter_zip_entries(conn: sqlite3.Connection, file_ids: Iterable[int],
                     folder_ids: Iterable[int]) -> Iterable[tuple[Path, str]]:
    """(disk path, path inside the zip) for a selection of files and folders."""
    for fid in file_ids:
        row = get_file(conn, fid)
        if not row["trashed_at"]:
            yield file_path(conn, row), row["name"]
    for folder_id in folder_ids:
        top = get_folder(conn, folder_id)
        top_name = top["name"] or "Library"
        base_parts = len(folder_parts(conn, folder_id))
        for sub in descendant_ids(conn, folder_id):
            rel = folder_parts(conn, sub)[base_parts:]
            for row in conn.execute("SELECT * FROM files WHERE folder_id = ? AND trashed_at IS NULL", (sub,)):
                yield file_path(conn, row), "/".join([top_name, *rel, row["name"]])
