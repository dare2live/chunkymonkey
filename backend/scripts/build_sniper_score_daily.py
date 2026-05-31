#!/usr/bin/env python3
"""Build mart_sniper_score_daily with PIT-strict SQL batch joins.

Phase 3.4 Sniper confluence materialization:
- one DuckDB SQL pipeline, no Python stock/date loops
- source dates are joined on equality to signal_date or via ASOF date <= signal_date
- missing rule inputs stay NULL and reduce n_rules_eligible
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
SMART_DB = REPO_ROOT / "data" / "smartmoney.duckdb"
MARKET_DB = REPO_ROOT / "data" / "market.duckdb"
ALPHA158_DB = REPO_ROOT / "data" / "alpha158.duckdb"

DEFAULT_START_DATE = "2024-07-01"  # rule-compliance: ok evidence=p0b-walk-forward-起始
DEFAULT_END_DATE = "2026-04-13"    # rule-compliance: ok evidence=panel-cutoff

log = logging.getLogger("build_sniper_score_daily")


MART_DDL = """
CREATE TABLE IF NOT EXISTS mart_sniper_score_daily (
    signal_date DATE NOT NULL,
    stock_code VARCHAR NOT NULL,
    confluence_score INTEGER NOT NULL,
    triggered BOOLEAN NOT NULL,
    r1_ret_60d_hit BOOLEAN,
    r2_lhb_hit BOOLEAN,
    r3_main_capital_hit BOOLEAN,
    r4_sector_momentum_hit BOOLEAN,
    r5_sue_hit BOOLEAN,
    r6_limit_up_hit BOOLEAN,
    r7_unlock_pledge_ok BOOLEAN,
    n_rules_eligible INTEGER,
    built_at VARCHAR,
    PRIMARY KEY (signal_date, stock_code)
)
"""


def _sql_path(path: str | Path) -> str:
    return str(path).replace("'", "''")


def _relation_columns(conn: duckdb.DuckDBPyConnection, relation: str) -> set[str]:
    try:
        rows = conn.execute(f"DESCRIBE {relation}").fetchall()
    except duckdb.Error:
        return set()
    return {str(row[0]) for row in rows}


def _main_capital_cte(conn: duckdb.DuckDBPyConnection) -> str:
    """Return r3 CTE using the best available main-capital source."""
    fact_cols = _relation_columns(conn, "fact_capital_flow_daily")
    if {"stock_code", "trade_date"} <= fact_cols:
        if "main_net_inflow" in fact_cols:
            amount_col = "main_net_inflow"
        elif "main_net_amount" in fact_cols:
            amount_col = "main_net_amount"
        else:
            amount_col = ""
        if amount_col:
            return f"""
flow_source AS MATERIALIZED (
    SELECT
        stock_code,
        CAST(trade_date AS DATE) AS flow_date,
        CAST({amount_col} AS DOUBLE) AS main_net_amount
    FROM fact_capital_flow_daily
    WHERE CAST(trade_date AS DATE) <= DATE '{{end_date}}'
),
flow_roll AS MATERIALIZED (
    SELECT
        stock_code,
        flow_date,
        SUM(main_net_amount) OVER (
            PARTITION BY stock_code
            ORDER BY flow_date
            ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
        ) AS main_net_inflow_5d
    FROM flow_source
),
r3 AS MATERIALIZED (
    SELECT
        u.signal_date,
        u.stock_code,
        CASE
            WHEN fr.flow_date IS NULL THEN NULL
            WHEN fr.flow_date < u.signal_date - INTERVAL 10 DAY THEN NULL
            WHEN fr.main_net_inflow_5d IS NULL THEN NULL
            ELSE fr.main_net_inflow_5d > 0
        END AS r3_main_capital_hit
    FROM universe u
    ASOF LEFT JOIN flow_roll fr
      ON u.stock_code = fr.stock_code
     AND u.signal_date >= fr.flow_date
)
"""

    return """
