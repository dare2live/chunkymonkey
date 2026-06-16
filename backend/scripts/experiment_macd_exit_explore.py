"""experiment_macd_exit_explore — 其他公式出场探索: 金叉买, 比较卖点规则含成本收益 (用户 2026-06-16)。

用户: MACD 金叉=买点, 卖点用金叉后波峰去探索因子(均线/其他)。死叉口径中位 -2.2%(假亏)。
本脚本对全部金叉 episode 模拟多种出场规则的含成本收益, 看哪种卖点最抓住波峰 (金叉买+好卖点=完整可交易公式)。
出场规则: 死叉(基线) / MA10破 / MA20破 / 移动止盈(回撤峰15%) / 固定20日 / 移动止盈8%。
T+1 open 入场, 出场触发日收盘出, 含成本(双边~13bps 含印花)。结果倒推不涉及(这是出场规则比较, 非预测)。
源: market.price_kline_qfq_tushare。
用法: PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_macd_exit_explore.py --end 2025-05-31
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

import duckdb  # rule-compliance: ok evidence=只读出场规则比较; manifest路径; allowlist
import numpy as np

from services.database_manifest import get_database_manifest
from services.experiment_store import open_store, record_verdict

log = logging.getLogger("macd_exit_explore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

FAST, SLOW, SIGNAL = 12, 26, 9   # rule-compliance: ok evidence=MACD Appel 定义常数
MAX_HOLD = 120                   # rule-compliance: ok evidence=持有上限(同 ground truth)
COST = 0.0013                    # rule-compliance: ok evidence=A股双边含成本~13bps(佣金~5+印花5卖+滑点), portfolio_execbacktest 同量级


def _ema(x, span):
    a = 2.0 / (span + 1.0); out = np.empty(len(x)); out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def scan_exits(closes, highs, opens, ma10, ma20):
    """每金叉 episode 各出场规则的含成本收益。返回 list of dict(rule->ret)。"""
    n = len(closes)
    if n < SLOW + SIGNAL + 25:
        return []
    dif = _ema(closes, FAST) - _ema(closes, SLOW); dea = _ema(dif, SIGNAL)
    gc = list(np.where((dif[:-1] <= dea[:-1]) & (dif[1:] > dea[1:]))[0] + 1)
    dc_set = set(np.where((dif[:-1] >= dea[:-1]) & (dif[1:] < dea[1:]))[0] + 1)
    rows = []
    for k, bi in enumerate(gc):
        if bi + 1 >= n:
            continue
        entry = opens[bi + 1]  # T+1 open 入场
        if entry <= 0:
            continue
        nxt = gc[k + 1] if k + 1 < len(gc) else n
        we = min(nxt, bi + MAX_HOLD, n - 1)
        if we <= bi + 1:
            continue
        seg = slice(bi + 1, we + 1)
        c = closes[seg]; h = highs[seg]
        run_peak = np.maximum.accumulate(h)
        def _exit_close(cond_idx):
            return closes[bi + 1 + cond_idx] if cond_idx is not None else closes[we]
        def _first(mask):
            w = np.where(mask)[0]
            return int(w[0]) if len(w) else None
        ex = {}
        # 死叉 (基线): 持有窗内第一个死叉日出
        dc_in = [i for i in range(bi + 1, we + 1) if i in dc_set]
        ex["death_cross"] = closes[dc_in[0]] if dc_in else closes[we]
        ex["ma10_break"] = _exit_close(_first(c < ma10[seg]))
        ex["ma20_break"] = _exit_close(_first(c < ma20[seg]))
        ex["trail_15"] = _exit_close(_first(c < run_peak * 0.85))
        ex["trail_8"] = _exit_close(_first(c < run_peak * 0.92))
        ex["hold_20"] = closes[min(bi + 20, we)]
        rows.append({r: float(px / entry - 1.0 - COST) for r, px in ex.items()})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", default="2025-05-31")  # rule-compliance: ok evidence=train窗截止, 同 GT
    args = ap.parse_args()
    mf = get_database_manifest()
    mk = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读K线; manifest; allowlist
    arr = mk.execute("SELECT code, date, open, high, close FROM price_kline_qfq_tushare WHERE date <= ? AND close>0 ORDER BY code, date", [args.end]).fetchnumpy()
    mk.close()
    codes = arr["code"]; uniq, first = np.unique(codes, return_index=True)
    order = np.argsort(first); uniq, first = uniq[order], first[order]
    last = np.concatenate([first[1:], [len(codes)]])
    allrows = []
    for ci in range(len(uniq)):
        s, e = int(first[ci]), int(last[ci])
        c = arr["close"][s:e].astype(float)
        if len(c) < SLOW + SIGNAL + 25:
            continue
        ma10 = np.concatenate([np.full(9, np.nan), np.convolve(c, np.ones(10) / 10, "valid")])
        ma20 = np.concatenate([np.full(19, np.nan), np.convolve(c, np.ones(20) / 20, "valid")])
        allrows.extend(scan_exits(c, arr["high"][s:e].astype(float), arr["open"][s:e].astype(float), ma10, ma20))

    rules = ["death_cross", "ma10_break", "ma20_break", "trail_8", "trail_15", "hold_20"]
    n = len(allrows)
    print(f"\n金叉 episode 出场规则含成本收益比较 (n={n:,}, T+1 open 入场, 双边{COST*1e4:.0f}bps)")
    print(f"{'出场规则':14}{'均值':>9}{'中位':>9}{'胜率':>8}{'>30%占比':>9}")
    rule_means = {}
    for r in rules:
        v = np.array([row[r] for row in allrows])
        rule_means[r] = round(float(np.median(v)), 5)
        print(f"  {r:12}{np.mean(v)*100:>8.2f}%{np.median(v)*100:>8.2f}%{(v>0).mean()*100:>7.1f}%{(v>0.30).mean()*100:>8.1f}%")
    print("\n注: 这是金叉全样本(无入场过滤)各出场规则的含成本期望; 死叉=基线。下一步=最优出场 × 入场因子过滤(Optuna)。")
    best = max(rule_means, key=rule_means.get)
    run_id = "macd_exit_explore_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with open_store() as store:
        record_verdict(store, run_id=run_id, family="macd_exit_rule", verdict="EXIT_RULE_SCAN",
                       judges={"n": n, "median_by_rule": rule_means, "best_rule": best,
                               "summary": f"金叉出场对比n={n}: 最优中位={best}({rule_means[best]*100:+.2f}%), 死叉基线={rule_means.get('death_cross')}"},
                       confirmed_by_owner=0)


if __name__ == "__main__":
    main()
