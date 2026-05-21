"""市场感知模块共享工具.

2026-05-21 拆出 (用户 audit push back: 6 engines 都 import regime_engine 工具枢纽 违反水平分层).
- `_table_exists` / `_fetchall` / `_fetchone` / `_to_date` / `_attach_market_if_available`
- 这些 helpers 不属于 regime_engine 专属逻辑, 是市场感知整个 sub-package 的工具.
- 保留 regime_engine 内部 re-export, 向后兼容现有 `from .regime_engine import _table_exists` 调用.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any


logger = logging.getLogger("market_perception.utils")

REPO_ROOT = Path(__file__).resolve().parents[3]
MARKET_DB = REPO_ROOT / "data" / "market.duckdb"


def _sql_path(path: Path) -> str:
    """rule-compliance: ok evidence=ddl-string-escape  Convert path to SQL-safe string."""
    return str(path).replace("'", "''")


def _attach_market_if_available(conn) -> None:
    """Attach market.duckdb as READ_ONLY alias 'market' if not already present and exists.

    Idempotent — already-attached or already-having-tables case returns without action.
    """
    if _table_exists(conn, "mart_index_daily") and _table_exists(conn, "fact_stock_kline_daily"):
        return
    if not MARKET_DB.exists():
        return
    try:
        conn.execute(f"ATTACH IF NOT EXISTS '{_sql_path(MARKET_DB)}' AS market (READ_ONLY)")
    except Exception as exc:  # rule-compliance: ok evidence=defensive-attach-may-double-bind
        logger.warning("market.duckdb attach failed: %s", exc)


def _table_exists(conn, table: str) -> bool:
    row = _fetchone(
        conn,
        "SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_name = ?",
        [table],
    )
    return bool(row and int(row["n"]) > 0)


def _fetchone(conn, sql: str, params: list[Any] | None = None):
    cur = conn.execute(sql, params or [])
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description] if getattr(cur, "description", None) else []
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return dict(zip(cols, row))


def _fetchall(conn, sql: str, params: list[Any] | None = None):
    cur = conn.execute(sql, params or [])
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description] if getattr(cur, "description", None) else []
    out = []
    for row in rows:
        if hasattr(row, "keys"):
            out.append({k: row[k] for k in row.keys()})
        else:
            out.append(dict(zip(cols, row)))
    return out


def _to_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _columns(conn, table: str) -> set[str]:
    return {r["column_name"] for r in _fetchall(conn, f"DESCRIBE {table}")}


def _first_existing(cols: set[str], names: list[str], required: bool = True) -> str | None:
    for name in names:
        if name in cols:
            return name
    if required:
        raise ValueError(f"required columns missing; expected one of {names}, got {sorted(cols)}")
    return None
