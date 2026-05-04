#!/usr/bin/env python3
"""Phase 6 DuckDB feature-panel build.

All large panel operations stay inside DuckDB temp tables. The script writes the
4M+ row feature panel directly from DuckDB relations.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.analytics import get_duck

logger = logging.getLogger("feature_panel_duck")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


PANEL_DDL = """
DROP TABLE IF EXISTS fact_feature_panel;
CREATE TABLE fact_feature_panel (
    stock_code TEXT NOT NULL,
    date       TEXT NOT NULL,
    close REAL,
    -- Pillar B 价量
    ret_1d REAL, ret_5d REAL, ret_20d REAL, ret_60d REAL,
    vol_z20d REAL, ma_ratio_5 REAL, ma_ratio_20 REAL, ma_ratio_60 REAL, ma_ratio_250 REAL,
    rz_balance REAL, rz_chg_5d_pct REAL,
    -- Alpha158 inspired
    kmid REAL, klen REAL, kup REAL, klow REAL, ksft REAL,
    vol_ratio_5_20 REAL, vol_std_5d REAL, vol_std_20d REAL,
    range_pos_20 REAL, range_pos_60 REAL,
    momentum_diff REAL, amount_chg_5d REAL,
    -- V2 dense cross-sectional / industry-relative features
    ret_20d_rank REAL, ret_60d_rank REAL, vol_z20d_rank REAL, amount_chg_5d_rank REAL,
    rz_balance_rank REAL, rz_chg_5d_pct_rank REAL,
    ret_20d_tdx_l1_rel REAL, ret_60d_tdx_l1_rel REAL,
    vol_z20d_tdx_l1_rel REAL, amount_chg_5d_tdx_l1_rel REAL,
    rz_balance_to_amount20 REAL,
    -- Pillar A 事件
    inst_event_count_30d INTEGER, inst_event_count_60d INTEGER,
    exec_buy_count_90d INTEGER, exec_buy_ge1_count_90d INTEGER,
    lhb_inst_buy_count_30d INTEGER, lhb_inst_buy_count_60d INTEGER,
    jgdy_count_60d INTEGER,
    dzjy_count_60d INTEGER,
    days_since_exec_buy INTEGER, days_since_lhb INTEGER,
    -- Pillar C 基本面
    shareholder_count_qoq REAL, inst_count_qoq REAL,
    fund_count_qoq REAL, qfii_count_qoq REAL,
    yjyg_lower_pct REAL, yjyg_upper_pct REAL,
    roe REAL, eps_basic REAL,
    -- Regime
    hs300_ret_20d REAL, hs300_ret_60d REAL, regime_flag TEXT,
    -- Label
    forward_ret_20d REAL,
    built_at TEXT,
    PRIMARY KEY (stock_code, date)
);
CREATE INDEX IF NOT EXISTS idx_fp_code ON fact_feature_panel(stock_code);
CREATE INDEX IF NOT EXISTS idx_fp_date ON fact_feature_panel(date);
CREATE INDEX IF NOT EXISTS idx_fp_date_label ON fact_feature_panel(date, forward_ret_20d);
CREATE INDEX IF NOT EXISTS idx_fp_label ON fact_feature_panel(forward_ret_20d);
"""

KLINE_DAILY_QFQ_SQL = """
SELECT code, date, open, high, low, close, volume, amount
FROM market.price_kline_tdxhub
WHERE freq='daily' AND adjust='qfq'
UNION ALL
SELECT code, date, open, high, low, close, volume, amount
FROM market.price_kline
WHERE freq='daily' AND adjust='qfq'
  AND date > (
      SELECT COALESCE(MAX(date), '1900-01-01')
      FROM market.price_kline_tdxhub
      WHERE freq='daily' AND adjust='qfq'
  )
