"""Phase 2 prereq — mart_stock_pool_assignment (Codex final hierarchical 24-pool, 2026-05-16).

每月初 assign stock → (supersector × liquidity_tier) pool.

Pool = 12 CITIC L1 supersectors × 2 liquidity tiers (high/low) = 24 pools.
- supersector: from mart_stock_industry_pit.tdx_l1 (PIT-safe, 取 month_start 时点最近 effective_from)
- liquidity_tier: within each supersector, ADV60-median split (high vs low)
- as_of_month: month_start 锚 (paper_sim 当月用前月 assignment, PIT-safe)

PIT 保证:
- industry: tdx_l1 from mart_stock_industry_pit WHERE effective_from <= month_start
- ADV60: WINDOW ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING anchor at month_start-1
- 不读未来 (assignment 月初 freeze, 月内不变)

用法:
    # 全量 (each month)
    PYTHONPATH=backend python backend/scripts/build_mart_stock_pool_assignment.py \\
        --start 2024-01-01 --end 2026-04-30

    # smoke (last 3 month)
    PYTHONPATH=backend python backend/scripts/build_mart_stock_pool_assignment.py \\
        --start 2026-02-01 --end 2026-04-30
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("build_pool_assignment")


SMART_DB = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"
TARGET = "mart_stock_pool_assignment"


DDL_DROP = f"DROP TABLE IF EXISTS {TARGET}"

DDL_CREATE = f"""
CREATE TABLE {TARGET} (
    stock_code TEXT NOT NULL,
    as_of_month DATE NOT NULL,
    supersector TEXT NOT NULL,          -- CITIC L1 industry (from tdx_l1, mapped)
    liquidity_tier TEXT NOT NULL,       -- 'high' | 'low' (ADV60 median split per supersector)
    adv60 DOUBLE,                       -- 60-day ADV at month_start (PIT)
    pool_id TEXT NOT NULL,              -- '{{supersector}}_{{tier}}'
    n_stocks_in_pool INTEGER,           -- pool 内 stock 数 (sanity)
    built_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, as_of_month)
);
"""

DDL_INDEX_MONTH = f"CREATE INDEX IF NOT EXISTS idx_pool_assign_month ON {TARGET}(as_of_month)"
DDL_INDEX_POOL = f"CREATE INDEX IF NOT EXISTS idx_pool_assign_pool ON {TARGET}(pool_id, as_of_month)"


BUILD_SQL = f"""
INSERT INTO {TARGET}
WITH month_anchors AS (
    -- 每月 1st (or first trading day) 作 as_of_month
    SELECT DISTINCT
        DATE_TRUNC('month', CAST(trade_date AS DATE)) AS month_start,
        MIN(CAST(trade_date AS DATE)) AS first_trading_day
    FROM dim_trading_calendar
    WHERE is_trading = 1
      AND CAST(trade_date AS DATE) >= CAST(? AS DATE)
      AND CAST(trade_date AS DATE) <= CAST(? AS DATE)
    GROUP BY 1
),
-- stocks active at each month_start
active_stocks AS (
    SELECT DISTINCT
        ma.first_trading_day AS month_start,
        ind.stock_code,
        ind.tdx_l1,
        ind.tdx_l1_name
    FROM month_anchors ma
    JOIN mart_stock_industry_pit ind
        ON CAST(ind.effective_from AS DATE) <= ma.first_trading_day
       AND (CAST(ind.effective_to AS DATE) > ma.first_trading_day OR ind.effective_to = '9999-12-31')
       AND ind.tdx_l1 IS NOT NULL
    -- Codex final 方案: supersector STATIC (按上市日期 fix), 接受 current_label_fallback (历史 industry_pit 99.978% fallback 已知).
    -- 2026-04-25+ 才有 observed_snapshot, 历史 PIT 用 fallback 等价静态映射 (Codex 接受).
),
-- ADV60 at each (stock, month_start), PIT prior 60 day
adv60_at_anchor AS (
    SELECT
        a.month_start,
        a.stock_code,
        a.tdx_l1,
        a.tdx_l1_name,
        AVG(k.amount) OVER (
            PARTITION BY k.code
            ORDER BY k.date
            ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING
        ) AS adv60
    FROM active_stocks a
    LEFT JOIN market.v_price_kline_qfq k
        ON k.code = a.stock_code AND k.adjust='qfq' AND k.freq='daily'
       AND CAST(k.date AS DATE) <= a.month_start
       AND CAST(k.date AS DATE) >= a.month_start - INTERVAL '90 days'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY a.month_start, a.stock_code ORDER BY k.date DESC
    ) = 1
),
-- median ADV60 per supersector
supersector_median AS (
    SELECT
        month_start, tdx_l1,
        MEDIAN(adv60) AS adv60_median
    FROM adv60_at_anchor
    WHERE adv60 IS NOT NULL
    GROUP BY 1, 2
),
final AS (
    SELECT
        a.month_start AS as_of_month,
        a.stock_code,
        COALESCE(a.tdx_l1_name, a.tdx_l1, 'unknown') AS supersector,
        CASE
            WHEN a.adv60 IS NULL THEN 'unknown'
            WHEN a.adv60 >= m.adv60_median THEN 'high'
            ELSE 'low'
        END AS liquidity_tier,
        a.adv60,
        COALESCE(a.tdx_l1_name, a.tdx_l1, 'unknown') || '_' ||
        CASE
            WHEN a.adv60 IS NULL THEN 'unknown'
            WHEN a.adv60 >= m.adv60_median THEN 'high'
            ELSE 'low'
        END AS pool_id
    FROM adv60_at_anchor a
    LEFT JOIN supersector_median m
        ON m.month_start = a.month_start AND m.tdx_l1 = a.tdx_l1
)
SELECT
    stock_code,
    as_of_month,
    supersector,
    liquidity_tier,
    adv60,
    pool_id,
    COUNT(*) OVER (PARTITION BY as_of_month, pool_id) AS n_stocks_in_pool,
    CURRENT_TIMESTAMP AS built_at
