"""P0a feature × label JOIN v3_ext — 加 fact_capital_flow_pit_daily 11 cols.

v3 (92 features usable after _META exclude) → v3_ext (+11 cols 资金流 PIT):
- LHB 龙虎榜 5: lhb_count_30d/90d, lhb_net_buy_pct_30d, lhb_inst_buy_30d/90d
- 高管交易 5: exec_buy_60d, exec_sell_60d, exec_buy/sell_pct_60d, exec_net_signal
- 股东户数 1: holder_count_change_q_pct (季度 PIT)

PIT 验证 (pit-audit Step 1-3 PASS, 2026-05-15):
- Source: fact_capital_flow_pit_daily (858K rows, 810 dates, 2023-01 → 2026-05)
- backfill_capital_flow_pit.py PIT design: trailing window + event publication date inclusive
- LHB trade_date <= signal_date (borderline inclusive, 实盘 T+1 才见但 marginal)
- exec notice_date <= signal_date (公告日当天可见, ✅ PIT)
- holder available_date <= signal_date (公告后, ✅ PIT)
- 跟 fact_financial_pit_daily 同模式 (Codex 之前 verify CLEAN)

Step 4 micro-ablation defer to chain v6 完后 (smartmoney.duckdb single writer lock).

⚠ chain v6 跑中, panel build 等 chain 完. 当前 code + 单测 prep, commit not run.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

UTC = timezone.utc
from typing import Iterable

from services.duck_adapter import connect as duck_connect

log = logging.getLogger("labels.feature_join_v3_ext")

FEATURE_PANEL_VERSION_V3_EXT = "p0a_v3_ext"


# v3_ext panel 在 v3 基础上加 11 cols (training feature) + 1 meta col (holder_count_q_report_date 仅 audit, 不入 feature)


def _add_column_duplicate_safe(conn, table: str, col: str, dtype: str) -> None:
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {dtype}")
    except Exception as e:
        log.debug(f"  ALTER ADD {col} skipped (likely exists): {e}")


FEATURE_PANEL_DDL_V3_EXT = """
CREATE TABLE IF NOT EXISTS mart_p0a_feature_label_panel_v3_ext AS
SELECT * FROM mart_p0a_feature_label_panel_v3 WHERE 1=0;

-- 加 11 cols + 1 meta (idempotent: IF NOT EXISTS via try/except in builder)
"""

# v3_ext capital_flow JOIN — 在 v3 panel 基础上 LEFT JOIN
# 设计: build_v3_ext 跑 INSERT INTO mart_p0a_feature_label_panel_v3_ext SELECT v3.*, cf.*
_FEATURE_JOIN_SQL_V3_EXT = """
INSERT INTO mart_p0a_feature_label_panel_v3_ext
SELECT
    v3.*,
    -- LHB 龙虎榜 5 (PIT-safe trailing window, source trade_date <= signal_date)
    cf.lhb_count_30d,
    cf.lhb_net_buy_pct_30d,
    cf.lhb_inst_buy_30d,
    cf.lhb_count_90d,
    cf.lhb_inst_buy_90d,
    -- 高管交易 5 (PIT-safe notice_date <= signal_date)
    cf.exec_buy_60d,
    cf.exec_sell_60d,
    cf.exec_buy_pct_60d,
    cf.exec_sell_pct_60d,
    cf.exec_net_signal,
    -- 股东户数 1 (PIT-safe available_date <= signal_date)
    cf.holder_count_change_q_pct,
    -- meta (audit only, 训练时 exclude via _META_FIELDS)
    cf.holder_count_q_report_date
FROM mart_p0a_feature_label_panel_v3 v3
LEFT JOIN fact_capital_flow_pit_daily cf
  ON cf.stock_code = v3.stock_code
 AND CAST(cf.trade_date AS DATE) = v3.signal_date
WHERE v3.signal_date IN (SELECT signal_date FROM tmp_signal_dates)
  AND v3.stock_code IN (SELECT stock_code FROM tmp_stocks)
