#!/usr/bin/env python3
"""#53 followup cleanup: fact_top10_holder_period 239 dup row 物理清理.

Codex round 14 (bl283si79) verdict 第 1 优先级:
> 数据完整性 first, 239 dup 会污染 validation + 实盘 records.

历史: commit cfb35bc3 修了 ingest_holders_tdxhub.py batch dedup
(防 INSERT OR REPLACE batch 内 PK dup trigger DuckDB INTERNAL FATAL).
但已存 239 dup rows 物理残留, 此脚本清理.

根因: UNIQUE constraint 含 share_class, share_class IS NULL 时 NULL distinct
语义不 enforce dedup → 239 dup 累积. 修法 DROP+recreate WITHOUT UNIQUE,
ingest dedup 防新增 (cfb35bc3).

执行:
    PYTHONPATH=backend python backend/scripts/cleanup_holder_dup.py

幂等: 多次跑安全, 已 0 dup 时 skip 实际 DROP/recreate.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import duckdb

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("cleanup_holder_dup")

SMART_DB = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"


def main() -> int:
    conn = duckdb.connect(str(SMART_DB))
    n_before = conn.execute("SELECT COUNT(*) FROM fact_top10_holder_period").fetchone()[0]
    log.info(f"fact_top10_holder_period before: {n_before:,} rows")

    # 检查 dup
    r = conn.execute("""
        WITH d AS (
          SELECT stock_code, report_date, holder_set, source, is_exit_row,
                 holder_rank, row_seq, COALESCE(share_class, '_NULL_') sc,
                 COUNT(*) cnt
          FROM fact_top10_holder_period
          GROUP BY 1,2,3,4,5,6,7,8
          HAVING COUNT(*) > 1
        )
        SELECT COUNT(*), COALESCE(SUM(cnt-1), 0) FROM d
    """).fetchone()
    dup_groups, extra_rows = r[0], r[1]
    log.info(f"dup groups: {dup_groups}, extra rows: {extra_rows}")

    if dup_groups == 0:
        log.info("0 dup found, skip cleanup (already clean)")
        return 0

    # DROP + recreate WITHOUT UNIQUE constraint (NULL distinct 语义 buggy, ingest dedup 防新增)
    conn.execute("""
        CREATE OR REPLACE TABLE _tmp_holder_clean AS
        SELECT DISTINCT ON (stock_code, report_date, holder_set, source, is_exit_row,
                            holder_rank, row_seq,
                            COALESCE(share_class, '_NULL_')) *
        FROM fact_top10_holder_period
        ORDER BY stock_code, report_date, holder_set, source, is_exit_row,
                 holder_rank, row_seq, COALESCE(share_class, '_NULL_'),
                 fetched_at DESC NULLS LAST, created_at DESC NULLS LAST
    """)
    n_tmp = conn.execute("SELECT COUNT(*) FROM _tmp_holder_clean").fetchone()[0]
    log.info(f"_tmp_holder_clean: {n_tmp:,} cleaned rows")

    conn.execute("DROP TABLE fact_top10_holder_period")
    conn.execute("CREATE TABLE fact_top10_holder_period AS SELECT * FROM _tmp_holder_clean")
    n_after = conn.execute("SELECT COUNT(*) FROM fact_top10_holder_period").fetchone()[0]
    log.info(f"after: {n_after:,} rows (deleted {n_before - n_after})")

    # 重建 5 indexes (跟 db.py CREATE INDEX 语句一致)
    indexes = [
        ("idx_fact_hp_stock_date", "fact_top10_holder_period(stock_code, report_date DESC)"),
        ("idx_fact_hp_name", "fact_top10_holder_period(holder_name)"),
        ("idx_fact_hp_name_norm", "fact_top10_holder_period(holder_name_norm)"),
        ("idx_fact_hp_eff_date", "fact_top10_holder_period(effective_date)"),
        ("idx_fact_hp_set_class", "fact_top10_holder_period(holder_set, share_class)"),
    ]
    for name, spec in indexes:
        conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {spec}")
    log.info(f"5 indexes recreated")

    conn.execute("DROP TABLE _tmp_holder_clean")

    # verify 0 dup
    r = conn.execute("""
        WITH d AS (
          SELECT stock_code, report_date, holder_set, source, is_exit_row,
                 holder_rank, row_seq, COALESCE(share_class, '_NULL_') sc,
                 COUNT(*) cnt
          FROM fact_top10_holder_period
          GROUP BY 1,2,3,4,5,6,7,8
          HAVING COUNT(*) > 1
        )
        SELECT COUNT(*) FROM d
    """).fetchone()
    log.info(f"verify: {r[0]} dup groups remaining")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
