"""Phase η P3 — 每日盘后 T+1 买入推荐。

逻辑:
  1. 找最新 date 所有公式触发 (fact_technical_trigger)
  2. JOIN fact_signal_context 拿 5 维 today_bin
  3. JOIN mart_stock_formula_optuna 找该股该 variant 在相似桶下历史最佳 hd
  4. 写 mart_daily_formula_buys (含 buy_price / sell_target / expected_dd / confidence)

confidence_score = win_rate × log(1 + n_signals)  (n=5 → 1.79; n=20 → 3.04; n=100 → 4.61)

用法:
  PYTHONPATH=backend python backend/scripts/build_daily_formula_buys.py [--date 2026-05-12]
"""
from __future__ import annotations

import argparse
import logging
import math
import time
from datetime import date as _date, timedelta

from services.db import get_conn
from services.formula_engine.per_stock_ddl import ensure_per_stock_tables
from services.shared_feature_bins_config import DEFAULT_SHARED_FEATURE_BINS_CONFIG


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("build_daily_formula_buys")

VOL_BINS = DEFAULT_SHARED_FEATURE_BINS_CONFIG.vol_bins
AMT_BINS = DEFAULT_SHARED_FEATURE_BINS_CONFIG.amt_bins
P60_BINS = DEFAULT_SHARED_FEATURE_BINS_CONFIG.p60_bins


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="signal_date, default=最新")
    parser.add_argument("--min-confidence", type=float, default=2.0,
                        help="confidence = win_rate × log(1+n); 默认 2.0 (≈ win 70%+n>5 或 win 60%+n>15)")
    args = parser.parse_args()

    t0 = time.time()
    conn = get_conn()
    try:
        ensure_per_stock_tables(conn)

        # 1. 找今日
        if args.date:
            signal_date = args.date
        else:
            r = conn.execute("SELECT MAX(date) FROM fact_technical_trigger").fetchone()
            signal_date = r[0] if r else None
            if not signal_date:
                log.error("无 fact_technical_trigger 数据")
                return
        log.info(f"signal_date = {signal_date}")

        # T+1 buy date: 找 dim_trading_calendar 下个交易日
        buy_date_row = conn.execute(
            """SELECT trade_date FROM dim_trading_calendar
               WHERE trade_date > ? AND is_trading=1 ORDER BY trade_date LIMIT 1""",
            [signal_date],
        ).fetchone()
        buy_date = buy_date_row[0] if buy_date_row else (
            (_date.fromisoformat(signal_date) + timedelta(days=1)).isoformat()
        )
        log.info(f"buy_date    = {buy_date} (T+1)")

        # 2. 主查询: 今日触发 × context × per-stock 历史最优桶
        # 桶匹配逻辑: 完全相同的 5 维桶 + n_signals ≥ 3
        # 用 SQL CTE 一次性 JOIN
        from services.primitives.ddl import (DIM_PRICE_LIMIT_RULES_DDL,
                                                DIM_MARKET_SEGMENT_DDL,
                                                DIM_TRADING_RULE_DDL,
                                                DIM_FEE_SCHEDULE_DDL,
                                                DIM_TRADING_SESSION_DDL)
        def _bin_case_sql(col, bins):
            cases = " ".join(
                f"WHEN {col} IS NOT NULL AND {col} >= {lo} AND {col} < {hi} THEN '{label}'"
                for lo, hi, label in bins
            )
            return f"CASE {cases} ELSE '?' END"

        log.info("收集今日触发 × 5 维桶 × 历史最优配置...")
        candidates = conn.execute(
            f"""
            WITH today_signals AS (
              SELECT t.stock_code, t.formula_id, t.formula_variant, t.strength,
                     {_bin_case_sql('c.vol_r20', VOL_BINS)} AS vol_bin,
                     {_bin_case_sql('c.amt_r20', AMT_BINS)} AS amt_bin,
                     {_bin_case_sql('c.price_pos_60d', P60_BINS)} AS p60_bin,
                     COALESCE(c.technical_stage, '?') AS stage_bin
                FROM fact_technical_trigger t
                LEFT JOIN fact_signal_context c
                  ON c.stock_code = t.stock_code AND c.date = t.date
               WHERE t.date = ?
            )
            SELECT ts.stock_code, ts.formula_id, ts.formula_variant, ts.strength,
                   ts.vol_bin, ts.amt_bin, ts.p60_bin, ts.stage_bin,
                   o.holding_days, o.n_signals, o.win_rate, o.avg_ret, o.avg_dd, o.sharpe, o.calmar
              FROM today_signals ts
              LEFT JOIN mart_stock_formula_optuna o
                ON o.stock_code = ts.stock_code
               AND o.formula_id = ts.formula_id
               AND o.formula_variant = ts.formula_variant
               AND o.vol_bin = ts.vol_bin
               AND o.amt_bin = ts.amt_bin
               AND o.price_pos_bin = ts.p60_bin
               AND o.stage_bin = ts.stage_bin
               AND o.is_best_hd = TRUE
            """,
            [signal_date],
        ).fetchall()
        log.info(f"  今日触发 {len(candidates):,} 条 (含含/不含 历史匹配)")

        # 拉今日所有触发股的 close (T 日)
        close_rows = conn.execute(
            """SELECT stock_code, latest_close FROM mart_stock_picture_daily
               WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM mart_stock_picture_daily)""",
        ).fetchall()
        close_by_code = {r[0]: float(r[1]) if r[1] else None for r in close_rows}

        # 3. 拼推荐行 (有历史匹配的才推荐)
        rows = []
        for r in candidates:
            (sc, fid, fvar, strength,
             vol_b, amt_b, p60_b, stage_b,
             hd, n, win, avg_ret, avg_dd, sharpe, cal) = r
            if hd is None or n is None or win is None:
                continue  # 该股该桶无历史匹配 (跳过, 不推荐)
            confidence = float(win) * math.log(1.0 + int(n))
            if confidence < args.min_confidence:
                continue
            signal_close = close_by_code.get(sc)
            if not signal_close or signal_close <= 0:
                continue
            buy_price_est = signal_close * 1.005  # 保守估计 T+1 开盘 0.5% 溢价
            avg_ret_f = float(avg_ret)
            avg_dd_f  = float(avg_dd) if avg_dd is not None else 0.0
            sell_target = buy_price_est * (1.0 + avg_ret_f)
            rows.append((
                signal_date, buy_date, sc, fid, fvar,
                vol_b, amt_b, p60_b, stage_b,
                float(strength) if strength else None,
                float(win), avg_ret_f, avg_dd_f, float(sharpe or 0.0), int(n),
                int(hd), signal_close, buy_price_est, sell_target,
                avg_dd_f, avg_ret_f, confidence,
            ))

        # 按 confidence 排序 + 分配 rank
        rows.sort(key=lambda x: x[-1], reverse=True)
        rows = [r + (i + 1,) for i, r in enumerate(rows)]
        log.info(f"  通过过滤 (confidence ≥ {args.min_confidence}): {len(rows):,} 条")

        # 4. 写库 atomic
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("DELETE FROM mart_daily_formula_buys WHERE signal_date = ?", [signal_date])
            conn.executemany(
                """INSERT INTO mart_daily_formula_buys
                   (signal_date, buy_date, stock_code, formula_id, formula_variant,
                    vol_bin, amt_bin, price_pos_bin, stage_bin,
                    signal_strength,
                    historical_win_rate, historical_avg_ret, historical_avg_dd,
                    historical_sharpe, historical_n_signals,
                    recommended_holding_days, signal_close_price, buy_price_est, sell_target_price,
                    expected_max_dd_pct, expected_return_pct, confidence_score,
                    rank_in_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            conn.execute("COMMIT")
        except BaseException:
            try: conn.execute("ROLLBACK")
            except Exception: pass
            raise

        # 5. 打印 top 10 给 user
        print(f"\n{'='*120}")
        print(f"  今日盘后 T+1 推荐 (signal={signal_date}, buy={buy_date}) - Top 10")
        print(f"{'='*120}")
        print(f"{'rank':>4} {'股票':>8} {'公式':>30} {'信号':>4} {'胜率':>6} "
              f"{'预期收益':>9} {'预期DD':>9} {'持仓':>4} {'信号价':>9} {'目标价':>9}")
        print(f"{'-'*120}")
        for r in rows[:10]:
            print(f"{r[-1]:>4} {r[2]:>8} {r[4]:>30} {r[14]:>4} {r[10]*100:>5.1f}% "
                  f"{r[11]*100:>+8.2f}% {r[12]*100:>+8.2f}% {r[15]:>4}d "
                  f"{r[16]:>9.2f} {r[18]:>9.2f}")
        print(f"{'='*120}")
        log.info(f"=== 总耗时 {time.time()-t0:.0f}s | 推荐 {len(rows)} 条 ===")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
