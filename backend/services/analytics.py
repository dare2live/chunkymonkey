"""DuckDB 分析引擎

定位: 列式 OLAP 查询引擎, 直接读取 DuckDB 主库并挂载 market/etf 库.
用途: feature_panel 构建 / 训练数据加载 / topK 排名 / cross-source ASOF JOIN.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path

import duckdb

logger = logging.getLogger("cm-api")

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
SMART_DB = str(_DATA_DIR / "smartmoney.duckdb")
MARKET_DB = str(_DATA_DIR / "market.duckdb")
ETF_DB = str(_DATA_DIR / "etf.duckdb")


def _open_duck(writable: bool = False) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(SMART_DB, read_only=not writable)
    try:
        con.execute(f"ATTACH IF NOT EXISTS '{MARKET_DB}' AS market (READ_ONLY)")
    except Exception as e:
        logger.warning("attach market: %s", e)
    try:
        con.execute(f"ATTACH IF NOT EXISTS '{ETF_DB}' AS etf (READ_ONLY)")
    except Exception as e:
        logger.warning("attach etf: %s", e)
    logger.info("[analytics] DuckDB 主库 smartmoney + ATTACH market/etf 完成")
    return con


@contextmanager
def duck_connection(writable: bool = False):
    """Managed DuckDB analytical connection with deterministic close."""

    con = _open_duck(writable=writable)
    try:
        yield con
    finally:
        con.close()


def get_duck(writable: bool = False) -> duckdb.DuckDBPyConnection:
    """Legacy helper returning a new analytical connection.

    New production code should prefer ``duck_connection`` so the connection is
    closed deterministically.
    """

    return _open_duck(writable=writable)


def sql(query: str, params: tuple = ()) -> list[dict]:
    """简便执行, 返回 records."""
    con = get_duck()
    try:
        cursor = con.execute(query, params)
        if cursor.description is None:
            return []
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        close = getattr(con, "close", None)
        if callable(close):
            close()


def reattach():
    """Backward-compatible alias for opening a fresh managed-style connection."""
    return get_duck()