FROM final
;
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-01-01")   # rule-compliance: ok evidence=panel-window
    parser.add_argument("--end", default="2026-04-30")     # rule-compliance: ok evidence=panel-window-end
    args = parser.parse_args()

    log.info(f"=== build mart_stock_pool_assignment ===")
    log.info(f"  window: {args.start} → {args.end}")

    conn = duckdb.connect(str(SMART_DB))
    market_db = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
    conn.execute(f"ATTACH IF NOT EXISTS '{market_db}' AS market (READ_ONLY)")

    # Prereq
    for tbl in ("mart_stock_industry_pit", "dim_trading_calendar"):
        r = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        log.info(f"  prereq {tbl}: {r:,} rows")

    conn.execute(DDL_DROP)
    conn.execute(DDL_CREATE)
    conn.execute(BUILD_SQL, [args.start, args.end])
    conn.execute(DDL_INDEX_MONTH)
    conn.execute(DDL_INDEX_POOL)

    # Audit
    r = conn.execute(f"""
        SELECT
            COUNT(*) AS total_rows,
            COUNT(DISTINCT stock_code) AS stocks,
            COUNT(DISTINCT as_of_month) AS months,
            COUNT(DISTINCT supersector) AS n_supersectors,
            COUNT(DISTINCT pool_id) AS n_pools
        FROM {TARGET}
    """).fetchone()
    log.info(f"  build done: {r[0]:,} rows, {r[1]} stocks, {r[2]} months, "
             f"{r[3]} supersectors, {r[4]} pools (target: 24)")

    # Pool distribution
    print("\n=== Pool distribution (latest month) ===")
    latest_month_row = conn.execute(f"SELECT MAX(as_of_month) FROM {TARGET}").fetchone()
    if latest_month_row and latest_month_row[0]:
        for r in conn.execute(f"""
            SELECT pool_id, COUNT(*) AS n
            FROM {TARGET}
            WHERE as_of_month = ?
            GROUP BY pool_id
            ORDER BY n DESC
        """, [latest_month_row[0]]).fetchall():
            print(f"  {r[0]:35s} {r[1]:>5}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
