"""Compact production DuckDB aliases after DROP/rebuild writers.

Writers must close connections first. ``db_compact.run`` always targets the
``database_manifest`` production path — tests that rebuilt a redirected file
must pass ``skip=True``.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

from services.duck_adapter import connect as duck_connect

REPO = Path(__file__).resolve().parents[1].parent
_COMPACT_SCRIPT = REPO / "backend" / "scripts" / "db_compact.py"
COMPACT_FREE_PCT = 10.0  # rule-compliance: ok evidence=2026-08-26 qfq full-table UPDATE market free_blocks ~25%; 25% moth band missed it
_SKIP_ENV = "CHUNKY_FEATURE_STORE_SKIP_COMPACT"


def _load_db_compact() -> Any:
    spec = importlib.util.spec_from_file_location("db_compact", _COMPACT_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load db_compact at {_COMPACT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def free_block_pct(alias: str) -> float | None:
    compact = _load_db_compact()
    path = compact._db_path(alias)
    if not path.exists():
        return None
    conn = duck_connect(str(path), read_only=True)
    try:
        row = conn.execute(
            "SELECT 100.0 * free_blocks / nullif(total_blocks, 0) "
            "FROM pragma_database_size()"
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    finally:
        conn.close()
    if not row or row[0] is None:
        return None
    return float(row[0])


def maybe_compact_alias(
    alias: str,
    *,
    min_free_pct: float = COMPACT_FREE_PCT,
    remove_bak: bool = True,
    skip: bool = False,
    always: bool = False,
) -> int:
    """Reclaim free blocks on the production alias. 0 = ok/skipped, else compact rc."""
    if skip or os.environ.get(_SKIP_ENV):
        return 0
    pct = free_block_pct(alias)
    if not always:
        if pct is None:
            return 0
        if pct + 1e-9 < float(min_free_pct):
            return 0
    compact = _load_db_compact()
    rc = int(compact.run(alias, execute=True, drop_bak=remove_bak))
    return rc