"""

REAL_ABS_LIMIT = 1e30

KEEP_COLS = [
    "stock_code", "date", "close",
    "ret_1d", "ret_5d", "ret_20d", "ret_60d",
    "vol_z20d", "ma_ratio_5", "ma_ratio_20", "ma_ratio_60", "ma_ratio_250",
    "rz_balance", "rz_chg_5d_pct",
    "kmid", "klen", "kup", "klow", "ksft",
    "vol_ratio_5_20", "vol_std_5d", "vol_std_20d",
    "range_pos_20", "range_pos_60",
    "momentum_diff", "amount_chg_5d",
    "ret_20d_rank", "ret_60d_rank", "vol_z20d_rank", "amount_chg_5d_rank",
    "rz_balance_rank", "rz_chg_5d_pct_rank",
    "ret_20d_tdx_l1_rel", "ret_60d_tdx_l1_rel",
    "vol_z20d_tdx_l1_rel", "amount_chg_5d_tdx_l1_rel",
    "rz_balance_to_amount20",
    "inst_event_count_30d", "inst_event_count_60d",
    "exec_buy_count_90d", "exec_buy_ge1_count_90d",
    "lhb_inst_buy_count_30d", "lhb_inst_buy_count_60d",
    "jgdy_count_60d", "dzjy_count_60d",
    "days_since_exec_buy", "days_since_lhb",
    "shareholder_count_qoq", "inst_count_qoq",
    "fund_count_qoq", "qfii_count_qoq",
    "yjyg_lower_pct", "yjyg_upper_pct", "roe", "eps_basic",
    "hs300_ret_20d", "hs300_ret_60d", "regime_flag",
    "forward_ret_20d", "built_at",
]

INTEGER_COLS = {
    "inst_event_count_30d", "inst_event_count_60d",
    "exec_buy_count_90d", "exec_buy_ge1_count_90d",
    "lhb_inst_buy_count_30d", "lhb_inst_buy_count_60d",
    "jgdy_count_60d", "dzjy_count_60d",
    "days_since_exec_buy", "days_since_lhb",
}

TEXT_COLS = {"stock_code", "date", "regime_flag", "built_at"}
REAL_COLS = set(KEEP_COLS) - INTEGER_COLS - TEXT_COLS


def execute_script(duck, sql: str) -> None:
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            duck.execute(stmt)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _date_expr(expr: str) -> str:
    """Normalize YYYYMMDD and ISO-ish date strings to DATE."""
    return (
        f"CASE "
        f"WHEN {expr} IS NULL THEN NULL "
        f"WHEN REGEXP_MATCHES(CAST({expr} AS VARCHAR), '^\\d{{8}}$') "
        f"THEN STRPTIME(CAST({expr} AS VARCHAR), '%Y%m%d')::DATE "
        f"ELSE CAST({expr} AS DATE) END"
    )


def _date_text(expr: str) -> str:
    return f"STRFTIME({_date_expr(expr)}, '%Y-%m-%d')"


def _replace_temp_table(duck, name: str, select_sql: str, params: list | tuple | None = None) -> None:
    temp_name = f"__tmp_replace_{name}"
    duck.execute(f"DROP TABLE IF EXISTS {_quote_ident(temp_name)}")
    duck.execute(f"CREATE TEMP TABLE {_quote_ident(temp_name)} AS {select_sql}", params or [])
    duck.execute(f"DROP TABLE IF EXISTS {_quote_ident(name)}")
    duck.execute(f"ALTER TABLE {_quote_ident(temp_name)} RENAME TO {_quote_ident(name)}")


def _table_columns(duck, table: str) -> list[str]:
    return [row[0] for row in duck.execute(f"DESCRIBE {_quote_ident(table)}").fetchall()]


def _row_count(duck, table: str) -> int:
    return int(duck.execute(f"SELECT COUNT(*) FROM {_quote_ident(table)}").fetchone()[0])


def _add_literal_columns(duck, definitions: dict[str, str]) -> None:
    additions = ", ".join(f"{expr} AS {_quote_ident(col)}" for col, expr in definitions.items())
    _replace_temp_table(duck, "current_panel", f"SELECT *, {additions} FROM current_panel")


def _rolling_event_count(duck, evt_sql: str, count_col: str, windows: list[int]) -> None:
    select_cols = ", ".join(
        f"COALESCE(r.{_quote_ident(count_col + '_' + str(w) + 'd')}, 0)::INTEGER "
        f"AS {_quote_ident(count_col + '_' + str(w) + 'd')}"
        for w in windows
    )
    rolled_cols = ", ".join(
        f"SUM(n) OVER (PARTITION BY stock_code ORDER BY date ROWS {w - 1} PRECEDING) "
        f"AS {_quote_ident(count_col + '_' + str(w) + 'd')}"
        for w in windows
    )
    _replace_temp_table(
        duck,
        "current_panel",
        f"""
        WITH ev_raw AS ({evt_sql}),
        ev_daily AS (
            SELECT stock_code, {_date_text('event_date')} AS date, COUNT(*)::INTEGER AS n
            FROM ev_raw
            WHERE stock_code IS NOT NULL AND event_date IS NOT NULL
            GROUP BY stock_code, {_date_text('event_date')}
        ),
        panel_ev AS (
            SELECT p.stock_code, p.date, COALESCE(e.n, 0) AS n
            FROM current_panel p
            LEFT JOIN ev_daily e ON e.stock_code = p.stock_code AND e.date = p.date
        ),
        rolled AS (
            SELECT stock_code, date, {rolled_cols}
            FROM panel_ev
        )
        SELECT p.*, {select_cols}
        FROM current_panel p
        LEFT JOIN rolled r ON r.stock_code = p.stock_code AND r.date = p.date
        """,
    )


def _days_since_event(duck, ev_sql: str, suffix: str) -> None:
    col = f"days_since_{suffix}"
    _replace_temp_table(
        duck,
        "current_panel",
        f"""
        WITH ev AS ({ev_sql}),
        ds AS (
            SELECT p.stock_code, p.date, {_date_expr('p.date')} AS date_dt,
                   MAX(CASE WHEN {_date_expr('e.event_date')} <= {_date_expr('p.date')}
                            THEN {_date_expr('e.event_date')} END) AS last_ev
            FROM current_panel p
            LEFT JOIN ev e ON e.stock_code = p.stock_code
            GROUP BY p.stock_code, p.date
        )
        SELECT p.*,
               COALESCE(CASE WHEN ds.last_ev IS NULL THEN -1
                             ELSE (ds.date_dt - ds.last_ev)::INTEGER END, -1) AS {_quote_ident(col)}
        FROM current_panel p
        LEFT JOIN ds ON ds.stock_code = p.stock_code AND ds.date = p.date
        """,
    )


def _clean_select_expr(col: str, params: list[str]) -> str:
    q = _quote_ident(col)
    if col == "built_at":
        params.append(datetime.utcnow().isoformat())
        return "? AS built_at"
    if col in REAL_COLS:
        return (
            f"CASE WHEN {q} IS NULL OR NOT ISFINITE(CAST({q} AS DOUBLE)) "
            f"OR ABS(CAST({q} AS DOUBLE)) > {REAL_ABS_LIMIT} "
            f"THEN NULL ELSE CAST({q} AS DOUBLE) END AS {q}"
        )
    if col in INTEGER_COLS:
        return f"CAST({q} AS INTEGER) AS {q}"
    return q


def _insert_fact_panel(duck) -> dict[str, int]:
    panel_cols = set(_table_columns(duck, "current_panel"))
    keep = [col for col in KEEP_COLS if col == "built_at" or col in panel_cols]
    params: list[str] = []
    select_exprs = [_clean_select_expr(col, params) for col in keep]
    execute_script(duck, PANEL_DDL)
    logger.info("DuckDB INSERT INTO fact_feature_panel SELECT FROM current_panel ...")
    duck.execute(
        f"""
        INSERT INTO fact_feature_panel ({', '.join(_quote_ident(col) for col in keep)})
        SELECT {', '.join(select_exprs)}
        FROM current_panel
        """,
        params,
    )
    row = duck.execute("""
        SELECT COUNT(*), COUNT(DISTINCT stock_code), COUNT(DISTINCT date),
               SUM(CASE WHEN forward_ret_20d IS NOT NULL THEN 1 ELSE 0 END)
        FROM fact_feature_panel
    """).fetchone()
    return {
        "rows": int(row[0] or 0),
        "codes": int(row[1] or 0),
        "dates": int(row[2] or 0),
        "label_non_null": int(row[3] or 0),
    }


def build_panel(start_date: str) -> dict[str, int]:
    duck = get_duck(writable=True)
    t0 = time.time()

    logger.info("Step 1: Pillar B price/volume + Alpha158-inspired features")
    _replace_temp_table(
        duck,
        "current_panel",
        f"""
        WITH px AS (
            SELECT code as stock_code, date,
                   open, high, low, close, volume, amount,
                   (close / NULLIF(LAG(close, 1) OVER (PARTITION BY code ORDER BY date), 0) - 1) AS close_ret_1d
            FROM ({KLINE_DAILY_QFQ_SQL}) AS kline
            WHERE date >= ?
        ),
        features AS (
            SELECT
                stock_code, date, close,
                close_ret_1d AS ret_1d,
                (close / NULLIF(LAG(close, 5) OVER w, 0) - 1) AS ret_5d,
                (close / NULLIF(LAG(close, 20) OVER w, 0) - 1) AS ret_20d,
                (close / NULLIF(LAG(close, 60) OVER w, 0) - 1) AS ret_60d,
                (volume - AVG(volume) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING))
                    / NULLIF(STDDEV_SAMP(volume) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING), 0)
                    AS vol_z20d,
                (close / NULLIF(AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS 4 PRECEDING), 0) - 1) AS ma_ratio_5,
                (close / NULLIF(AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING), 0) - 1) AS ma_ratio_20,
                (close / NULLIF(AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS 59 PRECEDING), 0) - 1) AS ma_ratio_60,
                (close / NULLIF(AVG(close) OVER (PARTITION BY stock_code ORDER BY date ROWS 249 PRECEDING), 0) - 1) AS ma_ratio_250,
                ((close - open) / NULLIF(open, 0)) AS kmid,
                ((high - low) / NULLIF(open, 0)) AS klen,
                ((high - GREATEST(open, close)) / NULLIF(open, 0)) AS kup,
                ((LEAST(open, close) - low) / NULLIF(open, 0)) AS klow,
                ((2 * close - high - low) / NULLIF(open, 0)) AS ksft,
                (AVG(volume) OVER (PARTITION BY stock_code ORDER BY date ROWS 4 PRECEDING)
                 / NULLIF(AVG(volume) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING), 0)) AS vol_ratio_5_20,
                STDDEV_SAMP(close_ret_1d) OVER (PARTITION BY stock_code ORDER BY date ROWS 4 PRECEDING) AS vol_std_5d,
                STDDEV_SAMP(close_ret_1d) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING) AS vol_std_20d,
                (close - MIN(low) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING))
                    / NULLIF(MAX(high) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING)
                             - MIN(low) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING), 0) AS range_pos_20,
                (close - MIN(low) OVER (PARTITION BY stock_code ORDER BY date ROWS 59 PRECEDING))
                    / NULLIF(MAX(high) OVER (PARTITION BY stock_code ORDER BY date ROWS 59 PRECEDING)
                             - MIN(low) OVER (PARTITION BY stock_code ORDER BY date ROWS 59 PRECEDING), 0) AS range_pos_60,
                (amount / NULLIF(LAG(amount, 5) OVER w, 0) - 1) AS amount_chg_5d,
                AVG(amount) OVER (PARTITION BY stock_code ORDER BY date ROWS 19 PRECEDING) AS amount_ma20
            FROM px
            WINDOW w AS (PARTITION BY stock_code ORDER BY date)
        )
        SELECT *, ret_5d - ret_20d AS momentum_diff
        FROM features
        """,
        [start_date],
    )
    logger.info("Pillar B done: %d rows, %.1fs", _row_count(duck, "current_panel"), time.time() - t0)

    logger.info("Step 2: margin join")
    _replace_temp_table(
        duck,
        "current_panel",
        """
        WITH margin AS (
            SELECT stock_code,
                   {margin_date} AS date,
                   rz_balance,
                   (
                       rz_balance
                       / NULLIF(LAG(rz_balance, 5) OVER (PARTITION BY stock_code ORDER BY {margin_date}), 0)
                       - 1
                   ) AS rz_chg_5d_pct
            FROM smartmoney.raw_margin_daily
        )
        SELECT p.*, m.rz_balance, m.rz_chg_5d_pct
        FROM current_panel p
        LEFT JOIN margin m
          ON p.stock_code = m.stock_code
         AND p.date = STRFTIME(m.date, '%Y-%m-%d')
        """.format(margin_date=_date_expr("trade_date")),
    )

    logger.info("Step 3: forward_ret_20d label")
    _replace_temp_table(
        duck,
        "current_panel",
        """
        SELECT *,
               (LEAD(close, 21) OVER w / NULLIF(LEAD(close, 1) OVER w, 0) - 1) AS forward_ret_20d
        FROM current_panel
        WINDOW w AS (PARTITION BY stock_code ORDER BY date)
        """,
    )

    logger.info("Step 4: event rolling counts")
    _rolling_event_count(
        duck,
        "SELECT stock_code, notice_date AS event_date FROM smartmoney.fact_institution_event",
        "inst_event_count", [30, 60],
    )
    _rolling_event_count(
        duck,
        "SELECT stock_code, notice_date AS event_date FROM smartmoney.fact_executive_trade_event WHERE direction='buy'",
        "exec_buy_count", [90],
    )
    _rolling_event_count(
        duck,
        "SELECT stock_code, notice_date AS event_date FROM smartmoney.fact_executive_trade_event "
        "WHERE direction='buy' AND total_change_pct_total >= 1.0",
        "exec_buy_ge1_count", [90],
    )
    _rolling_event_count(
        duck,
        "SELECT stock_code, trade_date AS event_date FROM smartmoney.fact_lhb_event WHERE is_inst_net_buy=1",
        "lhb_inst_buy_count", [30, 60],
    )
    try:
        _rolling_event_count(
            duck,
            "SELECT stock_code, notice_date AS event_date FROM smartmoney.fact_jgdy_event",
            "jgdy_count", [60],
        )
    except Exception as e:
        logger.warning("jgdy rolling skip: %s", e)
        _add_literal_columns(duck, {"jgdy_count_60d": "0::INTEGER"})
    try:
        _rolling_event_count(
            duck,
            "SELECT stock_code, trade_date AS event_date FROM smartmoney.fact_dzjy_event",
            "dzjy_count", [60],
        )
    except Exception as e:
        logger.warning("dzjy rolling skip: %s", e)
        _add_literal_columns(duck, {"dzjy_count_60d": "0::INTEGER"})

    logger.info("Step 5: days_since features")
    for ev_sql, suffix in [
        ("SELECT stock_code, notice_date AS event_date FROM smartmoney.fact_executive_trade_event WHERE direction='buy'", "exec_buy"),
        ("SELECT stock_code, trade_date AS event_date FROM smartmoney.fact_lhb_event WHERE is_inst_net_buy=1", "lhb"),
    ]:
        try:
            _days_since_event(duck, ev_sql, suffix)
        except Exception as e:
            logger.warning("days_since %s skip: %s", suffix, e)
            _add_literal_columns(duck, {f"days_since_{suffix}": "-1::INTEGER"})

    logger.info("Step 6: fundamentals ASOF join")
    _replace_temp_table(
        duck,
        "current_panel",
        """
        WITH ffq AS (
            SELECT stock_code,
                   STRFTIME(STRPTIME(report_date, '%Y%m%d'), '%Y-%m-%d') AS date,
                   shareholder_count, inst_count, fund_count, qfii_count,
                   yjyg_lower_pct, yjyg_upper_pct, roe, eps_basic,
                   (shareholder_count / NULLIF(LAG(shareholder_count) OVER w, 0) - 1) AS shareholder_count_qoq,
                   (inst_count / NULLIF(LAG(inst_count) OVER w, 0) - 1) AS inst_count_qoq,
                   (fund_count / NULLIF(LAG(fund_count) OVER w, 0) - 1) AS fund_count_qoq,
                   (qfii_count / NULLIF(LAG(qfii_count) OVER w, 0) - 1) AS qfii_count_qoq
            FROM smartmoney.fact_fundamental_quarterly
            WINDOW w AS (PARTITION BY stock_code ORDER BY report_date)
        )
        SELECT p.*,
               f.shareholder_count_qoq, f.inst_count_qoq, f.fund_count_qoq, f.qfii_count_qoq,
               f.yjyg_lower_pct, f.yjyg_upper_pct, f.roe, f.eps_basic
        FROM current_panel p
        ASOF LEFT JOIN ffq f
          ON p.stock_code = f.stock_code AND p.date >= f.date
        """,
    )

    logger.info("Step 7: market regime")
    _replace_temp_table(
        duck,
        "current_panel",
        f"""
        WITH regime AS (
            SELECT date,
                   (close / NULLIF(LAG(close, 20) OVER (ORDER BY date), 0) - 1) AS hs300_ret_20d,
                   (close / NULLIF(LAG(close, 60) OVER (ORDER BY date), 0) - 1) AS hs300_ret_60d
            FROM ({KLINE_DAILY_QFQ_SQL}) AS kline
            WHERE code='510300'
        ),
        regime_labeled AS (
            SELECT *,
                   CASE
                     WHEN hs300_ret_20d IS NULL THEN 'na'
                     WHEN hs300_ret_20d > 0.03 THEN 'up'
                     WHEN hs300_ret_20d < -0.03 THEN 'down'
                     ELSE 'flat'
                   END AS regime_flag
            FROM regime
        )
        SELECT p.*, r.hs300_ret_20d, r.hs300_ret_60d, r.regime_flag
        FROM current_panel p
        LEFT JOIN regime_labeled r ON r.date = p.date
        """,
    )

    logger.info("Step 8: cross-sectional rank / industry-relative / margin normalization")
    _replace_temp_table(
        duck,
        "current_panel",
        """
        WITH ind AS (
            SELECT stock_code, tdx_l1 FROM smartmoney.dim_stock_tdx_industry
        ),
        joined AS (
            SELECT p.*, ind.tdx_l1
            FROM current_panel p
            LEFT JOIN ind ON ind.stock_code = p.stock_code
        )
        SELECT *,
               CASE WHEN ret_20d IS NULL THEN NULL ELSE PERCENT_RANK() OVER (PARTITION BY date ORDER BY ret_20d NULLS LAST) END AS ret_20d_rank,
               CASE WHEN ret_60d IS NULL THEN NULL ELSE PERCENT_RANK() OVER (PARTITION BY date ORDER BY ret_60d NULLS LAST) END AS ret_60d_rank,
               CASE WHEN vol_z20d IS NULL THEN NULL ELSE PERCENT_RANK() OVER (PARTITION BY date ORDER BY vol_z20d NULLS LAST) END AS vol_z20d_rank,
               CASE WHEN amount_chg_5d IS NULL THEN NULL ELSE PERCENT_RANK() OVER (PARTITION BY date ORDER BY amount_chg_5d NULLS LAST) END AS amount_chg_5d_rank,
               CASE WHEN rz_balance IS NULL THEN NULL ELSE PERCENT_RANK() OVER (PARTITION BY date ORDER BY rz_balance NULLS LAST) END AS rz_balance_rank,
               CASE WHEN rz_chg_5d_pct IS NULL THEN NULL ELSE PERCENT_RANK() OVER (PARTITION BY date ORDER BY rz_chg_5d_pct NULLS LAST) END AS rz_chg_5d_pct_rank,
               ret_20d - AVG(ret_20d) OVER (PARTITION BY date, tdx_l1) AS ret_20d_tdx_l1_rel,
               ret_60d - AVG(ret_60d) OVER (PARTITION BY date, tdx_l1) AS ret_60d_tdx_l1_rel,
               vol_z20d - AVG(vol_z20d) OVER (PARTITION BY date, tdx_l1) AS vol_z20d_tdx_l1_rel,
               amount_chg_5d - AVG(amount_chg_5d) OVER (PARTITION BY date, tdx_l1) AS amount_chg_5d_tdx_l1_rel,
               rz_balance / NULLIF(amount_ma20, 0) AS rz_balance_to_amount20
        FROM joined
        """,
    )

    logger.info("Step 9: write fact_feature_panel")
    summary = _insert_fact_panel(duck)
    logger.info(
        "fact_feature_panel: rows=%d codes=%d dates=%d label_non_null=%d elapsed=%.1f min",
        summary["rows"], summary["codes"], summary["dates"], summary["label_non_null"],
        (time.time() - t0) / 60,
    )
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2023-01-01")
    args = parser.parse_args()
    build_panel(args.start)


if __name__ == "__main__":
    main()
