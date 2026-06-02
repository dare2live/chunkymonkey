"""Phase η P2 — 每股 × 每公式 × 每持仓 × 5 维上下文 grid search 回测。

不用 Optuna (5000 股 × 300 trials 太贵), 改用 grid search per stock:
  - 每股 JOIN fact_technical_trigger + fact_signal_context
  - 算每 (formula_id, variant, hd, vol_bin, amt_bin, p60_bin, stage_bin) 的胜率/收益/dd
  - 标记 is_best_hd (该股该 variant 下 calmar 最高的 hd)
  - 标记 is_high_conviction (胜率 ≥ 60% + n ≥ 5)

写入: mart_stock_formula_optuna (per-stock 视图的核心)
用法:
  PYTHONPATH=backend python backend/scripts/build_stock_formula_optuna.py [--start 2024-01-01]
"""
from __future__ import annotations

import argparse
import logging
import time
from collections import defaultdict
from datetime import date as _date
from pathlib import Path

import duckdb
import numpy as np

from services.formula_engine.shared_windows import HOLDING_DAYS
from services.stock_formula_optuna_config import DEFAULT_STOCK_FORMULA_OPTUNA_CONFIG


log = logging.getLogger("build_stock_formula_optuna")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")


MARKET_DB = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
SMART_DB  = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"


# 5 维分桶阈值 (与 analyze_macd_feature_buckets 一致)
VOL_BINS  = DEFAULT_STOCK_FORMULA_OPTUNA_CONFIG.vol_bins
AMT_BINS  = DEFAULT_STOCK_FORMULA_OPTUNA_CONFIG.amt_bins
P60_BINS  = DEFAULT_STOCK_FORMULA_OPTUNA_CONFIG.p60_bins

# 最少信号过滤 (per stock × variant × hd × 5 维桶)
MIN_N_PER_BUCKET = DEFAULT_STOCK_FORMULA_OPTUNA_CONFIG.min_n_per_bucket
MIN_WIN_HIGH_CONVICTION = DEFAULT_STOCK_FORMULA_OPTUNA_CONFIG.min_win_high_conviction
MIN_N_HIGH_CONVICTION = DEFAULT_STOCK_FORMULA_OPTUNA_CONFIG.min_n_high_conviction


