"""P0a label panel builder — SQL-driven entry/exit VWAP + tradeability mask + cost-after.

工作流:
1. ATTACH market.duckdb (price_kline view) READ_ONLY.
2. 对 (stock_code, signal_date) 算:
   - entry_date = T+1 trading day after signal_date
   - exit_date_Nd = entry_date + N trading days
   - VWAP = amount / volume (一日 K 线 amount = sum of price×qty, volume = sum of qty)
   - unable_at_entry/exit:
       * K 线缺失 (停牌) → True
       * 一字板 (open=high=low=close 且 volume>0) → True
   - fwd_cost_after_Nd = (exit_vwap_Nd / entry_vwap - 1) - round_trip_cost_pct
       * 若 unable_at_entry 或 unable_at_exit_Nd 则 NULL
3. INSERT mart_p0a_label_panel (idempotent: DELETE matching signal_dates 后 INSERT).

KEEP universe 守门: signal_date 输入应已 KEEP universe 过滤
(by 调用方; 这里只算 label 不做 universe 决策).

PIT 保证 (Rule 7):
- forward look-up 只在 label 构造时用; feature pipeline 严禁读 mart_p0a_label_panel
  的 entry_vwap / exit_vwap (它们是未来值).
- 模型输入 panel = feature panel (alpha158/risk/financial) + label (LEFT JOIN by
  stock_code + signal_date), 训练时严格 walk-forward 分窗.
"""
from __future__ import annotations

import logging
from datetime import datetime, UTC
from typing import Iterable

from services.duck_adapter import connect as duck_connect
from services.labels.cost_after import compute_round_trip_cost_pct
from services.labels.ddl import create_label_panel_ddl
from services.paper_sim.config import TxCostConfig, load_config as load_paper_sim_config

log = logging.getLogger("labels.build")

LABEL_VERSION = "p0a_v1"

