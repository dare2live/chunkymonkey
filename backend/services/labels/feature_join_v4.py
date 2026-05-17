"""P0a feature × label JOIN v4 — Phase 4 features wired into canonical panel.

Codex round 20 verdict: Phase 4 feature modules 都没 wire 到生产 panel — 必须建 v4.

v3_ext (v3 + 11 capital_flow PIT cols) → v4 (v3_ext + Phase 4 expansions):
- capital_flow (existing in v3_ext): lhb/exec/holder 11 raw cols
- market_cap_decile: mcap_decile (1 col from fact_market_cap_decile_daily, PIT trade_date)
- industry_beta: beta_60d + beta_60d_zscore (2 cols from fact_industry_beta_daily, PIT)
- sector_momentum: 9 raw + 2 derived (JOIN PIT industry + sector_momentum_daily, observed_snapshot only)
- institution_survey: 4 raw + 3 derived (JOIN mart_stock_survey_features, 2025-04 起覆盖)
- time_of_month: 7 inline SQL date features (无 join)

PIT 严格 (Codex CRITICAL):
- 全部 source 表都 verified PIT-safe (trade_date / as_of_date 严格 <= signal_date)
- mart_stock_industry_pit filter confidence_level='observed_snapshot' (14.3% fallback 排除)
- forecast_upside 不接 v4 (无 PIT 历史快照, 等 daily snapshot 累积数月)

API:
    from services.labels.feature_join_v4 import build_p0a_feature_label_panel_v4

    result = build_p0a_feature_label_panel_v4(
        db_path="data/smartmoney.duckdb",
        signal_dates=["2024-01-02", ...],
        stock_codes=["600000", ...],
    )

未跑前请确认 Optuna PID 已结束 (DB single-writer lock).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Iterable

from services.duck_adapter import connect as duck_connect

log = logging.getLogger("labels.feature_join_v4")

FEATURE_PANEL_VERSION_V4 = "p0a_v4"


# v4 列扩展 (相对 v3_ext 增量)
V4_NEW_COLS = [
    # market_cap_decile (1 col, from fact_market_cap_decile_daily)
    ("mcap_decile", "INTEGER"),
    # industry_beta (2 cols, from fact_industry_beta_daily)
    ("beta_60d", "DOUBLE"),
    ("beta_60d_zscore", "DOUBLE"),
    # sector_momentum (9 raw from fact_sector_momentum_daily via PIT industry)
    ("sm_ret_5d", "DOUBLE"),
    ("sm_ret_20d", "DOUBLE"),
    ("sm_ret_60d", "DOUBLE"),
    ("sm_ret_120d", "DOUBLE"),
    ("sm_excess_20d", "DOUBLE"),
    ("sm_excess_60d", "DOUBLE"),
    ("sm_price_vs_ma20", "DOUBLE"),
    ("sm_price_vs_ma60", "DOUBLE"),
    ("sm_vol_60d", "DOUBLE"),
    # institution_survey (4 raw from mart_stock_survey_features)
    ("survey_count_30d", "INTEGER"),
    ("survey_count_60d", "INTEGER"),
    ("survey_inst_30d", "INTEGER"),
    ("survey_inst_60d", "INTEGER"),
    # time_of_month (7 inline SQL)
    ("tom_day_of_month", "INTEGER"),
    ("tom_days_to_month_end", "INTEGER"),
    ("tom_days_from_month_start", "INTEGER"),
    ("tom_month_phase", "INTEGER"),
    ("tom_is_first_week", "INTEGER"),
    ("tom_is_last_week", "INTEGER"),
    ("tom_is_month_turn", "INTEGER"),
]


_FEATURE_JOIN_SQL_V4 = """
INSERT INTO mart_p0a_feature_label_panel_v4
SELECT
    v3ext.*,
    -- market_cap_decile
    mcd.mcap_decile,
    -- industry_beta
    ib.beta_60d, ib.beta_60d_zscore,
    -- sector_momentum (via PIT industry observed_snapshot only)
    sm.ret_5d AS sm_ret_5d,
    sm.ret_20d AS sm_ret_20d,
    sm.ret_60d AS sm_ret_60d,
    sm.ret_120d AS sm_ret_120d,
    sm.excess_20d AS sm_excess_20d,
    sm.excess_60d AS sm_excess_60d,
    sm.price_vs_ma20 AS sm_price_vs_ma20,
    sm.price_vs_ma60 AS sm_price_vs_ma60,
    sm.vol_60d AS sm_vol_60d,
    -- institution_survey
    sv.survey_count_30d, sv.survey_count_60d,
    sv.survey_inst_30d, sv.survey_inst_60d,
    -- time_of_month (inline SQL date math)
    EXTRACT(DAY FROM v3ext.signal_date)::INTEGER AS tom_day_of_month,
    DATEDIFF('day', v3ext.signal_date, DATE_TRUNC('month', v3ext.signal_date) + INTERVAL '1 month' - INTERVAL '1 day')::INTEGER AS tom_days_to_month_end,
    DATEDIFF('day', DATE_TRUNC('month', v3ext.signal_date), v3ext.signal_date)::INTEGER AS tom_days_from_month_start,
    CASE
        WHEN EXTRACT(DAY FROM v3ext.signal_date) <= 7 THEN 0
        WHEN EXTRACT(DAY FROM v3ext.signal_date) >= DAY(LAST_DAY(v3ext.signal_date)) - 6 THEN 2
        ELSE 1
    END::INTEGER AS tom_month_phase,
    CASE WHEN EXTRACT(DAY FROM v3ext.signal_date) <= 7 THEN 1 ELSE 0 END::INTEGER AS tom_is_first_week,
    CASE WHEN EXTRACT(DAY FROM v3ext.signal_date) >= DAY(LAST_DAY(v3ext.signal_date)) - 6 THEN 1 ELSE 0 END::INTEGER AS tom_is_last_week,
    CASE
        WHEN EXTRACT(DAY FROM v3ext.signal_date) <= 3 OR
             EXTRACT(DAY FROM v3ext.signal_date) >= DAY(LAST_DAY(v3ext.signal_date)) - 2 THEN 1
        ELSE 0
    END::INTEGER AS tom_is_month_turn
