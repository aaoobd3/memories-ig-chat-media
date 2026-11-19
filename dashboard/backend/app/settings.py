"""User-facing settings with defaults and validation."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import HTTPException

from .db import get_setting, set_setting

KINDS = ("photo", "video", "post", "reel")

DEFAULTS: dict[str, Any] = {
    "ig_username": "",
    "login_method": "auto",          # auto | password | sessionid
    "message_limit": 200,            # messages read per chat on "Sync now"
    "watch_batch": 30,               # messages read per chat on each automatic check
    "auto_sync": False,              # keep checking selected chats automatically
    "auto_interval_min": 10,         # minutes between automatic checks
    "default_types": list(KINDS),
    "since": "",                     # only messages on/after this date (YYYY-MM-DD)
    "include_private": False,        # show one-to-one chats too
    "search_threads": 60,            # how many recent chats to load
    "delay_min": 1.0,                # pause between Instagram requests (seconds)
    "delay_max": 3.0,
    "download_shared_from_export": False,
    "split_by_type": True,           # new chats get Photos/Videos/Posts/Reels subfolders
    "trash_retention_days": 30,
    "theme": "system",               # system | light | dark
    "grid_size": "medium",           # small | medium | large
}

_LIMITS = {
    "message_limit": (1, 20000),
    "watch_batch": (5, 500),
    "auto_interval_min": (1, 1440),
    "search_threads": (10, 500),
    "delay_min": (0.0, 30.0),
    "delay_max": (0.5, 60.0),
    "trash_retention_days": (0, 3650),
}
_CHOICES = {
    "login_method": ("auto", "password", "sessionid"),
    "theme": ("system", "light", "dark"),
    "grid_size": ("small", "medium", "large"),
}


def load(conn: sqlite3.Connection) -> dict[str, Any]:
    return {k: get_setting(conn, k, v) for k, v in DEFAULTS.items()}


def get(conn: sqlite3.Connection, key: str) -> Any:
    return get_setting(conn, key, DEFAULTS.get(key))


def _validate(key: str, value: Any) -> Any:
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return bool(value)
    if key in _LIMITS:
        lo, hi = _LIMITS[key]
        try:
            num = float(value) if isinstance(default, float) else int(value)
        except (TypeError, ValueError):
            raise HTTPException(400, f"'{key}' must be a number.")
        if not lo <= num <= hi:
            raise HTTPException(400, f"'{key}' must be between {lo} and {hi}.")
        return num
    if key in _CHOICES:
        if value not in _CHOICES[key]:
            raise HTTPException(400, f"'{key}' must be one of {', '.join(_CHOICES[key])}.")
        return value
    if key == "default_types":
        chosen = [t for t in KINDS if t in (value or [])]
        if not chosen:
            raise HTTPException(400, "Pick at least one type to download.")
        return chosen
    if key == "since":
        value = (value or "").strip()
        if value:
            from datetime import datetime
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, "The start date must look like 2026-01-31.")
        return value
    if key == "ig_username":
        return (value or "").strip().lstrip("@")
    return value


def update(conn: sqlite3.Connection, changes: dict[str, Any]) -> dict[str, Any]:
    clean = {}
    for key, value in changes.items():
        if key not in DEFAULTS:
            continue
        clean[key] = _validate(key, value)
    merged = {**load(conn), **clean}
    if merged["delay_max"] < merged["delay_min"]:
        raise HTTPException(400, "The longest pause can't be shorter than the shortest pause.")
    for key, value in clean.items():
        set_setting(conn, key, value)
    return load(conn)
