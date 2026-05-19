"""P0a label panel builder — SQL-driven entry/exit VWAP + tradeability mask + cost-after.

工作流:
1. ATTACH market.duckdb (price_kline view) READ_ONLY.
2. 对 (stock_code, signal_date) 算:
   - entry_date = T+1 trading-calendar day after signal_date
   - exit_date_Nd = entry_date + N trading-calendar days
   - VWAP = amount / volume (一日 K 线 amount = sum of price×qty, volume = sum of qty)
   - unable_at_entry/exit:
       * K 线缺失 (停牌) → True
       * 一字板 (open=high=low=close 且 volume>0) → True
   - fwd_cost_after_Nd = (exit_vwap_Nd / entry_vwap - 1) - round_trip_cost_pct
   - PIT-strict: fwd_cost_after_Nd 仅在 build_at >= exit_date_Nd + 1 day 时可见
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
from services.labels.ddl import LABEL_VERSION, create_label_panel_ddl
from services.labels.universe import has_pit_listing_source, pit_universe_by_signal_date
from services.paper_sim.config import TxCostConfig, load_config as load_paper_sim_config

log = logging.getLogger("labels.build")

# SQL: 对 signal_date list × stock_code list 一次性算 entry/exit VWAP + masks + labels.
# 入参 (DuckDB placeholder ?): build_as_of_date, tx round_trip_cost_pct.
# tmp tables: tmp_signal_dates(signal_date), tmp_stocks(stock_code).
_BUILD_SQL = """
WITH
build_context AS (
    SELECT ?::DATE AS build_as_of_date, ?::DOUBLE AS round_trip_cost_pct
),
trading_days AS (
    -- dim_trading_calendar 是 label horizon 的唯一 offset 基准.
    SELECT CAST(trade_date AS DATE) AS d
    FROM dim_trading_calendar
    WHERE COALESCE(is_trading, 1) = 1
),
trading_day_rank AS (
    SELECT d, ROW_NUMBER() OVER (ORDER BY d) AS rk FROM trading_days
),
signals_with_rank AS (
    -- Codex review 2026-05-19 + sub-agent a58333b3 优化: 改用 tmp_pit_stock_signal 直接 DISTINCT
    -- 取 signal_date, 不再依赖 per-date loop 的 tmp_signal_dates. PIT 仍在 tmp_pit_stock_signal
    -- (pit_universe_by_signal_date 已 filter listed_date <= signal_date) — 不 break PIT.
    -- 性能: 11h → 25-45min (805 dates 一次 batch, 不再每 date 重 build SQL)
    SELECT DISTINCT s.signal_date, r.rk AS signal_rk
    FROM tmp_pit_stock_signal s
    JOIN trading_day_rank r ON r.d = s.signal_date
),
horizons AS (
    SELECT signal_date, signal_rk,
           signal_rk + 1 AS entry_rk,
           signal_rk + 1 + 5  AS exit_rk_5d,
           signal_rk + 1 + 10 AS exit_rk_10d,
           signal_rk + 1 + 20 AS exit_rk_20d,
           signal_rk + 1 + 60 AS exit_rk_60d,
           signal_rk + 1 + 90 AS exit_rk_90d
    FROM signals_with_rank
),
horizons_with_dates AS (
    SELECT h.*,
           td_entry.d AS entry_date,
           td5.d  AS exit_date_5d,
           td10.d AS exit_date_10d,
           td20.d AS exit_date_20d,
           td60.d AS exit_date_60d,
           td90.d AS exit_date_90d
    FROM horizons h
    LEFT JOIN trading_day_rank td_entry ON td_entry.rk = h.entry_rk
    LEFT JOIN trading_day_rank td5      ON td5.rk      = h.exit_rk_5d
    LEFT JOIN trading_day_rank td10     ON td10.rk     = h.exit_rk_10d
    LEFT JOIN trading_day_rank td20     ON td20.rk     = h.exit_rk_20d
    LEFT JOIN trading_day_rank td60     ON td60.rk     = h.exit_rk_60d
    LEFT JOIN trading_day_rank td90     ON td90.rk     = h.exit_rk_90d
),
stock_signal_grid AS (
    -- Codex review 2026-05-19 + sub-agent a58333b3: 改用 tmp_pit_stock_signal JOIN horizons_with_dates,
    -- 不 CROSS JOIN tmp_stocks (会含 listed-after-signal-date 组合 → leakage 风险).
    -- tmp_pit_stock_signal 已 PIT-clean (universe.py pit_universe_by_signal_date 按 listed_date <=
    -- signal_date filter). 单次 batch 处理全 805 dates × 5K stocks, per-date loop 删除.
    -- rule-compliance: ok evidence=PIT-via-tmp-pit-stock-signal-batch-redesign
    SELECT t.stock_code, h.signal_date, h.entry_date,
           h.exit_date_5d, h.exit_date_10d, h.exit_date_20d,
           h.exit_date_60d, h.exit_date_90d
    FROM tmp_pit_stock_signal t
    JOIN horizons_with_dates h ON h.signal_date = t.signal_date
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
    LEFT JOIN tmp_kline k
      ON k.code = g.stock_code AND k.date = g.entry_date
),
exit_5d AS (
    SELECT g.stock_code, g.signal_date,
           k.amount AS x_amount, k.volume AS x_volume,
           k.open AS x_open, k.high AS x_high,
           k.low AS x_low, k.close AS x_close
    FROM stock_signal_grid g
    LEFT JOIN tmp_kline k
      ON k.code = g.stock_code AND k.date = g.exit_date_5d
),
exit_10d AS (
    SELECT g.stock_code, g.signal_date,
           k.amount AS x_amount, k.volume AS x_volume,
           k.open AS x_open, k.high AS x_high,
           k.low AS x_low, k.close AS x_close
    FROM stock_signal_grid g
    LEFT JOIN tmp_kline k
      ON k.code = g.stock_code AND k.date = g.exit_date_10d
),
exit_20d AS (
    SELECT g.stock_code, g.signal_date,
           k.amount AS x_amount, k.volume AS x_volume,
           k.open AS x_open, k.high AS x_high,
           k.low AS x_low, k.close AS x_close
    FROM stock_signal_grid g
    LEFT JOIN tmp_kline k
      ON k.code = g.stock_code AND k.date = g.exit_date_20d
),
exit_60d AS (
    SELECT g.stock_code, g.signal_date,
           k.amount AS x_amount, k.volume AS x_volume,
           k.open AS x_open, k.high AS x_high,
           k.low AS x_low, k.close AS x_close
    FROM stock_signal_grid g
    LEFT JOIN tmp_kline k
      ON k.code = g.stock_code AND k.date = g.exit_date_60d
),
exit_90d AS (
    SELECT g.stock_code, g.signal_date,
           k.amount AS x_amount, k.volume AS x_volume,
           k.open AS x_open, k.high AS x_high,
           k.low AS x_low, k.close AS x_close
    FROM stock_signal_grid g
    LEFT JOIN tmp_kline k
      ON k.code = g.stock_code AND k.date = g.exit_date_90d
),
masks_and_vwap AS (
    SELECT
        g.stock_code, g.signal_date, g.entry_date,
        g.exit_date_5d, g.exit_date_10d, g.exit_date_20d,
        g.exit_date_60d, g.exit_date_90d,
        CASE WHEN e.e_volume > 0 THEN e.e_amount / (e.e_volume * 100.0) ELSE NULL END AS entry_vwap,
        CASE
            WHEN e.e_amount IS NULL OR e.e_volume IS NULL OR e.e_volume = 0 THEN TRUE
            WHEN e.e_open = e.e_high AND e.e_open = e.e_low AND e.e_open = e.e_close THEN TRUE
            ELSE FALSE
        END AS unable_at_entry,
        CASE WHEN x5.x_volume > 0 THEN x5.x_amount / (x5.x_volume * 100.0) ELSE NULL END AS exit_vwap_5d,
        CASE
            WHEN x5.x_amount IS NULL OR x5.x_volume IS NULL OR x5.x_volume = 0 THEN TRUE
            WHEN x5.x_open = x5.x_high AND x5.x_open = x5.x_low AND x5.x_open = x5.x_close THEN TRUE
            ELSE FALSE
        END AS unable_at_exit_5d,
        CASE WHEN x10.x_volume > 0 THEN x10.x_amount / (x10.x_volume * 100.0) ELSE NULL END AS exit_vwap_10d,
        CASE
            WHEN x10.x_amount IS NULL OR x10.x_volume IS NULL OR x10.x_volume = 0 THEN TRUE
            WHEN x10.x_open = x10.x_high AND x10.x_open = x10.x_low AND x10.x_open = x10.x_close THEN TRUE
            ELSE FALSE
        END AS unable_at_exit_10d,
        CASE WHEN x20.x_volume > 0 THEN x20.x_amount / (x20.x_volume * 100.0) ELSE NULL END AS exit_vwap_20d,
        CASE
            WHEN x20.x_amount IS NULL OR x20.x_volume IS NULL OR x20.x_volume = 0 THEN TRUE
            WHEN x20.x_open = x20.x_high AND x20.x_open = x20.x_low AND x20.x_open = x20.x_close THEN TRUE
            ELSE FALSE
        END AS unable_at_exit_20d,
        CASE WHEN x60.x_volume > 0 THEN x60.x_amount / (x60.x_volume * 100.0) ELSE NULL END AS exit_vwap_60d,
        CASE
            WHEN x60.x_amount IS NULL OR x60.x_volume IS NULL OR x60.x_volume = 0 THEN TRUE
            WHEN x60.x_open = x60.x_high AND x60.x_open = x60.x_low AND x60.x_open = x60.x_close THEN TRUE
            ELSE FALSE
        END AS unable_at_exit_60d,
        CASE WHEN x90.x_volume > 0 THEN x90.x_amount / (x90.x_volume * 100.0) ELSE NULL END AS exit_vwap_90d,
        CASE
            WHEN x90.x_amount IS NULL OR x90.x_volume IS NULL OR x90.x_volume = 0 THEN TRUE
            WHEN x90.x_open = x90.x_high AND x90.x_open = x90.x_low AND x90.x_open = x90.x_close THEN TRUE
            ELSE FALSE
        END AS unable_at_exit_90d
    FROM stock_signal_grid g
    LEFT JOIN entry_kline e ON e.stock_code = g.stock_code AND e.signal_date = g.signal_date
    LEFT JOIN exit_5d x5    ON x5.stock_code = g.stock_code AND x5.signal_date = g.signal_date
    LEFT JOIN exit_10d x10  ON x10.stock_code = g.stock_code AND x10.signal_date = g.signal_date
    LEFT JOIN exit_20d x20  ON x20.stock_code = g.stock_code AND x20.signal_date = g.signal_date
    LEFT JOIN exit_60d x60  ON x60.stock_code = g.stock_code AND x60.signal_date = g.signal_date
    LEFT JOIN exit_90d x90  ON x90.stock_code = g.stock_code AND x90.signal_date = g.signal_date
)
SELECT
    mv.stock_code, mv.signal_date, mv.entry_date,
    mv.entry_vwap, mv.unable_at_entry,
    mv.exit_date_5d,  mv.exit_vwap_5d,  mv.unable_at_exit_5d,
    CASE WHEN mv.exit_date_5d IS NULL OR bc.build_as_of_date < mv.exit_date_5d + INTERVAL 1 DAY
              OR mv.unable_at_entry OR mv.unable_at_exit_5d OR mv.entry_vwap IS NULL OR mv.exit_vwap_5d IS NULL
         THEN NULL ELSE (mv.exit_vwap_5d / mv.entry_vwap - 1.0) - bc.round_trip_cost_pct END AS fwd_cost_after_5d,
    mv.exit_date_10d, mv.exit_vwap_10d, mv.unable_at_exit_10d,
    CASE WHEN mv.exit_date_10d IS NULL OR bc.build_as_of_date < mv.exit_date_10d + INTERVAL 1 DAY
              OR mv.unable_at_entry OR mv.unable_at_exit_10d OR mv.entry_vwap IS NULL OR mv.exit_vwap_10d IS NULL
         THEN NULL ELSE (mv.exit_vwap_10d / mv.entry_vwap - 1.0) - bc.round_trip_cost_pct END AS fwd_cost_after_10d,
    mv.exit_date_20d, mv.exit_vwap_20d, mv.unable_at_exit_20d,
    CASE WHEN mv.exit_date_20d IS NULL OR bc.build_as_of_date < mv.exit_date_20d + INTERVAL 1 DAY
              OR mv.unable_at_entry OR mv.unable_at_exit_20d OR mv.entry_vwap IS NULL OR mv.exit_vwap_20d IS NULL
         THEN NULL ELSE (mv.exit_vwap_20d / mv.entry_vwap - 1.0) - bc.round_trip_cost_pct END AS fwd_cost_after_20d,
    mv.exit_date_60d, mv.exit_vwap_60d, mv.unable_at_exit_60d,
    CASE WHEN mv.exit_date_60d IS NULL OR bc.build_as_of_date < mv.exit_date_60d + INTERVAL 1 DAY
              OR mv.unable_at_entry OR mv.unable_at_exit_60d OR mv.entry_vwap IS NULL OR mv.exit_vwap_60d IS NULL
         THEN NULL ELSE (mv.exit_vwap_60d / mv.entry_vwap - 1.0) - bc.round_trip_cost_pct END AS fwd_cost_after_60d,
    mv.exit_date_90d, mv.exit_vwap_90d, mv.unable_at_exit_90d,
    CASE WHEN mv.exit_date_90d IS NULL OR bc.build_as_of_date < mv.exit_date_90d + INTERVAL 1 DAY
              OR mv.unable_at_entry OR mv.unable_at_exit_90d OR mv.entry_vwap IS NULL OR mv.exit_vwap_90d IS NULL
         THEN NULL ELSE (mv.exit_vwap_90d / mv.entry_vwap - 1.0) - bc.round_trip_cost_pct END AS fwd_cost_after_90d