def _bin_label(value, bins):
    for lo, hi, label in bins:
        if value is not None and lo <= value < hi:
            return label
    return "?"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default=None,
                        help="默认 calendar-gated latest_closed_trade_date (Phase ψ.5)")
    args = parser.parse_args()

    if args.end is None:
        from services.utils import latest_closed_or_raise
        args.end = latest_closed_or_raise()
        log.info(f"--end 默认 (calendar-gated): {args.end}")

    t_total = time.time()
    log.info(f"per-stock formula grid search {args.start} ~ {args.end}")

    mkt = duckdb.connect(str(MARKET_DB), read_only=True)
    mkt.execute(f"ATTACH '{SMART_DB}' AS sm (READ_ONLY)")

    # 1. 加载所有信号 + context (一次性 JOIN, 内存可控)
    log.info("加载信号 × context...")
    sigs = mkt.execute(
        """
        SELECT t.stock_code, t.date, t.formula_id, t.formula_variant, t.strength,
               c.vol_r20, c.amt_r20, c.price_pos_60d, c.technical_stage
          FROM sm.fact_technical_trigger t
          INNER JOIN sm.fact_signal_context c
            ON c.stock_code = t.stock_code AND c.date = t.date
         WHERE t.date >= ? AND t.date <= ?
         ORDER BY t.stock_code, t.formula_id, t.formula_variant, t.date
        """,
        [args.start, args.end],
    ).fetchall()
    log.info(f"  signals × context: {len(sigs):,}")

    # 2. 加载全市场 K 线索引
    log.info("加载全市场 K 线...")
    kl_rows = mkt.execute(
        """
        SELECT code, date, close, low
          FROM v_price_kline_qfq
         WHERE adjust='qfq' AND freq='daily' AND date >= ?
         ORDER BY code, date
        """,
        [args.start],
    ).fetchall()
    kl_close: dict[str, list[tuple[str, float]]] = defaultdict(list)
    kl_low:   dict[str, list[float]] = defaultdict(list)
    for code, d, cl, lo in kl_rows:
        if cl and float(cl) > 0:
            kl_close[code].append((str(d), float(cl)))
            kl_low[code].append(float(lo) if lo and float(lo) > 0 else float(cl))
    log.info(f"  K 线: {len(kl_close)} 股")
    mkt.close()

    # 3. 对每信号 × 每 hd, 算 ret + max_dd, 累计到 (stock × variant × hd × 5桶) 桶
    log.info(f"网格回测 (5 维 × {len(HOLDING_DAYS)} hd × {len(sigs):,} 信号)...")
    t_calc = time.time()
    # bucket_key = (stock, formula_id, variant, hd, vol_bin, amt_bin, p60_bin, stage_bin)
    buckets: dict[tuple, list[tuple[float, float]]] = defaultdict(list)  # (ret, dd) list

    # 预建每股日期索引 (date -> idx)
    kl_idx: dict[str, dict[str, int]] = {}
    for code, lst in kl_close.items():
        kl_idx[code] = {d: i for i, (d, _) in enumerate(lst)}

    for sc, d, fid, fvar, strength, vr, ar, p60, stage in sigs:
        if vr is None or ar is None or p60 is None:
            continue
        cls_list = kl_close.get(sc)
        if not cls_list:
            continue
        i = kl_idx[sc].get(str(d))
        if i is None:
            continue
        # 5 维桶
        vol_b = _bin_label(vr, VOL_BINS)
        amt_b = _bin_label(ar, AMT_BINS)
        p60_b = _bin_label(p60, P60_BINS)
        stage_b = stage if stage in ("1", "1.5", "2", "3", "4") else "?"

        # T+1 入场
        entry_i = i + 1
        if entry_i >= len(cls_list):
            continue
        entry = cls_list[entry_i][1]
        if entry <= 0:
            continue

        for hd in HOLDING_DAYS:
            exit_i = entry_i + hd
            if exit_i >= len(cls_list):
                continue
            exit_p = cls_list[exit_i][1]
            if exit_p <= 0:
                continue
            ret = (exit_p - entry) / entry
            # max_dd: 持仓期最低 low / entry - 1
            lows = kl_low[sc][entry_i:exit_i + 1]
            if not lows:
                continue
            dd = (min(lows) - entry) / entry  # ≤0
            key = (sc, fid, fvar, hd, vol_b, amt_b, p60_b, stage_b)
            buckets[key].append((ret, dd))

    log.info(f"  网格桶: {len(buckets):,} ({time.time()-t_calc:.1f}s)")

    # 4. 聚合每桶 → metrics
    log.info("聚合 metrics...")
    out_rows = []
    # 也算 per-stock per-variant 下哪个 hd 最佳 (用于 is_best_hd 标记)
    per_stock_variant_best: dict[tuple, tuple] = {}  # (sc, fid, fvar) → (best_key, best_calmar)
    bucket_metrics = {}

    for key, items in buckets.items():
        if len(items) < MIN_N_PER_BUCKET:
            continue
        rets = np.array([x[0] for x in items])
        dds = np.array([x[1] for x in items])
        n = len(rets)
        win_rate = float((rets > 0).mean())
        avg_ret = float(rets.mean())
        med_ret = float(np.median(rets))
        avg_dd = float(dds.mean())
        med_dd = float(np.median(dds))
        sd = float(rets.std())
        sharpe = float(avg_ret * 252 / key[3] / sd) if sd > 0 else 0.0
        calmar = float(avg_ret / max(abs(avg_dd), 0.005))
        is_high_conviction = (win_rate >= MIN_WIN_HIGH_CONVICTION and n >= MIN_N_HIGH_CONVICTION)

        bucket_metrics[key] = {
            "n": n, "win_rate": win_rate, "avg_ret": avg_ret, "med_ret": med_ret,
            "avg_dd": avg_dd, "med_dd": med_dd, "sharpe": sharpe, "calmar": calmar,
            "is_high_conviction": is_high_conviction,
        }

        # best hd 跟踪 (per stock × variant × 5 维桶)
        bk = (key[0], key[1], key[2], key[4], key[5], key[6], key[7])  # 去掉 hd
        prev = per_stock_variant_best.get(bk)
        if prev is None or calmar > prev[1]:
            per_stock_variant_best[bk] = (key, calmar)

    # 5. 输出 row + 标记 is_best_hd
    log.info(f"  有效桶 (n≥{MIN_N_PER_BUCKET}): {len(bucket_metrics):,}")
    best_hd_set = {bk[0] for bk in per_stock_variant_best.values()}

    for key, m in bucket_metrics.items():
        sc, fid, fvar, hd, vb, ab, pb, sb = key
        is_best = key in best_hd_set
        out_rows.append((
            sc, fid, fvar, hd, vb, ab, pb, sb,
            m["n"], m["win_rate"], m["avg_ret"], m["med_ret"],
            m["avg_dd"], m["med_dd"], m["sharpe"], m["calmar"],
            is_best, m["is_high_conviction"],
            args.start, args.end,
        ))

    # 6. 写库 (atomic)
    log.info(f"写库 ({len(out_rows):,} 行)...")
    from services.db import get_conn
    from services.formula_engine.per_stock_ddl import ensure_per_stock_tables
    conn = get_conn()
    try:
        ensure_per_stock_tables(conn)
        t_write = time.time()
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("DELETE FROM mart_stock_formula_optuna")
            BATCH = 100_000
            for i in range(0, len(out_rows), BATCH):
                conn.executemany(
                    """INSERT INTO mart_stock_formula_optuna
                       (stock_code, formula_id, formula_variant, holding_days,
                        vol_bin, amt_bin, price_pos_bin, stage_bin,
                        n_signals, win_rate, avg_ret, median_ret,
                        avg_dd, median_dd, sharpe, calmar,
                        is_best_hd, is_high_conviction,
                        eval_start_date, eval_end_date)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    out_rows[i:i + BATCH],
                )
                if (i + BATCH) % 500_000 == 0:
                    log.info(f"  写入 {i+BATCH:,} / {len(out_rows):,}")
            conn.execute("COMMIT")
        except BaseException:
            try: conn.execute("ROLLBACK")
            except Exception: pass
            raise
        log.info(f"  写入完成 ({time.time()-t_write:.1f}s)")

        # 简单 stats
        n_high = sum(1 for r in out_rows if r[17])  # is_high_conviction
        n_best = sum(1 for r in out_rows if r[16])  # is_best_hd
        log.info(f"=== 总耗时 {time.time()-t_total:.0f}s | 桶数 {len(out_rows):,} | high_conviction {n_high:,} | best_hd {n_best:,} ===")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
