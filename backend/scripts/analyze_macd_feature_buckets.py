"""Phase ε++ — MACD 金叉 5 维分桶回测分析。

借鉴 bestchoice/macd_optuna_backtest 设计:
  按 5 维上下文 (dif_sign / vol_r20 / amt_r20 / price_pos_60d / technical_stage)
  对 MACD 金叉信号分桶 + 算每桶胜率/收益/calmar/sharpe.

输出 (按 calmar × win_rate 降序):
  - top 30 桶组合 (打印到 stdout)
  - CSV: scripts/macd_feature_buckets.csv

用法:
  PYTHONPATH=backend python backend/scripts/analyze_macd_feature_buckets.py [--holding-days 20]
"""
from __future__ import annotations

import argparse
import csv
import logging
import time
from collections import deque
from collections import defaultdict
from pathlib import Path

import duckdb
import numpy as np

from services.shared_feature_bins_config import DEFAULT_SHARED_FEATURE_BINS_CONFIG


log = logging.getLogger("macd_buckets")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")


MARKET_DB = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
SMART_DB  = Path(__file__).resolve().parents[2] / "data" / "smartmoney.duckdb"
OUT_DIR   = Path(__file__).resolve().parent


# 分桶阈值 (共享 feature bins config)
VOL_BINS   = DEFAULT_SHARED_FEATURE_BINS_CONFIG.vol_bins
AMT_BINS   = DEFAULT_SHARED_FEATURE_BINS_CONFIG.amt_bins
P60_BINS   = DEFAULT_SHARED_FEATURE_BINS_CONFIG.p60_bins
DIF_SIGNS  = ["above_zero", "below_zero"]
STAGES     = ["1", "1.5", "2", "3", "4"]


def _bin_label(value, bins, fallback="?"):
    for lo, hi, label in bins:
        if lo <= value < hi:
            return label
    return fallback


def _rolling_window_minima(values: list[float], window_size: int) -> list[float | None]:
    if window_size <= 0:
        raise ValueError("window_size must be positive")
    minima: list[float | None] = [None] * len(values)
    window: deque[tuple[int, float]] = deque()
    for idx, value in enumerate(values):
        while window and window[-1][1] >= value:
            window.pop()
        window.append((idx, value))
        start = idx - window_size + 1
        while window and window[0][0] < start:
            window.popleft()
        if idx >= window_size - 1:
            minima[start] = window[0][1]
    return minima


def _build_kline_cache(
    kl: dict[str, dict[str, float]],
    holding_days: int,
) -> dict[str, dict[str, object]]:
    window_size = holding_days + 1
    cache: dict[str, dict[str, object]] = {}
    for code, by_date in kl.items():
        dates = sorted(by_date.keys())
        closes = [by_date[d] for d in dates]
        if len(closes) <= holding_days:
            continue
        cache[code] = {
            "dates": dates,
            "closes": closes,
            "date_to_index": {date: idx for idx, date in enumerate(dates)},
            "entry_window_minima": _rolling_window_minima(closes, window_size),
        }
    return cache


