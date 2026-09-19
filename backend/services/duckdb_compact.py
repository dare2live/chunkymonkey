"""Compact production DuckDB aliases after DROP/rebuild writers.

Writers must close connections first. ``db_compact.run`` always targets the
``database_manifest`` production path — tests that rebuilt a redirected file
must pass a monkeypatched ``_db_path``.

cut_db_compaction (2026-09-19): the previous per-writer hook
``maybe_compact_alias`` (and its three call sites in
``build_price_kline_qfq_tushare.py`` / ``institution_profile.py`` /
``rally_gt.py``) is retired — compaction is now a single daily-update step,
``services.pipeline.store.compact_bloated_databases``, driven by
``backend/config/db_compaction.yaml`` (databases + trigger threshold). This
module keeps only the mechanism the daily step needs: measuring free-block
percentage and running one compaction attempt (:func:`compact_if_bloated`).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from services.duck_adapter import connect as duck_connect

REPO = Path(__file__).resolve().parents[1].parent
_COMPACT_SCRIPT = REPO / "backend" / "scripts" / "db_compact.py"


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


def compact_if_bloated(
    alias: str,
    *,
    trigger_free_block_pct: float | None = None,
    drop_bak: bool = True,
) -> dict[str, Any]:
    """Measure ``alias`` and compact it once if bloated. Daily store-step hook.

    ``trigger_free_block_pct`` defaults to ``None``, which reads
    ``backend/config/db_compaction.yaml`` fresh on every call (not cached at
    import time as a module constant) — a test that reloads the config with a
    different threshold must not see a stale value.

    Missing db file -> ``attempted=False``, ``free_pct_before=None`` (red
    line 3: 缺失只能传播为缺失; a missing file is neither "needs compaction"
    nor "already compacted", it is unknown).

    Returns a typed record (never raises on a normal compact failure — the
    caller decides how to treat a non-zero ``returncode``):
    ``{alias, free_pct_before, trigger_free_block_pct, attempted, returncode,
    size_before_bytes, size_after_bytes}``.
    """
    if trigger_free_block_pct is None:
        from services.db_compaction_rules import load_db_compaction_config

        trigger_free_block_pct = load_db_compaction_config().trigger_free_block_pct
    trigger = float(trigger_free_block_pct)

    compact = _load_db_compact()
    path = compact._db_path(alias)
    if not path.exists():
        return {
            "alias": alias,
            "free_pct_before": None,
            "trigger_free_block_pct": trigger,
            "attempted": False,
            "returncode": None,
            "size_before_bytes": None,
            "size_after_bytes": None,
        }

    pct = free_block_pct(alias)
    size_before = path.stat().st_size
    if pct is None or pct + 1e-9 < trigger:
        return {
            "alias": alias,
            "free_pct_before": pct,
            "trigger_free_block_pct": trigger,
            "attempted": False,
            "returncode": None,
            "size_before_bytes": size_before,
            "size_after_bytes": size_before,
        }

    rc = int(compact.run(alias, execute=True, drop_bak=drop_bak))
    size_after = path.stat().st_size if path.exists() else None
    return {
        "alias": alias,
        "free_pct_before": pct,
        "trigger_free_block_pct": trigger,
        "attempted": True,
        "returncode": rc,
        "size_before_bytes": size_before,
        "size_after_bytes": size_after,
    }