# SQL: 对 signal_date list × stock_code list 一次性算 entry/exit VWAP + masks + labels.
# 入参 (DuckDB placeholder ?): tx round_trip_cost_pct.
# tmp tables: tmp_signal_dates(signal_date), tmp_stocks(stock_code).
_BUILD_SQL = """
WITH
trading_days AS (
    -- 取所有交易日有序 (从 price_kline daily qfq).
    SELECT DISTINCT date::DATE AS d
    FROM mkt.price_kline
    WHERE freq='daily' AND adjust='qfq'
),
trading_day_rank AS (
    SELECT d, ROW_NUMBER() OVER (ORDER BY d) AS rk FROM trading_days
),
signals_with_rank AS (
    SELECT s.signal_date, r.rk AS signal_rk
    FROM tmp_signal_dates s
    JOIN trading_day_rank r ON r.d = s.signal_date
),
horizons AS (
    -- 对每个 signal 算 entry rk + 3 个 exit rk.
    SELECT signal_date, signal_rk,
           signal_rk + 1 AS entry_rk,
           signal_rk + 1 + 5  AS exit_rk_5d,
           signal_rk + 1 + 10 AS exit_rk_10d,
           signal_rk + 1 + 20 AS exit_rk_20d
    FROM signals_with_rank
),
horizons_with_dates AS (
    SELECT h.*,
           td_entry.d AS entry_date,
           td5.d  AS exit_date_5d,
           td10.d AS exit_date_10d,
           td20.d AS exit_date_20d
    FROM horizons h
    LEFT JOIN trading_day_rank td_entry ON td_entry.rk = h.entry_rk
    LEFT JOIN trading_day_rank td5      ON td5.rk      = h.exit_rk_5d
    LEFT JOIN trading_day_rank td10     ON td10.rk     = h.exit_rk_10d
    LEFT JOIN trading_day_rank td20     ON td20.rk     = h.exit_rk_20d
),
stock_signal_grid AS (
    SELECT s.stock_code, h.signal_date, h.entry_date,
           h.exit_date_5d, h.exit_date_10d, h.exit_date_20d
    FROM tmp_stocks s
    CROSS JOIN horizons_with_dates h
),
entry_kline AS (
    SELECT g.stock_code, g.signal_date,
           k.amount AS e_amount,
           k.volume AS e_volume,
           k.open   AS e_open,
           k.high   AS e_high,
           k.low    AS e_low,
           k.close  AS e_close
    FROM stock_signal_grid g
    LEFT JOIN mkt.price_kline k
      ON k.code = g.stock_code AND k.date = strftime(g.entry_date, '%Y-%m-%d')
     AND k.freq='daily' AND k.adjust='qfq'
),
exit_5d AS (
    SELECT g.stock_code, g.signal_date,
           k.amount AS x_amount, k.volume AS x_volume,
           k.open AS x_open, k.high AS x_high,
           k.low AS x_low, k.close AS x_close
    FROM stock_signal_grid g
    LEFT JOIN mkt.price_kline k
      ON k.code = g.stock_code AND k.date = strftime(g.exit_date_5d, '%Y-%m-%d')
     AND k.freq='daily' AND k.adjust='qfq'
),
exit_10d AS (
    SELECT g.stock_code, g.signal_date,
           k.amount AS x_amount, k.volume AS x_volume,
           k.open AS x_open, k.high AS x_high,
           k.low AS x_low, k.close AS x_close
    FROM stock_signal_grid g
    LEFT JOIN mkt.price_kline k
      ON k.code = g.stock_code AND k.date = strftime(g.exit_date_10d, '%Y-%m-%d')
     AND k.freq='daily' AND k.adjust='qfq'
),
exit_20d AS (
    SELECT g.stock_code, g.signal_date,
           k.amount AS x_amount, k.volume AS x_volume,
           k.open AS x_open, k.high AS x_high,
           k.low AS x_low, k.close AS x_close
    FROM stock_signal_grid g
    LEFT JOIN mkt.price_kline k
      ON k.code = g.stock_code AND k.date = strftime(g.exit_date_20d, '%Y-%m-%d')
     AND k.freq='daily' AND k.adjust='qfq'
),
masks_and_vwap AS (
    SELECT
        g.stock_code, g.signal_date, g.entry_date,
        g.exit_date_5d, g.exit_date_10d, g.exit_date_20d,
        -- entry VWAP + mask
        CASE WHEN e.e_volume > 0 THEN e.e_amount / e.e_volume ELSE NULL END AS entry_vwap,
        CASE
            WHEN e.e_amount IS NULL OR e.e_volume IS NULL OR e.e_volume = 0 THEN TRUE
            WHEN e.e_open = e.e_high AND e.e_open = e.e_low AND e.e_open = e.e_close THEN TRUE
            ELSE FALSE
        END AS unable_at_entry,
        -- 5d exit VWAP + mask
        CASE WHEN x5.x_volume > 0 THEN x5.x_amount / x5.x_volume ELSE NULL END AS exit_vwap_5d,
        CASE
            WHEN x5.x_amount IS NULL OR x5.x_volume IS NULL OR x5.x_volume = 0 THEN TRUE
            WHEN x5.x_open = x5.x_high AND x5.x_open = x5.x_low AND x5.x_open = x5.x_close THEN TRUE
            ELSE FALSE
        END AS unable_at_exit_5d,
        -- 10d
        CASE WHEN x10.x_volume > 0 THEN x10.x_amount / x10.x_volume ELSE NULL END AS exit_vwap_10d,
        CASE
            WHEN x10.x_amount IS NULL OR x10.x_volume IS NULL OR x10.x_volume = 0 THEN TRUE
            WHEN x10.x_open = x10.x_high AND x10.x_open = x10.x_low AND x10.x_open = x10.x_close THEN TRUE
            ELSE FALSE
        END AS unable_at_exit_10d,
        -- 20d
        CASE WHEN x20.x_volume > 0 THEN x20.x_amount / x20.x_volume ELSE NULL END AS exit_vwap_20d,
        CASE
            WHEN x20.x_amount IS NULL OR x20.x_volume IS NULL OR x20.x_volume = 0 THEN TRUE
            WHEN x20.x_open = x20.x_high AND x20.x_open = x20.x_low AND x20.x_open = x20.x_close THEN TRUE
            ELSE FALSE
        END AS unable_at_exit_20d
    FROM stock_signal_grid g
    LEFT JOIN entry_kline e ON e.stock_code = g.stock_code AND e.signal_date = g.signal_date
    LEFT JOIN exit_5d x5    ON x5.stock_code = g.stock_code AND x5.signal_date = g.signal_date
    LEFT JOIN exit_10d x10  ON x10.stock_code = g.stock_code AND x10.signal_date = g.signal_date
    LEFT JOIN exit_20d x20  ON x20.stock_code = g.stock_code AND x20.signal_date = g.signal_date
)
SELECT
    stock_code, signal_date, entry_date,
    entry_vwap, unable_at_entry,
    exit_date_5d,  exit_vwap_5d,  unable_at_exit_5d,
    CASE WHEN unable_at_entry OR unable_at_exit_5d OR entry_vwap IS NULL OR exit_vwap_5d IS NULL
         THEN NULL ELSE (exit_vwap_5d / entry_vwap - 1.0) - ? END  AS fwd_cost_after_5d,
    exit_date_10d, exit_vwap_10d, unable_at_exit_10d,
    CASE WHEN unable_at_entry OR unable_at_exit_10d OR entry_vwap IS NULL OR exit_vwap_10d IS NULL
         THEN NULL ELSE (exit_vwap_10d / entry_vwap - 1.0) - ? END AS fwd_cost_after_10d,
    exit_date_20d, exit_vwap_20d, unable_at_exit_20d,
    CASE WHEN unable_at_entry OR unable_at_exit_20d OR entry_vwap IS NULL OR exit_vwap_20d IS NULL
         THEN NULL ELSE (exit_vwap_20d / entry_vwap - 1.0) - ? END AS fwd_cost_after_20d
FROM masks_and_vwap
"""


