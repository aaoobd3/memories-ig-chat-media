"""Library API: folder tree, files, copy/move/delete/restore, uploads, zip downloads, previews."""
from __future__ import annotations

import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from . import library
from .config import STAGING_DIR
from .db import db_dep, fts_available, rows_dicts
from .jobs import runner
from .security import require_auth
from .thumbs import make_thumb

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

FILE_COLUMNS = """f.id, f.folder_id, f.name, f.media_type, f.mime, f.size, f.sha256, f.chat_id, f.sender,
                  f.sent_at, f.permalink, f.favorite, f.trashed_at, f.trash_origin, f.created_at, f.updated_at,
                  c.title AS chat_title"""


# --------------------------------------------------------------------------- #
# Folders
# --------------------------------------------------------------------------- #
@router.get("/folders")
def list_folders(conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    root = library.root_id(conn)
    rows = conn.execute(
        """SELECT d.id, d.parent_id, d.name, d.role, d.chat_id, d.media_type, d.color,
                  coalesce(s.n, 0) AS file_count, coalesce(s.bytes, 0) AS bytes
           FROM folders d
           LEFT JOIN (SELECT folder_id, count(*) AS n, sum(size) AS bytes
                      FROM files WHERE trashed_at IS NULL GROUP BY folder_id) s ON s.folder_id = d.id
           ORDER BY d.parent_id, d.name COLLATE NOCASE"""
    ).fetchall()
    return {"root_id": root, "folders": rows_dicts(rows)}


class FolderCreate(BaseModel):
    parent_id: int
    name: str = Field(min_length=1, max_length=150)


class FolderUpdate(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[int] = None
    color: Optional[str] = None


@router.post("/folders")
def create_folder(body: FolderCreate, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    fid = library.create_folder(conn, body.parent_id, body.name)
    return {"id": fid}


@router.get("/folders/{folder_id}")
def folder_info(folder_id: int, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    folder = dict(library.get_folder(conn, folder_id))
    folder["breadcrumbs"] = library.breadcrumbs(conn, folder_id)
    folder["path"] = str(library.folder_path(conn, folder_id))
    if folder["chat_id"]:
        chat = conn.execute("SELECT id, title FROM chats WHERE id = ?", (folder["chat_id"],)).fetchone()
        folder["chat"] = dict(chat) if chat else None
    folder["download_target_for"] = rows_dicts(conn.execute(
        "SELECT id, title FROM chats WHERE folder_id = ?", (folder_id,)).fetchall())
    return folder


@router.patch("/folders/{folder_id}")
def update_folder(folder_id: int, body: FolderUpdate, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    if body.name is not None:
        library.rename_folder(conn, folder_id, body.name)
    if body.parent_id is not None:
        library.move_folder(conn, folder_id, body.parent_id)
    if body.color is not None:
        color = body.color if body.color in ("", "red", "orange", "yellow", "green", "blue", "purple", "pink") else ""
        conn.execute("UPDATE folders SET color = ? WHERE id = ?", (color or None, folder_id))
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Listing files
# --------------------------------------------------------------------------- #
SORTS = {
    "name": "f.name COLLATE NOCASE",
    "date": "f.created_at",
    "sent": "coalesce(f.sent_at, f.created_at)",
    "size": "f.size",
    "type": "f.media_type",
}


@router.get("/files")
def list_files(
    conn: sqlite3.Connection = Depends(db_dep),
    view: Literal["folder", "all", "favorites", "recent", "trash", "duplicates"] = "folder",
    folder_id: Optional[int] = None,
    recursive: bool = False,
    media_type: Optional[str] = Query(None, alias="type"),
    chat_id: Optional[int] = None,
    q: str = "",
    sort: str = "sent",
    order: Literal["asc", "desc"] = "desc",
    offset: int = Query(0, ge=0),
    limit: int = Query(120, ge=1, le=500),
) -> dict:
    where, args = [], []
    joins = "LEFT JOIN chats c ON c.id = f.chat_id"
    if view == "trash":
        where.append("f.trashed_at IS NOT NULL")
    else:
        where.append("f.trashed_at IS NULL")
    if view == "folder":
        if folder_id is None:
            folder_id = library.root_id(conn)
        library.get_folder(conn, folder_id)
        if recursive:
            ids = library.descendant_ids(conn, folder_id)
            where.append(f"f.folder_id IN ({','.join('?' * len(ids))})")
            args += ids
        else:
            where.append("f.folder_id = ?")
            args.append(folder_id)
    elif view == "favorites":
        where.append("f.favorite = 1")
    elif view == "duplicates":
        where.append("f.sha256 IN (SELECT sha256 FROM files WHERE trashed_at IS NULL AND sha256 IS NOT NULL "
                     "GROUP BY sha256 HAVING count(*) > 1)")
    if media_type:
        where.append("f.media_type = ?")
        args.append(media_type)
    if chat_id:
        where.append("f.chat_id = ?")
        args.append(chat_id)
    q = q.strip()
    if q:
        if fts_available() and len(q) >= 3:
            where.append("f.id IN (SELECT rowid FROM files_fts WHERE files_fts MATCH ?)")
            args.append('"' + q.replace('"', '""') + '"')
        else:
            like = f"%{q}%"
            where.append("(f.name LIKE ? OR f.sender LIKE ? OR c.title LIKE ?)")
            args += [like, like, like]

    order_sql = SORTS.get(sort, SORTS["sent"])
    if view == "recent":
        order_sql, order = "f.created_at", "desc"
    if view == "trash":
        order_sql = "f.trashed_at"
    if view == "duplicates":
        order_sql = "f.sha256, f.created_at"
        order = "asc"
    sql_where = " AND ".join(where)
    total = conn.execute(f"SELECT count(*) FROM files f {joins} WHERE {sql_where}", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT {FILE_COLUMNS} FROM files f {joins} WHERE {sql_where} "
        f"ORDER BY {order_sql} {order.upper()}, f.id {order.upper()} LIMIT ? OFFSET ?",
        [*args, limit, offset]).fetchall()

    result: dict = {"items": rows_dicts(rows), "total": total, "offset": offset, "limit": limit}
    if view == "folder" and offset == 0:
        result["folder"] = dict(library.get_folder(conn, folder_id))
        result["breadcrumbs"] = library.breadcrumbs(conn, folder_id)
        folder_where, folder_args = "d.parent_id = ?", [folder_id]
        if q:
            folder_where += " AND d.name LIKE ?"
            folder_args.append(f"%{q}%")
        result["folders"] = rows_dicts(conn.execute(
            f"""SELECT d.id, d.parent_id, d.name, d.role, d.chat_id, d.media_type, d.color,
                       (SELECT count(*) FROM files x WHERE x.folder_id = d.id AND x.trashed_at IS NULL) AS file_count,
                       (SELECT count(*) FROM folders y WHERE y.parent_id = d.id) AS folder_count,
                       (SELECT x.id FROM files x WHERE x.folder_id = d.id AND x.trashed_at IS NULL
                          AND x.media_type != 'other' ORDER BY x.created_at DESC LIMIT 1) AS cover_id
                FROM folders d WHERE {folder_where} ORDER BY d.role = 'type' DESC, d.name COLLATE NOCASE""",
            folder_args).fetchall())
    return result


@router.get("/files/{file_id}")
def file_info(file_id: int, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    row = conn.execute(f"SELECT {FILE_COLUMNS} FROM files f LEFT JOIN chats c ON c.id = f.chat_id WHERE f.id = ?",
                       (file_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "That file doesn't exist anymore.")
    info = dict(row)
    if not info["trashed_at"] and info["folder_id"]:
        info["breadcrumbs"] = library.breadcrumbs(conn, info["folder_id"])
    info["duplicates"] = conn.execute(
        "SELECT count(*) FROM files WHERE sha256 = ? AND id != ? AND trashed_at IS NULL",
        (info["sha256"], file_id)).fetchone()[0] if info["sha256"] else 0
    return info


@router.get("/files/{file_id}/content")
def file_content(file_id: int, download: bool = False, conn: sqlite3.Connection = Depends(db_dep)):
    row = library.get_file(conn, file_id)
    path = library.file_path(conn, row)
    if not path.is_file():
        raise HTTPException(404, "The file is missing on disk.")
    return FileResponse(path, media_type=row["mime"] or None,
                        filename=row["name"] if download else None,
                        content_disposition_type="attachment" if download else "inline",
                        headers={"Cache-Control": "private, max-age=3600"})


@router.get("/files/{file_id}/thumb")
def file_thumb(file_id: int, conn: sqlite3.Connection = Depends(db_dep)):
    row = library.get_file(conn, file_id)
    path = library.file_path(conn, row)
    if not path.is_file():
        raise HTTPException(404, "missing")
    thumb = make_thumb(file_id, row["sha256"], path, row["mime"] or "")
    if thumb is None:
        raise HTTPException(404, "no preview")
    return FileResponse(thumb, media_type="image/webp",
                        headers={"Cache-Control": "private, max-age=604800, immutable"})


class FileUpdate(BaseModel):
    name: Optional[str] = None
    favorite: Optional[bool] = None
    media_type: Optional[str] = None


@router.patch("/files/{file_id}")
def update_file(file_id: int, body: FileUpdate, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    if body.name is not None:
        library.rename_file(conn, file_id, body.name)
    if body.favorite is not None:
        conn.execute("UPDATE files SET favorite = ? WHERE id = ?", (int(body.favorite), file_id))
    if body.media_type is not None:
        library.set_media_type(conn, file_id, body.media_type)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Bulk operations (each entry is committed on its own, so the disk and the
# database never disagree when one item in a selection fails)
# --------------------------------------------------------------------------- #
class Entries(BaseModel):
    file_ids: list[int] = []
    folder_ids: list[int] = []


class EntriesTo(Entries):
    target_id: int


class IdsValue(BaseModel):
    ids: list[int]
    value: Optional[bool] = None
    media_type: Optional[str] = None


def _each(conn: sqlite3.Connection, items: list, fn) -> dict:
    done, errors = 0, []
    for item in items:
        try:
            fn(item)
            conn.commit()
            done += 1
        except library.LibError as e:
            conn.rollback()
            errors.append(str(e))
        except OSError as e:
            conn.rollback()
            errors.append(f"Couldn't change a file on disk: {e.strerror or e}")
        except sqlite3.IntegrityError:
            conn.rollback()
            errors.append("Something with the same name was just created there. Try again.")
    return {"done": done, "errors": errors[:20]}


def _merge(*results: dict) -> dict:
    return {"done": sum(r["done"] for r in results), "errors": [e for r in results for e in r["errors"]][:20]}


@router.post("/entries/move")
def move_entries(body: EntriesTo, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    return _merge(
        _each(conn, body.folder_ids, lambda fid: library.move_folder(conn, fid, body.target_id)),
        _each(conn, body.file_ids, lambda fid: library.move_file(conn, fid, body.target_id)),
    )


@router.post("/entries/copy")
def copy_entries(body: EntriesTo, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    return _merge(
        _each(conn, body.folder_ids, lambda fid: library.copy_folder(conn, fid, body.target_id)),
        _each(conn, body.file_ids, lambda fid: library.copy_file(conn, fid, body.target_id)),
    )


@router.post("/entries/delete")
def delete_entries(body: Entries, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    return _merge(
        _each(conn, body.folder_ids, lambda fid: library.delete_folder(conn, fid)),
        _each(conn, body.file_ids, lambda fid: library.trash_file(conn, fid)),
    )


@router.post("/files/restore")
def restore_files(body: IdsValue, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    return _each(conn, body.ids, lambda fid: library.restore_file(conn, fid))


@router.post("/files/purge")
def purge_files(body: IdsValue, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    return _each(conn, body.ids, lambda fid: library.purge_file(conn, fid))


@router.post("/trash/empty")
def empty_trash(conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    ids = [r["id"] for r in conn.execute("SELECT id FROM files WHERE trashed_at IS NOT NULL")]
    return _each(conn, ids, lambda fid: library.purge_file(conn, fid))


@router.post("/files/favorite")
def favorite_files(body: IdsValue, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    marks = ",".join("?" * len(body.ids)) or "NULL"
    cur = conn.execute(f"UPDATE files SET favorite = ? WHERE id IN ({marks})", [int(bool(body.value)), *body.ids])
    return {"done": cur.rowcount, "errors": []}


@router.post("/files/set-type")
def set_type(body: IdsValue, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    if body.media_type not in (*library.KINDS, "other"):
        raise HTTPException(400, "Unknown type.")
    return _each(conn, body.ids, lambda fid: library.set_media_type(conn, fid, body.media_type))


@router.post("/files/upload")
async def upload_files(folder_id: int = Form(...), files: list[UploadFile] = File(...),
                       conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    library.get_folder(conn, folder_id)
    done, errors = 0, []
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    for up in files:
        name = library.safe_file_name(Path(up.filename or "file").name)
        with tempfile.NamedTemporaryFile(dir=STAGING_DIR, delete=False, suffix=".upload") as tmp:
            while chunk := await up.read(1 << 20):
                tmp.write(chunk)
            tmp_path = Path(tmp.name)
        try:
            library.register_file(conn, tmp_path, folder_id, name=name)
            conn.commit()
            done += 1
        except (library.LibError, OSError) as e:
            conn.rollback()
            tmp_path.unlink(missing_ok=True)
            errors.append(f"{name}: {e}")
    return {"done": done, "errors": errors}


@router.get("/zip")
def download_zip(files: str = "", folders: str = "", name: str = "instagram-media",
                 conn: sqlite3.Connection = Depends(db_dep)):
    file_ids = [int(x) for x in files.split(",") if x.strip().isdigit()]
    folder_ids = [int(x) for x in folders.split(",") if x.strip().isdigit()]
    if not file_ids and not folder_ids:
        raise HTTPException(400, "Nothing selected.")
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(dir=STAGING_DIR, delete=False, suffix=".zip")
    tmp.close()
    used: set[str] = set()
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
        for path, arc in library.iter_zip_entries(conn, file_ids, folder_ids):
            if not path.is_file():
                continue
            base, n = arc, 2
            while arc.casefold() in used:
                stem, ext = Path(base).stem, Path(base).suffix
                arc = str(Path(base).with_name(f"{stem} ({n}){ext}")).replace("\\", "/")
                n += 1
            used.add(arc.casefold())
            zf.write(path, arc)
    safe = library.safe_file_name(name) or "instagram-media"
    return FileResponse(tmp.name, media_type="application/zip", filename=f"{safe}.zip",
                        background=BackgroundTask(lambda: Path(tmp.name).unlink(missing_ok=True)))


class ImportPath(BaseModel):
    path: str
    folder_id: int
    move: bool = False


@router.post("/library/import")
def import_path(body: ImportPath, conn: sqlite3.Connection = Depends(db_dep)) -> dict:
    library.get_folder(conn, body.folder_id)
    p = Path(body.path.strip().strip('"')).expanduser()
    if not p.exists():
        raise HTTPException(404, "That file or folder doesn't exist on the computer running this app.")
    job = runner.enqueue("import_path", f"Import {p.name}", {"path": str(p), "folder_id": body.folder_id,
                                                              "move": body.move})
    return {"job_id": job}


@router.post("/library/rescan")
def rescan() -> dict:
    return {"job_id": runner.enqueue("rescan", "Rescan the library folder")}