FROM mart_p0a_feature_label_panel_v3_ext v3ext
LEFT JOIN fact_market_cap_decile_daily mcd
  ON mcd.stock_code = v3ext.stock_code
 AND mcd.trade_date = v3ext.signal_date
LEFT JOIN fact_industry_beta_daily ib
  ON ib.stock_code = v3ext.stock_code
 AND ib.trade_date = v3ext.signal_date
-- PIT industry lookup (strict observed_snapshot, fallback excluded)
LEFT JOIN mart_stock_industry_pit sip
  ON sip.stock_code = v3ext.stock_code
 AND sip.effective_from <= CAST(v3ext.signal_date AS VARCHAR)
 AND (sip.effective_to > CAST(v3ext.signal_date AS VARCHAR) OR sip.effective_to IS NULL)
 AND sip.confidence_level = 'observed_snapshot'
LEFT JOIN fact_sector_momentum_daily sm
  ON sm.sector_name = sip.tdx_l1_name
 AND sm.date = CAST(v3ext.signal_date AS VARCHAR)
-- Institution survey (PIT as_of_date)
LEFT JOIN mart_stock_survey_features sv
  ON sv.stock_code = v3ext.stock_code
 AND sv.as_of_date = CAST(v3ext.signal_date AS VARCHAR)
WHERE v3ext.signal_date IN (SELECT signal_date FROM tmp_signal_dates)
  AND v3ext.stock_code IN (SELECT stock_code FROM tmp_stocks)
"""


def build_p0a_feature_label_panel_v4(
    db_path: str,
    *,
    signal_dates: Iterable[str],
    stock_codes: Iterable[str],
) -> dict:
    """Build v4 panel — v3_ext + Phase 4 expansion 23 cols.

    Prerequisite: mart_p0a_feature_label_panel_v3_ext 已 build (capital_flow 已 wired).
    """
    signal_dates = list(signal_dates)
    stock_codes = list(stock_codes)
    if not signal_dates or not stock_codes:
        return {"rows_built": 0, "feature_version": FEATURE_PANEL_VERSION_V4}

    conn = duck_connect(db_path)
    try:
        # DDL: CREATE TABLE LIKE v3_ext + ALTER ADD 22 cols
        conn.execute(
            "CREATE TABLE IF NOT EXISTS mart_p0a_feature_label_panel_v4 AS "
            "SELECT * FROM mart_p0a_feature_label_panel_v3_ext WHERE 1=0"
        )
        for col, dtype in V4_NEW_COLS:
            try:
                conn.execute(
                    f"ALTER TABLE mart_p0a_feature_label_panel_v4 ADD COLUMN {col} {dtype}"
                )
            except Exception as e:
                log.debug(f"  ALTER ADD {col} skipped (likely exists): {e}")

        # Temp signal/stock filters (idempotent re-build by slice)
        conn.execute("DROP TABLE IF EXISTS tmp_signal_dates")
        conn.execute("CREATE TEMP TABLE tmp_signal_dates(signal_date DATE)")
        conn.executemany("INSERT INTO tmp_signal_dates VALUES (?)", [(d,) for d in signal_dates])
        conn.execute("DROP TABLE IF EXISTS tmp_stocks")
        conn.execute("CREATE TEMP TABLE tmp_stocks(stock_code TEXT)")
        conn.executemany("INSERT INTO tmp_stocks VALUES (?)", [(c,) for c in stock_codes])

        # Clear target slice for re-build
        conn.execute(
            "DELETE FROM mart_p0a_feature_label_panel_v4 "
            "WHERE signal_date IN (SELECT signal_date FROM tmp_signal_dates) "
            "  AND stock_code IN (SELECT stock_code FROM tmp_stocks)"
        )

        built_at = datetime.now(UTC).isoformat(timespec="seconds")
        conn.execute(_FEATURE_JOIN_SQL_V4)

        conn.execute(
            "UPDATE mart_p0a_feature_label_panel_v4 "
            "SET feature_version = ?, built_at = ? "
            "WHERE signal_date IN (SELECT signal_date FROM tmp_signal_dates) "
            "  AND stock_code IN (SELECT stock_code FROM tmp_stocks)",
            [FEATURE_PANEL_VERSION_V4, built_at],
        )

        n = conn.execute(
            "SELECT COUNT(*) FROM mart_p0a_feature_label_panel_v4 "
            "WHERE signal_date IN (SELECT signal_date FROM tmp_signal_dates) "
            "  AND stock_code IN (SELECT stock_code FROM tmp_stocks)"
        ).fetchone()[0]

        return {
            "rows_built": n,
            "feature_version": FEATURE_PANEL_VERSION_V4,
            "built_at": built_at,
            "new_cols_count": len(V4_NEW_COLS),
        }
    finally:
        conn.close()
