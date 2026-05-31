"""Phase ε++ — 一次性算全市场上下文 (fact_signal_context)。

借鉴 bestchoice/scripts/macd_optuna_backtest.py 的特征计算 (vol_r20/amt_r20/price60/price120)。

输入: market.duckdb v_price_kline_qfq + smartmoney fact_stock_technical_stage
输出: fact_signal_context (每股每日 1 行)

用法:
  PYTHONPATH=backend python backend/scripts/build_signal_context.py [--start 2024-01-01]
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import duckdb
import numpy as np

from services.db import get_conn
from services.universe import sql_where_active_a_share
from services.utils import latest_completed_trade_date


log = logging.getLogger("build_signal_context")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")


MARKET_DB = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
SMART_DB  = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"


def sma(arr: np.ndarray, window: int) -> np.ndarray:
    n = len(arr)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < window:
        return out
    kernel = np.ones(window, dtype=np.float64) / window
    out[window - 1:] = np.convolve(arr, kernel, mode="valid")
    return out


def rolling_max(arr: np.ndarray, window: int) -> np.ndarray:
    n = len(arr)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < window:
        return out
    from numpy.lib.stride_tricks import sliding_window_view
    padded = np.pad(arr, (window - 1, 0), mode="edge")
    out[:] = sliding_window_view(padded, window).max(axis=1)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default=None,
                        help="默认走 trading_calendar.latest_closed_trading_date (盘中调时取昨日)")
    args = parser.parse_args()

    if args.end is None:
        _c = get_conn()
        try:
            args.end = latest_completed_trade_date(_c)
        finally:
            _c.close()
        if not args.end:
            raise RuntimeError(
                "latest_completed_trade_date 返 None — dim_trading_calendar 未 seed, "
                "拒绝用 wall-clock fallback"
            )
        log.info(f"--end 默认 (calendar-gated): {args.end}")

    t_total = time.time()
    log.info(f"build signal context {args.start} ~ {args.end}")

    # 用原生 duckdb 因需 fetchnumpy
    mkt = duckdb.connect(str(MARKET_DB), read_only=True)
    mkt.execute(f"ATTACH '{SMART_DB}' AS sm (READ_ONLY)")

    log.info("加载 K 线 + technical_stage...")
    arr = mkt.execute(
        """
        SELECT k.code,
               k.date,
               k.close,
               k.volume,
               k.amount,
               COALESCE(ts.stage, '?') AS stage
          FROM v_price_kline_qfq k
          LEFT JOIN sm.fact_stock_technical_stage ts
            ON ts.stock_code = k.code AND ts.date = k.date
         WHERE k.adjust='qfq' AND k.freq='daily'
           AND """ + sql_where_active_a_share('k.code') + """
           AND k.date >= ? AND k.date <= ?
         ORDER BY k.code, k.date
        """,
        [args.start, args.end],
    ).fetchnumpy()
    log.info(f"  K 线 {len(arr['code']):,} 行")

    # group by code: 利用 ORDER BY code, date 的连续性
    codes_arr = arr["code"]
    unique_codes, first_idx = np.unique(codes_arr, return_index=True)
    sort_perm = np.argsort(first_idx)
    unique_codes = unique_codes[sort_perm]
    first_idx = first_idx[sort_perm]
    last_idx = np.concatenate([first_idx[1:], [len(codes_arr)]])

    out_rows: list[tuple] = []
    t_calc = time.time()
    for ci, code in enumerate(unique_codes):
        s_, e_ = int(first_idx[ci]), int(last_idx[ci])
        if e_ - s_ < 120 + 1:  # 至少 121 天才能算 price_pos_120d
            continue
        dates_g = arr["date"][s_:e_]
        close = arr["close"][s_:e_].astype(np.float64)
        volume = arr["volume"][s_:e_].astype(np.float64)
        amount = arr["amount"][s_:e_].astype(np.float64)
        stages = arr["stage"][s_:e_]

        vol_ma20 = sma(volume, 20)
        amt_ma20 = sma(amount, 20)
        max60 = rolling_max(close, 60)
        max120 = rolling_max(close, 120)

        for i in range(120, e_ - s_):  # 跳前 120 天 (预热)
            v_ma = vol_ma20[i]
            a_ma = amt_ma20[i]
            mx60 = max60[i]
            mx120 = max120[i]
            if (np.isnan(v_ma) or v_ma <= 0
                    or np.isnan(a_ma) or a_ma <= 0
                    or np.isnan(mx60) or mx60 <= 0
                    or np.isnan(mx120) or mx120 <= 0):
                continue
            cl = close[i]
            if cl <= 0:
                continue
            vol_r20 = float(volume[i] / v_ma)
            amt_r20 = float(amount[i] / a_ma)
            p60 = float(cl / mx60)
            p120 = float(cl / mx120)
            dd60 = float((cl - mx60) / mx60)  # ≤0
            out_rows.append((
                str(code), str(dates_g[i]),
                vol_r20, amt_r20, float(a_ma),
                p60, p120, dd60,
                str(stages[i]) if stages[i] is not None else None,
            ))

        if (ci + 1) % 500 == 0:
            log.info(f"  {ci+1}/{len(unique_codes)} ({(ci+1)/len(unique_codes)*100:.0f}%) 已算 {len(out_rows):,} 行 ({time.time()-t_calc:.0f}s)")
    log.info(f"  特征计算完成 {len(out_rows):,} 行 ({time.time()-t_calc:.1f}s)")

    mkt.close()

    # 写库 (DELETE + INSERT 显式事务, 用 services 主 conn)
    log.info("写库 (atomic)...")
    # Phase ψ.β.4.5: 不要重复 import get_conn — Python local scoping 会让 line 62
    # 的 _c = get_conn() 失效 (UnboundLocalError). get_conn 已在顶部 import.
    from services.formula_engine.signal_context_ddl import ensure_signal_context_table
    conn = get_conn()
    try:
        ensure_signal_context_table(conn)
        t_write = time.time()
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(
                "DELETE FROM fact_signal_context WHERE date >= ? AND date <= ?",
                [args.start, args.end],
            )
            # 分批 INSERT (单次 500K 行)
            BATCH = 50_000
            for i in range(0, len(out_rows), BATCH):
                conn.executemany(
                    """INSERT INTO fact_signal_context
                       (stock_code, date,
                        vol_r20, amt_r20, amount_20d_avg,
                        price_pos_60d, price_pos_120d, drawdown_60d,
                        technical_stage)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    out_rows[i:i + BATCH],
                )
                if (i + BATCH) % 200_000 == 0:
                    log.info(f"  写入 {i+BATCH:,} / {len(out_rows):,}")
            conn.execute("COMMIT")
        except BaseException:
            try: conn.execute("ROLLBACK")
            except Exception: pass
            raise
        log.info(f"  写入完成 {len(out_rows):,} 行 ({time.time()-t_write:.1f}s)")
    finally:
        conn.close()

    log.info(f"=== 总耗时 {time.time()-t_total:.0f}s ===")


if __name__ == "__main__":
    main()