r3 AS MATERIALIZED (
    SELECT
        signal_date,
        stock_code,
        CAST(NULL AS BOOLEAN) AS r3_main_capital_hit
    FROM universe
)
"""


def _sue_cte(conn: duckdb.DuckDBPyConnection) -> str:
    """Return r5 CTE from mart_stock_sue when present, else financial PIT proxy."""
    sue_cols = _relation_columns(conn, "mart_stock_sue")
    sue_value_col = next((c for c in ("sue", "SUE", "earnings_surprise", "standardized_unexpected_earnings") if c in sue_cols), "")
    sue_date_col = next((c for c in ("signal_date", "trade_date", "date", "report_date") if c in sue_cols), "")
    if {"stock_code"} <= sue_cols and sue_value_col and sue_date_col:
        return f"""
sue_source AS MATERIALIZED (
    SELECT
        stock_code,
        CAST({sue_date_col} AS DATE) AS sue_date,
        CAST({sue_value_col} AS DOUBLE) AS sue_value
    FROM mart_stock_sue
    WHERE CAST({sue_date_col} AS DATE) <= DATE '{{end_date}}'
      AND {sue_value_col} IS NOT NULL
),
r5 AS MATERIALIZED (
    SELECT
        u.signal_date,
        u.stock_code,
        CASE
            WHEN s.sue_value IS NULL THEN NULL
            ELSE s.sue_value > 0
        END AS r5_sue_hit
    FROM universe u
    ASOF LEFT JOIN sue_source s
      ON u.stock_code = s.stock_code
     AND u.signal_date >= s.sue_date
)
"""

    fin_cols = _relation_columns(conn, "fact_financial_pit_daily")
    if {"stock_code", "trade_date", "profit_yoy"} <= fin_cols:
        return """
sue_source AS MATERIALIZED (
    SELECT
        stock_code,
        CAST(trade_date AS DATE) AS sue_date,
        CAST(profit_yoy AS DOUBLE) AS sue_value
    FROM fact_financial_pit_daily
    WHERE CAST(trade_date AS DATE) <= DATE '{end_date}'
      AND profit_yoy IS NOT NULL
),
r5 AS MATERIALIZED (
    SELECT
        u.signal_date,
        u.stock_code,
        CASE
            WHEN s.sue_value IS NULL THEN NULL
            ELSE s.sue_value > 0
        END AS r5_sue_hit
    FROM universe u
    ASOF LEFT JOIN sue_source s
      ON u.stock_code = s.stock_code
     AND u.signal_date >= s.sue_date
)
"""

    return """
r5 AS MATERIALIZED (
    SELECT
        signal_date,
        stock_code,
        CAST(NULL AS BOOLEAN) AS r5_sue_hit
    FROM universe
)
"""


def _unlock_pledge_cte(conn: duckdb.DuckDBPyConnection) -> str:
    """Return r7 CTE only when both required PIT event tables are available."""
    unlock_cols = _relation_columns(conn, "fact_unlock_event")
    pledge_cols = _relation_columns(conn, "fact_pledge_event")
    unlock_ratio_col = next((c for c in ("unlock_ratio", "unlock_ratio_float_mkt", "unlock_pct") if c in unlock_cols), "")
    pledge_ratio_col = next((c for c in ("pledge_ratio", "pledged_ratio", "share_pledge_ratio") if c in pledge_cols), "")
    unlock_date_col = next((c for c in ("event_date", "unlock_date", "trade_date", "snapshot_date") if c in unlock_cols), "")
    pledge_date_col = next((c for c in ("event_date", "pledge_date", "trade_date", "snapshot_date") if c in pledge_cols), "")

    if {"stock_code"} <= unlock_cols and {"stock_code"} <= pledge_cols and unlock_ratio_col and pledge_ratio_col and unlock_date_col and pledge_date_col:
        return f"""
