"""Shared helpers for Scheme 7 institution-follow modules."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from services.duck_adapter import connect


REPO_ROOT = Path(__file__).resolve().parents[4]
DATA_DIR = REPO_ROOT / "data"
SMART_DB = DATA_DIR / "smartmoney.duckdb"
MARKET_DB = DATA_DIR / "market.duckdb"


def open_smart_conn(read_only: bool = True):
    """Open smartmoney.duckdb through the project DuckDB adapter."""
    return connect(str(SMART_DB), read_only=read_only)


def normalize_signal_date(signal_date) -> str:
    """Return signal_date in canonical YYYY-MM-DD form."""
    return pd.to_datetime(signal_date).strftime("%Y-%m-%d")


def date_expr(column: str) -> str:
    """DuckDB expression normalizing either YYYY-MM-DD or YYYYMMDD strings."""
    casted = f"CAST({column} AS VARCHAR)"
    return (
        f"COALESCE("
        f"try_strptime({casted}, '%Y-%m-%d')::DATE, "
        f"try_strptime({casted}, '%Y%m%d')::DATE"
        f")"
    )


def fetch_df(conn, sql: str, params: Sequence | None = None) -> pd.DataFrame:
    """Execute SQL on either DuckConn or raw DuckDB connection and return DataFrame."""
    cur = conn.execute(sql, list(params or []))
    rows = cur.fetchall()
    cols = [d[0] for d in (cur.description or [])]
    return pd.DataFrame([tuple(r) for r in rows], columns=cols)


def table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*)
          FROM information_schema.tables
         WHERE table_name = ?
        """,
        [table_name.split(".")[-1]],
    ).fetchone()
    return bool(row and row[0])


def table_columns(conn, table_name: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_name = ?
        """,
        [table_name.split(".")[-1]],
    ).fetchall()
    return {str(r[0]) for r in rows}


def ensure_market_attached(conn, market_db_path: Path | str = MARKET_DB, alias: str = "market") -> None:
    """Attach market.duckdb read-only if this connection has not attached it yet."""
    try:
        rows = conn.execute("SELECT database_name FROM duckdb_databases()").fetchall()
        if alias in {str(r[0]) for r in rows}:
            return
    except Exception:
        return
    try:
        conn.execute(f"ATTACH '{Path(market_db_path)}' AS {alias} (READ_ONLY)")
    except Exception:
        # ATTACH may fail in tiny unit-test connections; callers can pass an unqualified price_table.
        return


def universe_clause(universe: Iterable[str] | None, column: str = "stock_code") -> tuple[str, list[str]]:
    if universe is None:
        return "", []
    codes = [str(c) for c in universe]
    if not codes:
        return " AND 1 = 0", []
    placeholders = ",".join("?" for _ in codes)
    return f" AND {column} IN ({placeholders})", codes


def empty_features(universe: Iterable[str] | None, columns: Sequence[str]) -> pd.DataFrame:
    codes = [] if universe is None else [str(c) for c in universe]
    frame = pd.DataFrame({"stock_code": codes})
    for col in columns:
        frame[col] = 0.0
    return frame


def complete_universe(
    df: pd.DataFrame,
    universe: Iterable[str] | None,
    columns: Sequence[str],
) -> pd.DataFrame:
    """Ensure all universe stocks exist and all feature columns are numeric-filled."""
    if universe is not None:
        base = pd.DataFrame({"stock_code": [str(c) for c in universe]})
        out = base.merge(df, on="stock_code", how="left")
    else:
        out = df.copy()
        if "stock_code" not in out.columns:
            out["stock_code"] = pd.Series(dtype="object")
    for col in columns:
        if col not in out.columns:
            out[col] = 0.0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
    return out[["stock_code", *columns]]


def zscore(values: pd.Series) -> pd.Series:
    vals = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    std = float(vals.std(ddof=0))
    if std <= 1e-12:
        return pd.Series(np.zeros(len(vals)), index=vals.index)
    return (vals - float(vals.mean())) / std
