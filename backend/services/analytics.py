"""DuckDB 分析引擎

定位: 列式 OLAP 查询引擎, 直接读取 DuckDB 主库并挂载 market/etf 库.
用途: feature_panel 构建 / 训练数据加载 / topK 排名 / cross-source ASOF JOIN.
不变: pandas API 解析与 LightGBM 模型层.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd

logger = logging.getLogger("cm-api")

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
SMART_DB = str(_DATA_DIR / "smartmoney.duckdb")
MARKET_DB = str(_DATA_DIR / "market.duckdb")
ETF_DB = str(_DATA_DIR / "etf.duckdb")

_con: Optional[duckdb.DuckDBPyConnection] = None


def get_duck(writable: bool = False) -> duckdb.DuckDBPyConnection:
    """获取 DuckDB 分析连接 (进程级单例).

    smartmoney.duckdb 作为主库打开 (可写则 writable=True), 再 ATTACH market/etf 只读.
    注意: 同进程不要和 db.get_conn() 并存 — 调用方二选一.
    """
    global _con
    if _con is None:
        _con = duckdb.connect(SMART_DB, read_only=not writable)
        # 别名 smart 指向自身 (通过 SHOW DATABASES 看到 database 名就是文件名去后缀)
        # 为让 SQL 里统一 `smart.table` 能用, 建 alias 视图
        try:
            _con.execute(f"ATTACH '{MARKET_DB}' AS market (READ_ONLY)")
        except Exception as e:
            logger.warning("attach market: %s", e)
        try:
            _con.execute(f"ATTACH '{ETF_DB}' AS etf (READ_ONLY)")
        except Exception as e:
            logger.warning("attach etf: %s", e)
        # smart alias: DuckDB 主库默认 catalog name = 文件名 (smartmoney).
        # 为脚本里 `smart.xxx` 能用, 我们不 ATTACH 一次自身 (会报错), 而用 SQL 创建 CREATE SCHEMA smart AS ALIAS OF smartmoney
        # DuckDB 没有 SCHEMA AS ALIAS, 退而用最简: 直接 `smartmoney.xxx`
        # 或 ATTACH 一次 smartmoney.duckdb 作 smart 别名 (不同文件句柄)
        logger.info("[analytics] DuckDB 主库 smartmoney + ATTACH market/etf 完成")
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
