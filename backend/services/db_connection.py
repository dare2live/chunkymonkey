"""DuckDB connection entry points for the business database."""

from __future__ import annotations

import sys
from pathlib import Path

from services.database_manifest import get_database_manifest
from services.duck_adapter import connect as _duck_connect, DuckConn

_MANIFEST = get_database_manifest()
DB_PATH = _MANIFEST.path_for("smartmoney")
DB_DIR = DB_PATH.parent

__all__ = ["DB_DIR", "DB_PATH", "DuckConn", "current_db_paths", "get_conn"]


def _current_db_paths() -> tuple[Path, Path]:
    facade = sys.modules.get("services.db")
    if facade is None:
        return DB_DIR, DB_PATH
    return getattr(facade, "DB_DIR", DB_DIR), getattr(facade, "DB_PATH", DB_PATH)


def current_db_paths() -> tuple[Path, Path]:
    """Return the active DB directory/path, honoring services.db monkeypatches."""
    return _current_db_paths()


def get_conn(timeout: int = 30) -> DuckConn:
    """返回 DuckDB 连接。"""
    db_dir, db_path = current_db_paths()
    db_dir.mkdir(parents=True, exist_ok=True)
    return _duck_connect(str(db_path), timeout=timeout)