def build_p0a_label_panel(
    db_path: str,
    market_db_path: str,
    *,
    signal_dates: Iterable[str],
    stock_codes: Iterable[str],
    tx: TxCostConfig | None = None,
    output_table: str = "mart_p0a_label_panel",
) -> dict:
    """Build P0a label panel for given signal_dates × stock_codes.

    Args:
        db_path: smartmoney.duckdb path (writes mart_p0a_label_panel here).
        market_db_path: market.duckdb path (ATTACH AS mkt for price_kline).
        signal_dates: list of YYYY-MM-DD strings (must be trading days in price_kline).
        stock_codes: list of stock codes (caller responsible for KEEP universe filter).
        tx: TxCostConfig; default loads paper_sim_config.yaml.
        output_table: target table name.

    Returns:
        {"rows_built": int, "round_trip_cost_pct": float, "label_version": str}.
    """
    if tx is None:
        tx = load_paper_sim_config().tx_cost
    round_trip = compute_round_trip_cost_pct(tx)

    signal_dates = list(signal_dates)
    stock_codes = list(stock_codes)
    if not signal_dates or not stock_codes:
        return {"rows_built": 0, "round_trip_cost_pct": round_trip, "label_version": LABEL_VERSION}

    conn = duck_connect(db_path, attach={"mkt": market_db_path})
    try:
        create_label_panel_ddl(conn)

        # Stage tmp tables for IN-set filtering (避免 SQL inline 数千 stock/date).
        conn.execute("DROP TABLE IF EXISTS tmp_signal_dates")
        conn.execute("CREATE TEMP TABLE tmp_signal_dates(signal_date DATE)")
        conn.executemany("INSERT INTO tmp_signal_dates VALUES (?)", [(d,) for d in signal_dates])

        conn.execute("DROP TABLE IF EXISTS tmp_stocks")
        conn.execute("CREATE TEMP TABLE tmp_stocks(stock_code TEXT)")
        conn.executemany("INSERT INTO tmp_stocks VALUES (?)", [(c,) for c in stock_codes])

        # Run main build SQL → rows.
        rows = conn.execute(_BUILD_SQL, [round_trip, round_trip, round_trip]).fetchall()
        if not rows:
            return {"rows_built": 0, "round_trip_cost_pct": round_trip, "label_version": LABEL_VERSION}

        built_at = datetime.now(UTC).isoformat(timespec="seconds")

        # Idempotent INSERT: DELETE matching signal_dates 后 INSERT.
        conn.execute(
            f"DELETE FROM {output_table} WHERE signal_date IN (SELECT signal_date FROM tmp_signal_dates) "
            f"  AND stock_code IN (SELECT stock_code FROM tmp_stocks)"
        )

        conn.executemany(
            f"""
            INSERT INTO {output_table} (
                stock_code, signal_date, entry_date,
                entry_vwap, unable_at_entry,
                exit_date_5d, exit_vwap_5d, unable_at_exit_5d, fwd_cost_after_5d,
                exit_date_10d, exit_vwap_10d, unable_at_exit_10d, fwd_cost_after_10d,
                exit_date_20d, exit_vwap_20d, unable_at_exit_20d, fwd_cost_after_20d,
                round_trip_cost_pct, label_version, built_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r[0], r[1], r[2],
                    r[3], r[4],
                    r[5], r[6], r[7], r[8],
                    r[9], r[10], r[11], r[12],
                    r[13], r[14], r[15], r[16],
                    round_trip, LABEL_VERSION, built_at,
                )
                for r in rows
            ],
        )
        # Post-insert governance verify (Phase ψ.γ.dict.2 字典 enforce wire).
        # Lightweight: sample 100 行验证字典约束 (NOT NULL pk / type / outlier_cap / enum).
        verify = _post_insert_governance_verify(conn, output_table, sample_size=100)
        log.info(f"  governance: {verify['passed']}/{verify['total']} rows pass dict; "
                 f"rate={verify['rate']:.4%}")

        return {
            "rows_built": len(rows),
            "round_trip_cost_pct": round_trip,
            "label_version": LABEL_VERSION,
            "built_at": built_at,
            "governance_verify": verify,
        }
    finally:
        conn.close()


def _post_insert_governance_verify(conn, table_name: str, sample_size: int = 100) -> dict:
    """Post-insert field dictionary verify (Phase ψ.γ.dict.2 wire).

    SQL INSERT 完成后 sample N 行, 经 validate_rows_before_insert (skip if 表不在字典).
    返回 {passed, failed, rate, sample_violations}; 不 raise (post-hoc audit).
    """
    try:
        from services.data_governance import validate_rows_before_insert
    except ImportError:
        log.warning("data_governance not importable, skip verify")
        return {"passed": 0, "failed": 0, "total": 0, "rate": 0.0, "violations_sample": []}

    cur = conn._con.execute(f"SELECT * FROM {table_name} ORDER BY built_at DESC LIMIT {sample_size}")
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    if not rows:
        return {"passed": 0, "failed": 0, "total": 0, "rate": 0.0, "violations_sample": []}
    return validate_rows_before_insert(
        rows, cols, table_name,
        max_violation_rate=1.0,        # 不 raise (post-hoc), 仅 log
        skip_missing_table=True,       # 表不在字典则 skip (graceful)
    )
