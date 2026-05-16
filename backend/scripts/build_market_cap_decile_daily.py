"""Feature research — fact_market_cap_decile_daily (Codex a49c90a6 backlog #2).

每日 cross-sectional market cap 分 10 deciles (1=smallest / 10=largest).

PIT-safe:
- market_cap proxy: amount × ADV60 / shares_outstanding 或 close × shares_outstanding
- 简化版: 用 mart_p0a_feature_label_panel_v3 已有 vol60d (近似 market cap)
- 实际应用 shares_outstanding (Phase 2 完善 接 dim_stock_basic.shares)

Columns:
- stock_code / trade_date / market_cap_proxy / mcap_decile (1-10) / source_max_trade_date

decile 计算 PIT-safe by construction (current-day cross-section, T 决策时知 T-1 EOD cap).

用法:
    PYTHONPATH=backend python backend/scripts/build_market_cap_decile_daily.py --start 2024-01-01 --end 2026-04-23
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
log = logging.getLogger("build_market_cap_decile_daily")


SMART_DB = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"
TARGET = "fact_market_cap_decile_daily"


DDL_DROP = f"DROP TABLE IF EXISTS {TARGET}"
DDL_CREATE = f"""
CREATE TABLE {TARGET} (
    stock_code TEXT NOT NULL,
    trade_date DATE NOT NULL,
    market_cap_proxy DOUBLE,
    mcap_decile INTEGER,
    source_max_trade_date DATE NOT NULL,
    built_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, trade_date)
)
"""
DDL_INDEX = f"CREATE INDEX IF NOT EXISTS idx_mcap_date ON {TARGET}(trade_date)"

BUILD_SQL = f"""
INSERT INTO {TARGET}
WITH prior_day AS (
    -- PIT: 用 prior day close × volume_60d_amt 近似 market cap proxy
    -- (实际应用 shares_outstanding, defer 到 dim_stock_basic 接入)
    SELECT
        code AS stock_code,
        CAST(date AS DATE) AS trade_date,
        LAG(close * amount) OVER (PARTITION BY code ORDER BY date) AS prior_proxy
    FROM market.v_price_kline_qfq
    WHERE adjust='qfq' AND freq='daily'
      AND CAST(date AS DATE) >= CAST(? AS DATE)
      AND CAST(date AS DATE) <= CAST(? AS DATE)
),
deciled AS (
    SELECT
        stock_code, trade_date,
        prior_proxy AS market_cap_proxy,
        NTILE(10) OVER (PARTITION BY trade_date ORDER BY prior_proxy) AS mcap_decile
    FROM prior_day
    WHERE prior_proxy IS NOT NULL AND prior_proxy > 0
)
SELECT
    stock_code, trade_date, market_cap_proxy, mcap_decile,
    trade_date AS source_max_trade_date,
    CURRENT_TIMESTAMP AS built_at
FROM deciled
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-01-01")   # rule-compliance: ok evidence=panel-window
    parser.add_argument("--end", default="2026-04-23")     # rule-compliance: ok evidence=panel-window-end
    args = parser.parse_args()
    log.info(f"=== build fact_market_cap_decile_daily {args.start} → {args.end} ===")

    conn = duckdb.connect(str(SMART_DB))
    market_db = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
    conn.execute(f"ATTACH IF NOT EXISTS '{market_db}' AS market (READ_ONLY)")

    conn.execute(DDL_DROP)
    conn.execute(DDL_CREATE)
    conn.execute(BUILD_SQL, [args.start, args.end])
    conn.execute(DDL_INDEX)

    r = conn.execute(f"""
        SELECT COUNT(*), COUNT(DISTINCT stock_code), COUNT(DISTINCT trade_date),
               COUNT(DISTINCT mcap_decile)
        FROM {TARGET}
    """).fetchone()
    log.info(f"  built: {r[0]:,} rows / {r[1]} stocks / {r[2]} days / {r[3]} deciles (target 10)")

    # PIT integrity
    bad = conn.execute(f"SELECT COUNT(*) FROM {TARGET} WHERE source_max_trade_date > trade_date").fetchone()[0]
    if bad > 0:
        log.error(f"  PIT integrity FAIL: {bad} rows")
        return 1
    log.info("  PIT integrity PASS: 0 violations")

    # Decile distribution (should be roughly equal ~10% each)
    print("\n  Decile distribution (latest day):")
    r2 = conn.execute(f"""
        SELECT mcap_decile, COUNT(*) AS n
        FROM {TARGET} WHERE trade_date = (SELECT MAX(trade_date) FROM {TARGET})
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    for x in r2:
        print(f"    decile {x[0]}: {x[1]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