unlock_source AS MATERIALIZED (
    SELECT
        stock_code,
        CAST({unlock_date_col} AS DATE) AS event_date,
        MAX(CAST({unlock_ratio_col} AS DOUBLE)) AS unlock_ratio
    FROM fact_unlock_event
    WHERE CAST({unlock_date_col} AS DATE) <= DATE '{{end_date}}'
    GROUP BY 1, 2
),
pledge_source AS MATERIALIZED (
    SELECT
        stock_code,
        CAST({pledge_date_col} AS DATE) AS event_date,
        MAX(CAST({pledge_ratio_col} AS DOUBLE)) AS pledge_ratio
    FROM fact_pledge_event
    WHERE CAST({pledge_date_col} AS DATE) <= DATE '{{end_date}}'
    GROUP BY 1, 2
),
unlock_pit AS MATERIALIZED (
    SELECT
        u.signal_date,
        u.stock_code,
        us.unlock_ratio
    FROM universe u
    ASOF LEFT JOIN unlock_source us
      ON u.stock_code = us.stock_code
     AND u.signal_date >= us.event_date
),
pledge_pit AS MATERIALIZED (
    SELECT
        u.signal_date,
        u.stock_code,
        ps.pledge_ratio
    FROM universe u
    ASOF LEFT JOIN pledge_source ps
      ON u.stock_code = ps.stock_code
     AND u.signal_date >= ps.event_date
),
r7 AS MATERIALIZED (
    SELECT
        u.signal_date,
        u.stock_code,
        CASE
            WHEN up.unlock_ratio IS NULL OR pp.pledge_ratio IS NULL THEN NULL
            ELSE up.unlock_ratio < 0.10 AND pp.pledge_ratio < 0.50
        END AS r7_unlock_pledge_ok
    FROM universe u
    LEFT JOIN unlock_pit up USING (signal_date, stock_code)
    LEFT JOIN pledge_pit pp USING (signal_date, stock_code)
)
"""

    return """
r7 AS MATERIALIZED (
    SELECT
        signal_date,
        stock_code,
        CAST(NULL AS BOOLEAN) AS r7_unlock_pledge_ok
    FROM universe
)
"""


def _build_insert_sql(conn: duckdb.DuckDBPyConnection, start_date: str, end_date: str) -> str:
    flow_cte = _main_capital_cte(conn).format(end_date=end_date)
    sue_cte = _sue_cte(conn).format(end_date=end_date)
    unlock_pledge_cte = _unlock_pledge_cte(conn).format(end_date=end_date)

    return f"""
