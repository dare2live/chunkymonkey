#!/usr/bin/env python3
"""Phase 7: SQLite → DuckDB 一次性迁移

策略: INSTALL sqlite + ATTACH READ_ONLY + CREATE TABLE AS SELECT *
每张表独立 COPY, 全量行数验证.
不迁移 qlib 相关表 (Phase 8 删除).

生成 3 个 DuckDB:
  data/smartmoney.duckdb  ← data/smartmoney.db
  data/market.duckdb      ← data/market_data.db
  data/etf.duckdb         ← data/etf.db
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb

logger = logging.getLogger("migrate")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


# qlib 表不迁移 (Phase 8 删除)
SKIP_TABLES = {
    "qlib_model_state", "qlib_predictions", "qlib_alpha158_index",
    "qlib_backtest_result", "qlib_data_state", "qlib_factor_importance",
    "qlib_etf_feature_store", "qlib_etf_label_store", "qlib_etf_predictions",
    "qlib_etf_backtest_result", "qlib_etf_model_state", "qlib_etf_param_search",
    "qlib_alpha158",
}


def list_sqlite_tables(src: str) -> list[str]:
    c = sqlite3.connect(src)
    rows = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    c.close()
    return sorted([r[0] for r in rows])


def migrate_db(src: str, dst: str, skip: set[str]) -> dict:
    if os.path.exists(dst):
        logger.warning("%s 已存在, 删除重建", dst)
        os.remove(dst)

    tables = list_sqlite_tables(src)
    logger.info("源 %s: %d 表", src, len(tables))

    con = duckdb.connect(dst)
    con.execute("INSTALL sqlite; LOAD sqlite;")
    # all_varchar=true 让 SQLite scanner 把所有列当字符串读, 避免类型冲突 (如 int 列里混 float)
    con.execute("SET GLOBAL sqlite_all_varchar=true")
    con.execute(f"ATTACH '{src}' AS src (TYPE SQLITE, READ_ONLY)")

    results = {}
    for t in tables:
        if t in skip:
            logger.info("  [skip] %s", t)
            continue
        try:
            t0 = time.time()
            con.execute(f'CREATE TABLE "{t}" AS SELECT * FROM src."{t}"')
            n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            dt = time.time() - t0
            results[t] = n
            logger.info("  ✓ %-50s rows=%-10d %.1fs", t, n, dt)
        except Exception as e:
            logger.error("  ✗ %s: %s", t, e)
            results[t] = None

    con.execute("DETACH src")
    # 建议索引
    _create_indexes(con, dst)
    con.close()
    return results


def _create_indexes(con, dst_path: str):
    """为高频查询列建 DuckDB 索引 (DuckDB 有 min-max zone map 自带, 索引次要)"""
    if dst_path.endswith('smartmoney.duckdb'):
        # 事件表和面板表最常用
        queries = [
            "CREATE INDEX IF NOT EXISTS idx_fp_code_date ON fact_feature_panel(stock_code, date)",
            "CREATE INDEX IF NOT EXISTS idx_fp_date ON fact_feature_panel(date)",
            "CREATE INDEX IF NOT EXISTS idx_fie_code ON fact_institution_event(stock_code)",
            "CREATE INDEX IF NOT EXISTS idx_fete_code ON fact_executive_trade_event(stock_code)",
            "CREATE INDEX IF NOT EXISTS idx_ffq_code ON fact_fundamental_quarterly(stock_code)",
        ]
    elif dst_path.endswith('market.duckdb'):
        queries = [
            "CREATE INDEX IF NOT EXISTS idx_pk_code_date ON price_kline_tdxhub(code, date)",
            "CREATE INDEX IF NOT EXISTS idx_pk_date ON price_kline_tdxhub(date)",
        ]
    elif dst_path.endswith('etf.duckdb'):
        queries = [
            "CREATE INDEX IF NOT EXISTS idx_epk_code_date ON etf_price_kline(code, date)",
        ]
    else:
        return
    for q in queries:
        try:
            con.execute(q)
        except Exception as e:
            logger.debug("index skip: %s", e)


def verify(src: str, dst: str, skip: set[str]) -> bool:
    logger.info("=== 验证 %s ↔ %s ===", src, dst)
    sql_tables = set(list_sqlite_tables(src)) - skip
    con = duckdb.connect(dst)
    duck_tables = set(r[0] for r in con.execute("SHOW TABLES").fetchall())
    missing = sql_tables - duck_tables
    if missing:
        logger.error("DuckDB 缺失: %s", missing)
        con.close()
        return False
    sql_con = sqlite3.connect(src)
    mismatches = []
    for t in sql_tables:
        sn = sql_con.execute(f"SELECT COUNT(*) FROM \"{t}\"").fetchone()[0]
        dn = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        if sn != dn:
            mismatches.append((t, sn, dn))
    sql_con.close()
    con.close()
    if mismatches:
        logger.error("行数不一致: %s", mismatches)
        return False
    logger.info("✓ %d 张表行数全一致", len(sql_tables))
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', choices=['smart', 'market', 'etf', 'all'], default='all')
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()

    data_dir = Path(__file__).resolve().parent.parent.parent / "data"
    plans = []
    if args.only in ('smart', 'all'):
        plans.append((str(data_dir / 'smartmoney.db'), str(data_dir / 'smartmoney.duckdb')))
    if args.only in ('market', 'all'):
        plans.append((str(data_dir / 'market_data.db'), str(data_dir / 'market.duckdb')))
    if args.only in ('etf', 'all'):
        plans.append((str(data_dir / 'etf.db'), str(data_dir / 'etf.duckdb')))

    t_all = time.time()
    for src, dst in plans:
        if not os.path.exists(src):
            logger.warning("源 %s 不存在, 跳过", src)
            continue
        logger.info("━" * 60)
        logger.info("迁移: %s → %s", src, dst)
        logger.info("━" * 60)
        if not args.verify_only:
            migrate_db(src, dst, SKIP_TABLES)
        ok = verify(src, dst, SKIP_TABLES)
        if not ok:
            logger.error("验证失败, 终止")
            sys.exit(1)

    logger.info("━" * 60)
    logger.info("全部迁移+验证完成, 总耗时 %.1f min", (time.time() - t_all) / 60)


if __name__ == "__main__":
    main()
