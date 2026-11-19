"""Paths and environment configuration. Everything the app writes lives in DATA_DIR."""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]          # dashboard/backend
DASHBOARD_DIR = BACKEND_DIR.parent                          # dashboard
PROJECT_ROOT = DASHBOARD_DIR.parent                         # folder that holds ig_chat_media.py

# Where ig_chat_media.py (the original CLI, reused for detection/downloads) lives.
CORE_DIR = Path(os.environ.get("IGCM_CORE_DIR", PROJECT_ROOT)).resolve()
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

DATA_DIR = Path(os.environ.get("DATA_DIR", DASHBOARD_DIR / "data")).expanduser().resolve()
LIBRARY_DIR = Path(os.environ.get("LIBRARY_DIR", DATA_DIR / "library")).expanduser().resolve()
TRASH_DIR = DATA_DIR / "trash"
THUMBS_DIR = DATA_DIR / "thumbs"
STAGING_DIR = DATA_DIR / "staging"
EXPORTS_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "app.db"
SESSION_FILE = DATA_DIR / "ig_session.json"
SECRET_KEY_FILE = DATA_DIR / "secret.key"

FRONTEND_DIST = Path(os.environ.get("FRONTEND_DIST", DASHBOARD_DIR / "frontend" / "dist")).resolve()

# Optional: preset the dashboard passcode (e.g. in docker-compose) instead of the first-run screen.
PRESET_PASSCODE = os.environ.get("DASHBOARD_PASSCODE", "").strip()
# Optional: Fernet key for encrypting the Instagram password/sessionid. Generated if missing.
APP_SECRET_KEY = os.environ.get("APP_SECRET_KEY", "").strip()


def ensure_dirs() -> None:
    for d in (DATA_DIR, LIBRARY_DIR, TRASH_DIR, THUMBS_DIR, STAGING_DIR, EXPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