INSERT OR REPLACE INTO mart_sniper_score_daily
WITH
universe AS MATERIALIZED (
    SELECT
        signal_date,
        stock_code,
        CAST(lhb_inst_buy_30d AS DOUBLE) AS lhb_inst_buy_30d,
        CAST(sector_ret_20d AS DOUBLE) AS sector_ret_20d
    FROM mart_p0a_feature_label_panel_v4
    WHERE signal_date >= DATE '{start_date}'
      AND signal_date <= DATE '{end_date}'
),
alpha_ret AS MATERIALIZED (
    SELECT
        stock_code,
        date AS price_date,
        CAST(a158_roc60 AS DOUBLE) AS alpha_ret_60d
    FROM alpha158.fact_alpha158_panel
    WHERE date >= DATE '{start_date}'
      AND date <= DATE '{end_date}'
),
kline_features AS MATERIALIZED (
    SELECT
        code AS stock_code,
        CAST(date AS DATE) AS price_date,
        close / NULLIF(LAG(close, 60) OVER (
            PARTITION BY code
            ORDER BY CAST(date AS DATE)
        ), 0) - 1 AS kline_ret_60d,
        close / NULLIF(LAG(close) OVER (
            PARTITION BY code
            ORDER BY CAST(date AS DATE)
        ), 0) AS one_day_ratio,
        LEAD(CAST(date AS DATE)) OVER (
            PARTITION BY code
            ORDER BY CAST(date AS DATE)
        ) AS next_trade_date
    FROM market.v_price_kline_qfq
    WHERE freq = 'daily'
      AND adjust = 'qfq'
      AND CAST(date AS DATE) >= DATE '{start_date}' - INTERVAL 220 DAY
      AND CAST(date AS DATE) <= DATE '{end_date}'
),
base_market AS MATERIALIZED (
    SELECT
        u.signal_date,
        u.stock_code,
        COALESCE(k.kline_ret_60d, a.alpha_ret_60d) AS ret_60d,
        CASE
            WHEN y.one_day_ratio IS NULL THEN NULL
            WHEN regexp_matches(u.stock_code, '^(300|301)') THEN y.one_day_ratio > 1.197
            ELSE y.one_day_ratio > 1.097
        END AS r6_limit_up_hit
    FROM universe u
    LEFT JOIN kline_features k
      ON k.stock_code = u.stock_code
     AND k.price_date = u.signal_date
    LEFT JOIN alpha_ret a
      ON a.stock_code = u.stock_code
     AND a.price_date = u.signal_date
    LEFT JOIN kline_features y
      ON y.stock_code = u.stock_code
     AND y.next_trade_date = u.signal_date
),
thresholds AS MATERIALIZED (
    SELECT
        u.signal_date,
        quantile_cont(b.ret_60d, 0.90) FILTER (WHERE b.ret_60d IS NOT NULL) AS ret_60d_q90,
        quantile_cont(u.sector_ret_20d, 0.75) FILTER (WHERE u.sector_ret_20d IS NOT NULL) AS sector_ret_20d_q75
    FROM universe u
    LEFT JOIN base_market b USING (signal_date, stock_code)
    GROUP BY u.signal_date
),
r1_r2_r4_r6 AS MATERIALIZED (
    SELECT
        u.signal_date,
        u.stock_code,
        CASE
            WHEN b.ret_60d IS NULL OR t.ret_60d_q90 IS NULL THEN NULL
            ELSE b.ret_60d >= t.ret_60d_q90
        END AS r1_ret_60d_hit,
        CASE
            WHEN u.lhb_inst_buy_30d IS NULL THEN NULL
            ELSE u.lhb_inst_buy_30d > 0
        END AS r2_lhb_hit,
        CASE
            WHEN u.sector_ret_20d IS NULL OR t.sector_ret_20d_q75 IS NULL THEN NULL
            ELSE u.sector_ret_20d >= t.sector_ret_20d_q75
        END AS r4_sector_momentum_hit,
        b.r6_limit_up_hit
    FROM universe u
    LEFT JOIN base_market b USING (signal_date, stock_code)
    LEFT JOIN thresholds t USING (signal_date)
),
{flow_cte},
{sue_cte},
{unlock_pledge_cte},
scored AS MATERIALIZED (
    SELECT
        u.signal_date,
        u.stock_code,
        r.r1_ret_60d_hit,
        r.r2_lhb_hit,
        r3.r3_main_capital_hit,
        r.r4_sector_momentum_hit,
        r5.r5_sue_hit,
        r.r6_limit_up_hit,
        r7.r7_unlock_pledge_ok,
        (
            COALESCE(r.r1_ret_60d_hit::INTEGER, 0) +
            COALESCE(r.r2_lhb_hit::INTEGER, 0) +
            COALESCE(r3.r3_main_capital_hit::INTEGER, 0) +
            COALESCE(r.r4_sector_momentum_hit::INTEGER, 0) +
            COALESCE(r5.r5_sue_hit::INTEGER, 0) +
            COALESCE(r.r6_limit_up_hit::INTEGER, 0) +
            COALESCE(r7.r7_unlock_pledge_ok::INTEGER, 0)
        ) AS confluence_score,
        (
            (r.r1_ret_60d_hit IS NOT NULL)::INTEGER +
            (r.r2_lhb_hit IS NOT NULL)::INTEGER +
            (r3.r3_main_capital_hit IS NOT NULL)::INTEGER +
            (r.r4_sector_momentum_hit IS NOT NULL)::INTEGER +
            (r5.r5_sue_hit IS NOT NULL)::INTEGER +
            (r.r6_limit_up_hit IS NOT NULL)::INTEGER +
            (r7.r7_unlock_pledge_ok IS NOT NULL)::INTEGER
        ) AS n_rules_eligible
    FROM universe u
    LEFT JOIN r1_r2_r4_r6 r USING (signal_date, stock_code)
    LEFT JOIN r3 USING (signal_date, stock_code)
    LEFT JOIN r5 USING (signal_date, stock_code)
    LEFT JOIN r7 USING (signal_date, stock_code)
)
SELECT
    signal_date,
    stock_code,
    confluence_score,
    confluence_score >= 4 AS triggered,
    r1_ret_60d_hit,
    r2_lhb_hit,
    r3_main_capital_hit,
    r4_sector_momentum_hit,
    r5_sue_hit,
    r6_limit_up_hit,
    r7_unlock_pledge_ok,
    n_rules_eligible,
    CAST(current_timestamp AS VARCHAR) AS built_at