def _enrich_macd_signals(
    rows: list[tuple[object, ...]],
    kl: dict[str, dict[str, float]],
    holding_days: int,
) -> list[dict[str, object]]:
    cache = _build_kline_cache(kl, holding_days)
    enriched: list[dict[str, object]] = []
    for sc, d, fvar, strength, vr, ar, p60, p120, stage in rows:
        if vr is None or ar is None or p60 is None:
            continue
        code_cache = cache.get(sc)
        if not code_cache:
            continue
        date_to_index = code_cache["date_to_index"]  # type: ignore[assignment]
        dates = code_cache["dates"]  # type: ignore[assignment]
        closes = code_cache["closes"]  # type: ignore[assignment]
        window_minima = code_cache["entry_window_minima"]  # type: ignore[assignment]
        try:
            i = date_to_index[str(d)]  # type: ignore[index]
        except KeyError:
            continue
        entry_i = i + 1
        exit_i = entry_i + holding_days
        if exit_i >= len(dates):
            continue
        entry = closes[entry_i]
        exit_ = closes[exit_i]
        if entry <= 0:
            continue
        ret = (exit_ - entry) / entry
        dd_floor = window_minima[entry_i]
        dd = (dd_floor - entry) / entry if dd_floor is not None else 0.0
        enriched.append(
            {
                "ret": ret,
                "dd": dd,
                "dif_sign": str(fvar).replace("macd_golden_cross_", ""),  # above_zero / below_zero
                "vol_bin": _bin_label(vr, VOL_BINS),
                "amt_bin": _bin_label(ar, AMT_BINS),
                "p60_bin": _bin_label(p60, P60_BINS),
                "stage": stage if stage in STAGES else "?",
            }
        )
    return enriched


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--holding-days", type=int, default=20)
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2026-05-12")
    parser.add_argument("--min-n", type=int, default=200, help="桶最少信号数")
    args = parser.parse_args()

    t0 = time.time()
    log.info(f"MACD 5 维分桶回测 (holding={args.holding_days}d)")

    mkt = duckdb.connect(str(MARKET_DB), read_only=True)
    mkt.execute(f"ATTACH '{SMART_DB}' AS sm (READ_ONLY)")

    # 1. JOIN: 信号 × context (DIF sign 由 formula_variant 决定)
    log.info("加载 MACD 信号 + context...")
    rows = mkt.execute(
        """
        SELECT t.stock_code,
               t.date,
               t.formula_variant,
               t.strength,
               c.vol_r20,
               c.amt_r20,
               c.price_pos_60d,
               c.price_pos_120d,
               c.technical_stage
          FROM sm.fact_technical_trigger t
          INNER JOIN sm.fact_signal_context c
            ON c.stock_code = t.stock_code AND c.date = t.date
         WHERE t.formula_id = 'macd_golden_cross'
           AND t.date >= ? AND t.date <= ?
         ORDER BY t.stock_code, t.date
        """,
        [args.start, args.end],
    ).fetchall()
    log.info(f"  {len(rows):,} 个 MACD 金叉信号 (with context)")

    # 2. 加载 K 线 (内存索引 code→date→close)
    log.info("加载全市场 K 线...")
    kl_rows = mkt.execute(
        """
        SELECT code, date, close
          FROM v_price_kline_qfq
         WHERE adjust='qfq' AND freq='daily' AND date >= ?
        """,
        [args.start],
    ).fetchall()
    kl: dict[str, dict[str, float]] = defaultdict(dict)
    for code, d, cl in kl_rows:
        if cl and float(cl) > 0:
            kl[code][str(d)] = float(cl)
    log.info(f"  K 线索引: {len(kl)} 股")

    # 3. 对每信号算 T+1 close 入场, T+1+hd close 出场 (跟主 horizon_evidence 一致)
    log.info(f"算 forward return (持仓 {args.holding_days}d)...")
    enriched = _enrich_macd_signals(rows, kl, args.holding_days)
    log.info(f"  有效信号 (含 forward return): {len(enriched):,}")

    mkt.close()

    # 4. 按桶聚合
    log.info("分桶聚合...")
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for e in enriched:
        key = (e["dif_sign"], e["vol_bin"], e["amt_bin"], e["p60_bin"], e["stage"])
        buckets[key].append(e)

    # 5. 算每桶 metrics
    bucket_stats = []
    for key, items in buckets.items():
        if len(items) < args.min_n:
            continue
        rets = np.array([x["ret"] for x in items])
        dds = np.array([x["dd"] for x in items])
        n = len(rets)
        win_rate = float((rets > 0).mean())
        avg_ret = float(rets.mean())
        med_ret = float(np.median(rets))
        avg_dd = float(dds.mean())
        med_dd = float(np.median(dds))
        sd = float(rets.std())
        sharpe = float(avg_ret * 252 / args.holding_days / sd) if sd > 0 else 0.0  # 年化
        calmar = float(avg_ret / max(abs(avg_dd), 0.005))
        score = calmar * win_rate
        bucket_stats.append({
            "dif_sign": key[0], "vol_bin": key[1], "amt_bin": key[2],
            "p60_bin": key[3], "stage": key[4],
            "n": n,
            "win_rate": round(win_rate, 4),
            "avg_ret": round(avg_ret, 4),
            "med_ret": round(med_ret, 4),
            "avg_dd": round(avg_dd, 4),
            "med_dd": round(med_dd, 4),
            "sharpe": round(sharpe, 3),
            "calmar": round(calmar, 3),
            "score": round(score, 3),
        })

    bucket_stats.sort(key=lambda x: x["score"], reverse=True)
    log.info(f"  {len(bucket_stats)} 个有效桶 (n >= {args.min_n})")

    # 6. 打印 top 30
    print(f"\n{'='*120}")
    print(f"  MACD 金叉 5 维分桶 (持仓 {args.holding_days} 日, 信号数≥{args.min_n}) — 按 calmar×win_rate 降序")
    print(f"  期间 {args.start} ~ {args.end} | 总有效信号 {len(enriched):,} | 有效桶 {len(bucket_stats)}")
    print(f"{'='*120}")
    print(f"{'rank':>4} {'信号':>5} {'0轴':>10} {'量比':>4} {'额比':>4} {'位置':>4} {'阶段':>4}  "
          f"{'胜率':>6} {'均收益':>8} {'中位收益':>8} {'均DD':>8} {'Sharpe':>7} {'Calmar':>7} {'Score':>7}")
    print(f"{'-'*120}")
    for rank, b in enumerate(bucket_stats[:30], 1):
        print(f"{rank:>4} {b['n']:>5} {b['dif_sign']:>10} {b['vol_bin']:>4} {b['amt_bin']:>4} "
              f"{b['p60_bin']:>4} {b['stage']:>4}  "
              f"{b['win_rate']*100:>5.1f}% {b['avg_ret']*100:>+7.2f}% {b['med_ret']*100:>+7.2f}% "
              f"{b['avg_dd']*100:>+7.2f}% {b['sharpe']:>7.3f} {b['calmar']:>7.3f} {b['score']:>7.3f}")
    print(f"{'='*120}\n")

    # 7. 保存 CSV
    csv_path = OUT_DIR / f"macd_feature_buckets_hd{args.holding_days}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=bucket_stats[0].keys() if bucket_stats else [
            "dif_sign", "vol_bin", "amt_bin", "p60_bin", "stage", "n",
            "win_rate", "avg_ret", "med_ret", "avg_dd", "med_dd",
            "sharpe", "calmar", "score",
        ])
        writer.writeheader()
        writer.writerows(bucket_stats)
    log.info(f"CSV 保存: {csv_path}")
    log.info(f"=== 总耗时 {time.time()-t0:.0f}s ===")


if __name__ == "__main__":
    main()
