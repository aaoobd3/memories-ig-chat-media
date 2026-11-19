"""One-time startup work (folders, database, keys). Safe to run more than once."""
from __future__ import annotations

import shutil

from . import config
from .config import SESSION_FILE, ensure_dirs
from .db import get_db, init_db
from .library import root_id
from .security import apply_preset_passcode, ensure_key_file


def bootstrap() -> None:
    ensure_dirs()
    init_db()
    if not config.APP_SECRET_KEY:
        ensure_key_file()
    with get_db() as conn:
        apply_preset_passcode(conn)
        root_id(conn)
    # Re-use a login saved by the command-line tool, if there is one.
    legacy = config.PROJECT_ROOT / "ig_session.json"
    if not SESSION_FILE.exists() and legacy.is_file():
        shutil.copy2(legacy, SESSION_FILE)