"""


def build_p0a_feature_label_panel_v3_ext(
    db_path: str,
    *,
    signal_dates: Iterable[str],
    stock_codes: Iterable[str],
) -> dict:
    """Build v3_ext panel — v3 + 11 capital_flow_pit cols.

    Prerequisite: mart_p0a_feature_label_panel_v3 已 build (chain v6 Step 1 完).
    """
    signal_dates = list(signal_dates)
    stock_codes = list(stock_codes)
    if not signal_dates or not stock_codes:
        return {"rows_built": 0, "feature_version": FEATURE_PANEL_VERSION_V3_EXT}

    conn = duck_connect(db_path)
    try:
        # DDL: CREATE TABLE LIKE v3 + ALTER ADD 11+1 cols
        conn.execute(
            "CREATE TABLE IF NOT EXISTS mart_p0a_feature_label_panel_v3_ext AS "
            "SELECT * FROM mart_p0a_feature_label_panel_v3 WHERE 1=0"
        )
        # 加 11 cols + 1 meta (idempotent)
        table = "mart_p0a_feature_label_panel_v3_ext"
        _add_column_duplicate_safe(conn, table, "lhb_count_30d", "INTEGER")
        _add_column_duplicate_safe(conn, table, "lhb_net_buy_pct_30d", "DOUBLE")
        _add_column_duplicate_safe(conn, table, "lhb_inst_buy_30d", "INTEGER")
        _add_column_duplicate_safe(conn, table, "lhb_count_90d", "INTEGER")
        _add_column_duplicate_safe(conn, table, "lhb_inst_buy_90d", "INTEGER")
        _add_column_duplicate_safe(conn, table, "exec_buy_60d", "INTEGER")
        _add_column_duplicate_safe(conn, table, "exec_sell_60d", "INTEGER")
        _add_column_duplicate_safe(conn, table, "exec_buy_pct_60d", "DOUBLE")
        _add_column_duplicate_safe(conn, table, "exec_sell_pct_60d", "DOUBLE")
        _add_column_duplicate_safe(conn, table, "exec_net_signal", "DOUBLE")
        _add_column_duplicate_safe(conn, table, "holder_count_change_q_pct", "DOUBLE")
        _add_column_duplicate_safe(conn, table, "holder_count_q_report_date", "TEXT")

        conn.execute("DROP TABLE IF EXISTS tmp_signal_dates")
        conn.execute("CREATE TEMP TABLE tmp_signal_dates(signal_date DATE)")
        conn.executemany("INSERT INTO tmp_signal_dates VALUES (?)", [(d,) for d in signal_dates])
        conn.execute("DROP TABLE IF EXISTS tmp_stocks")
        conn.execute("CREATE TEMP TABLE tmp_stocks(stock_code TEXT)")
        conn.executemany("INSERT INTO tmp_stocks VALUES (?)", [(c,) for c in stock_codes])

        # Clear target slice for re-build
        conn.execute(
            "DELETE FROM mart_p0a_feature_label_panel_v3_ext "
            "WHERE signal_date IN (SELECT signal_date FROM tmp_signal_dates) "
            "  AND stock_code IN (SELECT stock_code FROM tmp_stocks)"
        )

        # Set feature_version + built_at on the JOIN target subset BEFORE INSERT
        built_at = datetime.now(UTC).isoformat(timespec="seconds")

        conn.execute(_FEATURE_JOIN_SQL_V3_EXT)

        # Override feature_version on newly inserted rows
        conn.execute(
            "UPDATE mart_p0a_feature_label_panel_v3_ext "
            "SET feature_version = ?, built_at = ? "
            "WHERE signal_date IN (SELECT signal_date FROM tmp_signal_dates) "
            "  AND stock_code IN (SELECT stock_code FROM tmp_stocks)",
            [FEATURE_PANEL_VERSION_V3_EXT, built_at],
        )

        n = conn.execute(
            "SELECT COUNT(*) FROM mart_p0a_feature_label_panel_v3_ext "
            "WHERE signal_date IN (SELECT signal_date FROM tmp_signal_dates) "
            "  AND stock_code IN (SELECT stock_code FROM tmp_stocks)"
        ).fetchone()[0]

        return {
            "rows_built": n,
            "feature_version": FEATURE_PANEL_VERSION_V3_EXT,
            "built_at": built_at,
        }
    finally:
        conn.close()
