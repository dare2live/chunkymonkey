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
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.db_connection import DB_PATH
from services.duck_adapter import connect


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("build_market_cap_decile_daily")


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
DDL_CREATE_IF_NOT_EXISTS = DDL_CREATE.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1)
DDL_INDEX = f"CREATE INDEX IF NOT EXISTS idx_mcap_date ON {TARGET}(trade_date)"

BUILD_SQL = f"""
INSERT INTO {TARGET}
WITH trading_days AS (
    SELECT CAST(trade_date AS DATE) AS trade_date
      FROM dim_trading_calendar
     WHERE is_trading = 1
       AND CAST(trade_date AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
),
prior_day AS (
    -- PIT: 用 prior day close × volume_60d_amt 近似 market cap proxy
    -- (实际应用 shares_outstanding, defer 到 dim_stock_basic 接入)
    SELECT
        px.code AS stock_code,
        CAST(px.date AS DATE) AS trade_date,
        LAG(px.close * px.amount) OVER (PARTITION BY px.code ORDER BY px.date) AS prior_proxy
    FROM market.v_price_kline_qfq px
    JOIN dim_trading_calendar cal
      ON CAST(cal.trade_date AS DATE) = CAST(px.date AS DATE)
     AND cal.is_trading = 1
    WHERE px.adjust='qfq' AND px.freq='daily'
      AND CAST(px.date AS DATE) >= CAST(? AS DATE) - INTERVAL '21 days'
      AND CAST(px.date AS DATE) <= CAST(? AS DATE)
      AND regexp_matches(px.code, '^(00|30|60|68)[0-9]{{4}}$')
      AND COALESCE(px.source_name, '') NOT LIKE 'tdxhub_index%'
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
JOIN trading_days USING (trade_date)
"""


def _parse_day(value: str) -> date:
    return date.fromisoformat(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-01-01")   # rule-compliance: ok evidence=panel-window
    parser.add_argument("--end", default="2026-04-23")     # rule-compliance: ok evidence=panel-window-end
    parser.add_argument("--incremental", action="store_true",
                        help="只重算 start/end 切片，不删除历史全表")
    args = parser.parse_args()
    requested_start = _parse_day(args.start)
    requested_end = _parse_day(args.end)
    if requested_start > requested_end:
        raise ValueError(f"start {requested_start} > end {requested_end}")
    if requested_end >= date.today():
        raise ValueError(f"end {requested_end} is today/future; PIT requires end < today")

    log.info(f"=== build fact_market_cap_decile_daily {requested_start} -> {requested_end} ===")

    market_db = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
    with connect(str(DB_PATH), timeout=300) as conn:
        conn.execute(f"ATTACH IF NOT EXISTS '{market_db}' AS market (READ_ONLY)")
        source_max = conn.execute(
            """
            SELECT MAX(CAST(date AS DATE))
              FROM market.v_price_kline_qfq
             WHERE adjust='qfq' AND freq='daily'
               AND regexp_matches(code, '^(00|30|60|68)[0-9]{4}$')
               AND COALESCE(source_name, '') NOT LIKE 'tdxhub_index%'
            """
        ).fetchone()[0]
        if source_max is None:
            raise RuntimeError("market.v_price_kline_qfq has no stock daily rows")
        effective_end = min(requested_end, source_max)
        if effective_end < requested_start:
            log.warning("no source-covered trading days for %s -> %s; source_max=%s", requested_start, requested_end, source_max)
            return 0
        if effective_end != requested_end:
            log.warning("clamped end from %s to source_max %s", requested_end, effective_end)

        days = conn.execute(
            """
            SELECT COUNT(*), MIN(CAST(trade_date AS DATE)), MAX(CAST(trade_date AS DATE))
              FROM dim_trading_calendar
             WHERE is_trading = 1
               AND CAST(trade_date AS DATE) BETWEEN ? AND ?
            """,
            [requested_start.isoformat(), effective_end.isoformat()],
        ).fetchone()
        log.info("  trading-calendar target: %s days / %s -> %s", days[0], days[1], days[2])
        if days[0] == 0:
            return 0

        if args.incremental:
            conn.execute(DDL_CREATE_IF_NOT_EXISTS)
            conn.execute(
                f"DELETE FROM {TARGET} WHERE trade_date >= CAST(? AS DATE) AND trade_date <= CAST(? AS DATE)",
                [requested_start.isoformat(), effective_end.isoformat()],
            )
        else:
            conn.execute(DDL_DROP)
            conn.execute(DDL_CREATE)
        conn.execute(
            BUILD_SQL,
            [
                requested_start.isoformat(),
                effective_end.isoformat(),
                requested_start.isoformat(),
                effective_end.isoformat(),
            ],
        )
        conn.execute(DDL_INDEX)
        conn.commit()

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
        non_trading = conn.execute(
            f"""
            SELECT COUNT(*)
              FROM {TARGET} t
              LEFT JOIN dim_trading_calendar cal
                ON CAST(cal.trade_date AS DATE) = CAST(t.trade_date AS DATE)
             WHERE COALESCE(cal.is_trading, 0) <> 1
            """
        ).fetchone()[0]
        if non_trading > 0:
            log.error("  calendar integrity FAIL: %s non-trading rows", non_trading)
            return 1
        log.info("  PIT/calendar integrity PASS: pit=0 non_trading=0")

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