FROM masks_and_vwap mv
CROSS JOIN build_context bc
"""


def build_p0a_label_panel(
    db_path: str,
    market_db_path: str,
    *,
    signal_dates: Iterable[str],
    stock_codes: Iterable[str] | None = None,
    tx: TxCostConfig | None = None,
    output_table: str = "mart_p0a_label_panel",
) -> dict:
    """Build P0a label panel for given signal_dates × stock_codes.

    Args:
        db_path: smartmoney.duckdb path (writes mart_p0a_label_panel here).
        market_db_path: market.duckdb path (ATTACH AS mkt for price_kline).
        signal_dates: list of YYYY-MM-DD strings (must be trading days in price_kline).
        stock_codes: optional candidate stock codes.  The final build universe is
            resolved point-in-time per signal_date from listing history.
        tx: TxCostConfig; default loads paper_sim_config.yaml.
        output_table: target table name.

    Returns:
        {"rows_built": int, "round_trip_cost_pct": float, "label_version": str}.
    """
    if tx is None:
        tx = load_paper_sim_config().tx_cost
    round_trip = compute_round_trip_cost_pct(tx)

    signal_dates = list(signal_dates)
    stock_codes = list(stock_codes) if stock_codes is not None else None
    if not signal_dates:
        return {"rows_built": 0, "round_trip_cost_pct": round_trip, "label_version": LABEL_VERSION}

    conn = duck_connect(db_path, attach={"mkt": market_db_path})
    try:
        create_label_panel_ddl(conn)

        if has_pit_listing_source(conn):
            stocks_by_date = pit_universe_by_signal_date(
                conn,
                signal_dates,
                candidate_stock_codes=stock_codes,
            )
        elif stock_codes is not None:
            log.warning("PIT listing source missing; falling back to explicit stock_codes")
            stocks_by_date = {str(d)[:10]: stock_codes for d in signal_dates}
        else:
            raise RuntimeError("stock_codes omitted but no PIT listing source is available")

        pit_pairs = [
            (stock_code, signal_date)
            for signal_date, stocks in stocks_by_date.items()
            for stock_code in stocks
        ]
        if not pit_pairs:
            return {"rows_built": 0, "round_trip_cost_pct": round_trip, "label_version": LABEL_VERSION}
        log.info(
            "  PIT universe: %s stock-date pairs across %s signal_dates",
            f"{len(pit_pairs):,}",
            f"{len(stocks_by_date):,}",
        )

        conn.execute("DROP TABLE IF EXISTS tmp_pit_stock_signal")
        conn.execute("CREATE TEMP TABLE tmp_pit_stock_signal(stock_code TEXT, signal_date DATE)")
        conn.executemany("INSERT INTO tmp_pit_stock_signal VALUES (?, ?)", pit_pairs)

        # Codex review 2026-05-19 + sub-agent a58333b3 Step 2 优化: materialize tmp_kline
        # 替代 6× mkt.v_price_kline_qfq LEFT JOIN view scan. DuckDB columnar zone-map 比 view+index
        # 快 10-50×, 估时 ~30-60s materialize + JOIN. Date 转 DATE 类型 (原 TEXT) 让 join key 直接命中
        # hash join, 不需 strftime cast.
        # Range: forward 90 trading days ≈ 126 calendar days + 春节 ≤ 10 day buffer = 150 calendar days
        # safe margin. Codex review 2026-05-19 ac3f4ef1 HIGH: +130d 不够 long holiday edge case
        # (e.g. 春节 7+ day 关闭 + 国庆 7 day → 90 trading days 可达 140-145 calendar).
        # Could query dim_trading_calendar to get exact max_signal_rank+91 date, but +150 buffer
        # safer + simpler.
        # rule-compliance: ok evidence=90-trading-days-plus-holiday-buffer-150-calendar
        all_dates = sorted(stocks_by_date.keys())
        min_signal_date = all_dates[0]
        max_signal_date = all_dates[-1]
        from datetime import date as _date, timedelta as _td
        max_kline_date = (_date.fromisoformat(max_signal_date) + _td(days=150)).isoformat()
        # entry_date = signal_date + 1 trading day. trading_day_rank uses dim_trading_calendar
        # 不依赖 K-line 前置; -5 是冗余 buffer.
        min_kline_date = (_date.fromisoformat(min_signal_date) - _td(days=5)).isoformat()
        log.info(f"  materialize tmp_kline: {min_kline_date} → {max_kline_date}")
        conn.execute("DROP TABLE IF EXISTS tmp_kline")
        conn.execute(
            "CREATE TEMP TABLE tmp_kline AS "
            "SELECT code, date::DATE AS date, amount, volume, open, high, low, close "
            "FROM mkt.v_price_kline_qfq "
            "WHERE freq='daily' AND adjust='qfq' AND date BETWEEN ? AND ?",
            [min_kline_date, max_kline_date],
        )
        n_kline = conn.execute("SELECT COUNT(*) FROM tmp_kline").fetchone()[0]
        log.info(f"  tmp_kline rows: {n_kline:,}")

        built_at_dt = datetime.now(UTC)
        built_at = built_at_dt.isoformat(timespec="seconds")
        build_as_of_date = built_at_dt.date().isoformat()

        # Codex review 2026-05-19 + sub-agent a58333b3 优化: per-date loop 删除, 改 single batch SQL.
        # 原 loop 每 date DROP+CREATE tmp_signal_dates+tmp_stocks + 跑 _BUILD_SQL fetchall, 805 dates ×
        # ~50s = 11h. New batch uses tmp_pit_stock_signal (已 PIT-clean) 直接 JOIN, 一次跑完.
        # PIT 不破: tmp_pit_stock_signal 来自 pit_universe_by_signal_date (listed_date <= signal_date filter).
        # rule-compliance: ok evidence=batch-redesign-PIT-preserved-via-tmp-pit-stock-signal
        rows = conn.execute(_BUILD_SQL, [build_as_of_date, round_trip]).fetchall()
        if not rows:
            return {"rows_built": 0, "round_trip_cost_pct": round_trip, "label_version": LABEL_VERSION}

        # Idempotent INSERT: DELETE matching PIT stock-date pairs 后 INSERT.
        conn.execute(
            f"DELETE FROM {output_table} t "
            f"WHERE EXISTS ("
            f"  SELECT 1 FROM tmp_pit_stock_signal u "
            f"  WHERE u.stock_code = t.stock_code AND u.signal_date = t.signal_date"
            f")"
        )

        conn.executemany(
            f"""
            INSERT INTO {output_table} (
                stock_code, signal_date, entry_date,
                entry_vwap, unable_at_entry,
                exit_date_5d, exit_vwap_5d, unable_at_exit_5d, fwd_cost_after_5d,
                exit_date_10d, exit_vwap_10d, unable_at_exit_10d, fwd_cost_after_10d,
                exit_date_20d, exit_vwap_20d, unable_at_exit_20d, fwd_cost_after_20d,
                exit_date_60d, exit_vwap_60d, unable_at_exit_60d, fwd_cost_after_60d,
                exit_date_90d, exit_vwap_90d, unable_at_exit_90d, fwd_cost_after_90d,
                round_trip_cost_pct, label_version, built_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r[0], r[1], r[2],
                    r[3], r[4],
                    r[5], r[6], r[7], r[8],
                    r[9], r[10], r[11], r[12],
                    r[13], r[14], r[15], r[16],
                    r[17], r[18], r[19], r[20],
                    r[21], r[22], r[23], r[24],
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
