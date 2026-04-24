"""DuckDB 分析引擎

定位: 列式 OLAP 查询引擎, 读挂载的 SQLite 作 source of truth. 零存储迁移.
用途: feature_panel 构建 / 训练数据加载 / topK 排名 / cross-source ASOF JOIN.
不变: SQLite 原始存储, pandas API 解析, LightGBM 模型层.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd

logger = logging.getLogger("cm-api")

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
SMART_DB = str(_DATA_DIR / "smartmoney.db")
MARKET_DB = str(_DATA_DIR / "market_data.db")

_con: Optional[duckdb.DuckDBPyConnection] = None


def get_duck() -> duckdb.DuckDBPyConnection:
    """获取 DuckDB 连接 (进程级单例). ATTACH 两个 SQLite 作只读视图."""
    global _con
    if _con is None:
        _con = duckdb.connect(":memory:")
        _con.execute("INSTALL sqlite; LOAD sqlite;")
        # 别名注册,后续 SQL 可直接 sqlite_scan 引用
        _con.execute(f"ATTACH '{SMART_DB}' AS smart (TYPE SQLITE)")
        _con.execute(f"ATTACH '{MARKET_DB}' AS market (TYPE SQLITE)")
        logger.info("[analytics] DuckDB + SQLite ATTACH 完成")
    return _con


def sql(query: str, params: tuple = ()) -> pd.DataFrame:
    """简便执行, 返回 pandas DataFrame."""
    con = get_duck()
    return con.execute(query, params).df()


def reattach():
    """如需重建连接 (例如 schema 变化后)"""
    global _con
    if _con is not None:
        _con.close()
        _con = None
    return get_duck()
