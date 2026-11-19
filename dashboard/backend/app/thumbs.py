"""Small cached previews for the library grid (images via Pillow, videos via ffmpeg if installed)."""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional

from .config import THUMBS_DIR

THUMB_PX = 480
_FFMPEG = shutil.which("ffmpeg")
_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(file_id: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(file_id, threading.Lock())


def thumb_path(file_id: int, sha: Optional[str]) -> Path:
    return THUMBS_DIR / f"{file_id}_{(sha or 'x')[:12]}.webp"


def drop_thumb(file_id: int) -> None:
    for p in THUMBS_DIR.glob(f"{file_id}_*"):
        p.unlink(missing_ok=True)


def make_thumb(file_id: int, sha: Optional[str], src: Path, mime: str) -> Optional[Path]:
    out = thumb_path(file_id, sha)
    if out.exists():
        return out
    with _lock_for(file_id):
        if out.exists():
            return out
        THUMBS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            if mime.startswith("image/"):
                return _image_thumb(src, out)
            if mime.startswith("video/") and _FFMPEG:
                return _video_thumb(src, out)
        except Exception:
            out.unlink(missing_ok=True)
        return None


def _image_thumb(src: Path, out: Path) -> Optional[Path]:
    from PIL import Image, ImageOps

    # Several web workers may build the same preview at once: write to a private
    # temp file, then atomically swap it into place.
    tmp = out.with_name(f"{out.stem}.{_tag()}.part.webp")
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((THUMB_PX, THUMB_PX))
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
            im.save(tmp, "WEBP", quality=78, method=4)
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    return out


def _tag() -> str:
    return f"{os.getpid()}-{threading.get_ident()}"


def _video_thumb(src: Path, out: Path) -> Optional[Path]:
    tmp = out.with_name(f"{out.stem}.{_tag()}.frame.jpg")
    for seek in ("1", "0"):
        subprocess.run(
            [_FFMPEG, "-y", "-loglevel", "error", "-ss", seek, "-i", str(src), "-frames:v", "1",
             "-vf", f"scale='min({THUMB_PX},iw)':-2", str(tmp)],
            timeout=30, check=False, stdin=subprocess.DEVNULL,
        )
        if tmp.exists() and tmp.stat().st_size > 0:
            break
    if not tmp.exists():
        return None
    try:
        return _image_thumb(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