FROM scored
"""


def build_sniper_score_daily(
    *,
    smartmoney_db: str | Path = SMART_DB,
    market_db: str | Path = MARKET_DB,
    alpha158_db: str | Path = ALPHA158_DB,
    start_date: str = DEFAULT_START_DATE,
    end_date: str = DEFAULT_END_DATE,
    threads: int = 8,
    memory_limit: str = "6GB",
    rebuild: bool = False,
) -> dict[str, object]:
    """Materialize mart_sniper_score_daily and return summary stats.

    Incremental by default — INSERT OR REPLACE preserves rows outside [start_date, end_date].
    Pass rebuild=True (or --rebuild via CLI) only for explicit full wipe.
    """
    smartmoney_db = Path(smartmoney_db)
    market_db = Path(market_db)
    alpha158_db = Path(alpha158_db)

    conn = duckdb.connect(str(smartmoney_db))
    try:
        conn.execute(f"PRAGMA threads={int(threads)}")
        conn.execute(f"PRAGMA memory_limit='{memory_limit}'")
        conn.execute(f"ATTACH '{_sql_path(market_db)}' AS market (READ_ONLY)")
        conn.execute(f"ATTACH '{_sql_path(alpha158_db)}' AS alpha158 (READ_ONLY)")

        insert_sql = _build_insert_sql(conn, start_date, end_date)
        if rebuild:
            log.warning("rebuild=True: dropping mart_sniper_score_daily (full history wipe)")
            conn.execute("DROP TABLE IF EXISTS mart_sniper_score_daily")
        conn.execute(MART_DDL)
        conn.execute(insert_sql)

        summary = conn.execute("""
            SELECT
                COUNT(*) AS row_count,
                AVG(confluence_score) AS avg_score,
                SUM(triggered::INTEGER) * 100.0 / NULLIF(COUNT(*), 0) AS trigger_pct,
                MIN(signal_date) AS min_signal_date,
                MAX(signal_date) AS max_signal_date
            FROM mart_sniper_score_daily
        """).fetchone()
        return {
            "row_count": int(summary[0] or 0),
            "avg_score": float(summary[1]) if summary[1] is not None else None,
            "trigger_pct": float(summary[2]) if summary[2] is not None else None,
            "min_signal_date": summary[3],
            "max_signal_date": summary[4],
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build mart_sniper_score_daily")
    parser.add_argument("--smartmoney-db", default=str(SMART_DB))
    parser.add_argument("--market-db", default=str(MARKET_DB))
    parser.add_argument("--alpha158-db", default=str(ALPHA158_DB))
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=DEFAULT_END_DATE)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--memory-limit", default="6GB")
    parser.add_argument("--rebuild", action="store_true",
                        help="Drop existing mart_sniper_score_daily before rebuild (DESTRUCTIVE — full history wipe)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    t0 = time.time()
    log.info("Building mart_sniper_score_daily %s -> %s (rebuild=%s)",
             args.start_date, args.end_date, args.rebuild)
    result = build_sniper_score_daily(
        smartmoney_db=args.smartmoney_db,
        market_db=args.market_db,
        alpha158_db=args.alpha158_db,
        start_date=args.start_date,
        end_date=args.end_date,
        threads=args.threads,
        memory_limit=args.memory_limit,
        rebuild=args.rebuild,
    )
    elapsed = time.time() - t0
    log.info(
        "Done: rows=%s avg_score=%.4f trigger_pct=%.2f%% range=%s -> %s elapsed=%.1fs",
        f"{result['row_count']:,}",
        result["avg_score"] or 0.0,
        result["trigger_pct"] or 0.0,
        result["min_signal_date"],
        result["max_signal_date"],
        elapsed,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
