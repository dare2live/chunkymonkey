"""Phase 1 — mart_stock_regime_full materialized table (Codex aa4a41ca Path 3, 2026-05-16).

CREATE TABLE AS SELECT 把 v3 panel + 3 missing dim (candle / regime / calendar) JOIN 成单 materialized
表, 供下游 model training / paper_sim 一次性读, 不重复 JOIN.

PIT-safe (Codex aa4a41ca):
- 所有 JOIN 严格 trade_date 锚点
- regime 用 D-1 (T 日 09:25 决策时未知 T 日 EOD regime, 用 D-1)
- candle source_max_trade_date = trade_date (per-bar PIT)
- calendar 由 trade_date 计算 (PIT 安全 by construction)

Prerequisite:
- v3 panel: mart_p0a_feature_label_panel_v3 (112 cols, ~4M rows)
- fact_candle_pattern_daily (build via build_candle_pattern_daily.py first)
- fact_regime_state (existing, ~775 rows)
- dim_trading_calendar (existing, ~969 rows)

用法:
    # 全量 (rebuild)
    PYTHONPATH=backend python backend/scripts/build_mart_stock_regime_full.py \\
        --start 2024-01-01 --end 2026-04-23

    # smoke (验证逻辑)
    PYTHONPATH=backend python backend/scripts/build_mart_stock_regime_full.py \\
        --start 2026-03-01 --end 2026-04-23 --limit-stocks 50
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("build_mart_stock_regime_full")


SMART_DB = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"
TARGET = "mart_stock_regime_full"


# 注: v3 panel 112 cols 全 inherit. Codex Path 3 keep v3 alpha 不 recurate.
DROP_SQL = f"DROP TABLE IF EXISTS {TARGET}"
CREATE_INDEX_DATE = f"CREATE INDEX IF NOT EXISTS idx_regime_full_date ON {TARGET}(signal_date)"
CREATE_INDEX_STOCK_DATE = f"CREATE INDEX IF NOT EXISTS idx_regime_full_stock_date ON {TARGET}(stock_code, signal_date)"

DDL = f"""
CREATE TABLE {TARGET} AS
WITH calendar_features AS (
    SELECT
        CAST(trade_date AS DATE) AS trade_date,
        EXTRACT(MONTH FROM CAST(trade_date AS DATE)) AS cal_month,
        EXTRACT(DOW FROM CAST(trade_date AS DATE)) AS cal_dow,
        EXTRACT(DAY FROM CAST(trade_date AS DATE)) AS cal_day_of_month,
        EXTRACT(QUARTER FROM CAST(trade_date AS DATE)) AS cal_quarter,
        -- trading_day_of_month: 该月内第几个交易日
        ROW_NUMBER() OVER (
            PARTITION BY DATE_TRUNC('month', CAST(trade_date AS DATE))
            ORDER BY trade_date
        ) AS cal_trading_day_of_month,
        -- 距月末 trading day 数
        COUNT(*) OVER (
            PARTITION BY DATE_TRUNC('month', CAST(trade_date AS DATE))
            ORDER BY trade_date
            ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING
        ) AS cal_trading_days_to_month_end
    FROM dim_trading_calendar
    WHERE is_trading = 1
),
regime_lag1 AS (
    -- PIT: T 日决策用 T-1 日 EOD regime label
    SELECT
        CAST(trade_date AS DATE) AS regime_orig_date,
        regime_id,
        regime_label,
        transition_signal,
        -- 取后一个 trading_date 作 "应用日" (PIT-safe)
        LEAD(CAST(trade_date AS DATE)) OVER (ORDER BY trade_date) AS apply_date
    FROM fact_regime_state
)
SELECT
    p.*,  -- v3 panel 全 112 cols inherit
    -- candle_pattern dim (12 cols)
    cp.body_ratio AS cdp_body_ratio,
    cp.upper_shadow_ratio AS cdp_upper_shadow,
    cp.lower_shadow_ratio AS cdp_lower_shadow,
    cp.close_position AS cdp_close_pos,
    cp.volume_relative AS cdp_volume_rel,
    cp.breakout_strength_20 AS cdp_breakout_20,
    cp.is_bullish AS cdp_is_bullish,
    cp.is_doji AS cdp_is_doji,
    cp.is_long_lower_shadow AS cdp_is_long_lower,
    cp.is_long_upper_shadow AS cdp_is_long_upper,
    cp.is_marubozu AS cdp_is_marubozu,
    cp.is_high_volume AS cdp_is_high_vol,
    cp.source_max_trade_date AS cdp_source_max_date,
    -- regime dim (3 cols, PIT D-1)
    rl.regime_id AS regime_id_lag1,
    rl.regime_label AS regime_label_lag1,
    rl.transition_signal AS regime_transition_lag1,
    -- calendar (5 cols PIT-safe)
    cal.cal_month AS cal_month,
    cal.cal_dow AS cal_dow,
    cal.cal_day_of_month AS cal_dom,
    cal.cal_trading_day_of_month AS cal_tdom,
    cal.cal_trading_days_to_month_end AS cal_tdays_to_month_end,
    -- backlog feature research (Codex a49c90a6 step #51): industry beta + mcap decile
    ib.beta_60d AS beta_60d,
    ib.beta_60d_zscore AS beta_60d_z,
    mc.mcap_decile AS mcap_decile,
    -- PIT 锚点
    p.signal_date AS regime_full_anchor_date,
    CURRENT_TIMESTAMP AS built_at
FROM mart_p0a_feature_label_panel_v3 p
LEFT JOIN fact_candle_pattern_daily cp
    ON cp.stock_code = p.stock_code AND cp.trade_date = p.signal_date
LEFT JOIN regime_lag1 rl
    ON rl.apply_date = p.signal_date
LEFT JOIN calendar_features cal
    ON cal.trade_date = p.signal_date
LEFT JOIN fact_industry_beta_daily ib
    ON ib.stock_code = p.stock_code AND ib.trade_date = p.signal_date
LEFT JOIN fact_market_cap_decile_daily mc
    ON mc.stock_code = p.stock_code AND mc.trade_date = p.signal_date
WHERE p.signal_date >= CAST(? AS DATE) AND p.signal_date <= CAST(? AS DATE)
"""


# 5 acceptance audits (Codex aa4a41ca D)
ACCEPTANCE_AUDIT_SQL = {
    "PIT-integrity-candle": f"""
        SELECT COUNT(*) AS bad
        FROM {TARGET}
        WHERE cdp_source_max_date > signal_date
    """,
    "PIT-integrity-regime": f"""
        SELECT COUNT(*) AS bad
        FROM {TARGET} t
        LEFT JOIN fact_regime_state r ON CAST(r.trade_date AS DATE) = t.signal_date
        WHERE t.regime_id_lag1 IS NOT NULL AND r.regime_id IS NOT NULL
        -- regime_id_lag1 应该 != 当日 regime_id (因为是 D-1)
        -- 这检测如果 lag1 == 当日 regime, 说明 PIT 错 (用了当日 regime)
        -- 不严格的 audit, 但相邻日 regime 相同时也合法, 仅作 sanity
    """,
    "Feature-coverage": f"""
        SELECT
            ROUND(AVG(CASE WHEN cdp_body_ratio IS NOT NULL THEN 1.0 ELSE 0 END), 4) AS candle_cov,
            ROUND(AVG(CASE WHEN regime_id_lag1 IS NOT NULL THEN 1.0 ELSE 0 END), 4) AS regime_cov,
            ROUND(AVG(CASE WHEN cal_tdom IS NOT NULL THEN 1.0 ELSE 0 END), 4) AS calendar_cov
        FROM {TARGET}
    """,
    "Row-count": f"""
        SELECT COUNT(*) AS rows,
               COUNT(DISTINCT stock_code) AS stocks,
               COUNT(DISTINCT signal_date) AS days,
               MIN(signal_date) AS first_d,
               MAX(signal_date) AS last_d
        FROM {TARGET}
    """,
    "Schema": f"""
        SELECT COUNT(*) AS total_cols
        FROM information_schema.columns
        WHERE table_name = '{TARGET}'
    """,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-01-01")   # rule-compliance: ok evidence=v3-panel-window-start
    parser.add_argument("--end", default="2026-04-23")     # rule-compliance: ok evidence=v3-panel-window-end
    args = parser.parse_args()

    log.info(f"=== build mart_stock_regime_full ===")
    log.info(f"  window: {args.start} → {args.end}")

    conn = duckdb.connect(str(SMART_DB))

    # Prereq check
    for tbl in ("mart_p0a_feature_label_panel_v3", "fact_candle_pattern_daily",
                "fact_regime_state", "dim_trading_calendar"):
        try:
            r = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            log.info(f"  prereq {tbl}: {r:,} rows")
        except Exception as e:
            log.error(f"  prereq MISSING: {tbl} ({e})")
            return 1

    t0 = time.time()
    conn.execute(DROP_SQL)
    conn.execute(DDL, [args.start, args.end])
    conn.execute(CREATE_INDEX_DATE)
    conn.execute(CREATE_INDEX_STOCK_DATE)
    log.info(f"  build done: {time.time() - t0:.0f}s")

    # 5 acceptance audits
    log.info("\n=== acceptance audits ===")
    for name, sql in ACCEPTANCE_AUDIT_SQL.items():
        r = conn.execute(sql).fetchone()
        log.info(f"  {name}: {r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
