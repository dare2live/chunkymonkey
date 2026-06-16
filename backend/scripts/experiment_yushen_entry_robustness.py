"""experiment_yushen_entry_robustness — 二次突破入场 alpha 鲁棒性扫描 (主会话主导, 2026-06-16)。

前置裁决: 二次突破入场比随机 +2.81%/笔 (样本内 bootstrap p=1.0), 但仅 561 笔 = 太稀建不起组合, OOS 欠样本。
本实验是**投 Optuna 前的自我 grill**: 放宽规则 (BASE_N/RETR/缩量阈值) 扫一个小网格, 看
  (a) alpha 是稳健 (放宽仍保住增量) 还是 knife-edge (放宽即消失=稀有模式运气);
  (b) 能否在保住增量的同时拿到足够交易量 (建 20 仓组合需 ~数千笔)。
若稳健且能提频 → 值得投 Optuna 系统搜 + 组合NAV; 若 knife-edge → 561笔alpha是运气, 转 stage-conditional 因子排名。
PIT/出场/成本口径同 experiment_yushen_selective_entry (周线上一完成周, T+1 open, 涨停剔, 13bps, 移动止盈/周破位)。
效率: 每股 weekly_state 算一次, 12 组参数共享同一遍扫描; random 基线算一次。
源: market.price_kline_qfq_tushare。用法: PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_yushen_entry_robustness.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import duckdb  # rule-compliance: ok evidence=只读K线鲁棒性扫描; manifest; allowlist
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest
from services.experiment_store import open_store, record_verdict

log = logging.getLogger("yushen_robust")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

COST = 0.0013        # rule-compliance: ok evidence=A股双边13bps, 同 selective_entry
TRAIL = 0.88         # rule-compliance: ok evidence=移动止盈12%, 同
MAX_HOLD = 120       # rule-compliance: ok evidence=持有上限, 同
OOS_CUT = "2025-06-01"  # rule-compliance: ok evidence=方法论OOS切点(MASTER§5), 同

# 参数网格 (放宽方向: BASE_N 缩短=更易触发首突破, RETR 减小=更浅回调算数, vol_mult 放大=放松缩量要求)
GRID = [  # rule-compliance: ok evidence=鲁棒性扫描网格(结构参数变体, 非拟合最优), base=selective_entry原值
    dict(base_n=60, retr=0.08, hold_tol=0.05, max_base=60, vol_mult=1.0),   # 原始 (561笔基准)
    dict(base_n=60, retr=0.05, hold_tol=0.05, max_base=60, vol_mult=1.0),
    dict(base_n=40, retr=0.08, hold_tol=0.05, max_base=60, vol_mult=1.0),
    dict(base_n=40, retr=0.05, hold_tol=0.05, max_base=60, vol_mult=1.0),
    dict(base_n=40, retr=0.05, hold_tol=0.08, max_base=80, vol_mult=1.3),
    dict(base_n=40, retr=0.05, hold_tol=0.08, max_base=80, vol_mult=2.0),   # 最放松 (放弃缩量约束)
    dict(base_n=30, retr=0.04, hold_tol=0.08, max_base=90, vol_mult=2.0),   # 极放松
    dict(base_n=60, retr=0.08, hold_tol=0.05, max_base=120, vol_mult=1.0),  # 长窗口
]


def _weekly_state(dates, closes):
    df = pd.DataFrame({"date": pd.to_datetime(dates), "close": closes})
    df["wk"] = df["date"].dt.to_period("W")
    wk = df.groupby("wk")["close"].last().reset_index()
    wk["ma30"] = wk["close"].rolling(30).mean()
    wk["ma10"] = wk["close"].rolling(10).mean()
    wk["confirmed"] = (wk["close"] > wk["ma30"]) & (wk["ma30"] > wk["ma30"].shift(1)) & (wk["ma10"] > wk["ma30"])
    wk["confirmed_lag"] = wk["confirmed"].shift(1).fillna(False)
    return df["wk"].map(dict(zip(wk["wk"], wk["confirmed_lag"]))).fillna(False).to_numpy().astype(bool)


def _exit_ret(opens, highs, closes, state, i, n):
    entry = opens[i + 1] if opens[i + 1] > 0 else closes[i]
    peak = entry
    exit_px = closes[min(i + MAX_HOLD, n - 1)]
    for j in range(i + 1, min(i + MAX_HOLD, n - 1) + 1):
        peak = max(peak, highs[j])
        if closes[j] < peak * TRAIL or not state[j]:
            return float(closes[j] / entry - 1.0 - COST), j
    return float(exit_px / entry - 1.0 - COST), min(i + MAX_HOLD, n - 1)


def _limit_up(closes, i):
    return i >= 1 and closes[i] / closes[i - 1] - 1 >= 0.098


def scan_second(dates, opens, highs, closes, vols, state, hh_base, p):
    """二次突破状态机, 参数 p=dict(retr/hold_tol/max_base/vol_mult); hh_base 已按 base_n 预算。"""
    n = len(closes)
    out = []
    i = p["_base_n"] + 1
    while i < n - 1:
        if not (state[i] and closes[i] >= hh_base[i] and closes[i] > 0):
            i += 1
            continue
        b1 = i
        brk_level = closes[b1]
        vol_brk = vols[max(b1 - 2, 0):b1 + 1].mean()
        peak = closes[b1]
        pulled_back = False
        pb_lo = b1
        j = b1 + 1
        entered = False
        while j < min(b1 + p["max_base"], n - 1):  # complexity: 内层 max_base 限界 → O(n) per stock
            peak = max(peak, highs[j])
            if closes[j] < brk_level * (1 - p["hold_tol"]):
                i = j
                break
            if not pulled_back and closes[j] <= peak * (1 - p["retr"]) and closes[j] > brk_level * (1 - p["hold_tol"]):
                pulled_back = True
                pb_lo = j
            if pulled_back and closes[j] >= peak and state[j] and not _limit_up(closes, j):
                vol_pb = vols[pb_lo:j + 1].mean() if j > pb_lo else vol_brk
                if vol_pb <= vol_brk * p["vol_mult"]:
                    r, exit_i = _exit_ret(opens, highs, closes, state, j, n)
                    out.append((str(dates[j + 1]), r))
                    i = exit_i + 1
                    entered = True
                    break
            j += 1
        if not entered and i == b1:
            i = b1 + 1
    return out


def scan_random(dates, opens, highs, closes, state, rng, rate):
    n = len(closes)
    out = []
    i = 21
    while i < n - 1:
        if state[i] and closes[i] > 0 and rng.random() < rate and not _limit_up(closes, i):
            r, exit_i = _exit_ret(opens, highs, closes, state, i, n)
            out.append((str(dates[i + 1]), r))
            i = exit_i + 1
        else:
            i += 1
    return out


def _agg(rl, cut=None):
    r = np.array([x[1] for x in rl if (cut is None or x[0] >= cut)])
    if len(r) == 0:
        return dict(n=0, mean=0.0, win=0.0)
    return dict(n=len(r), mean=float(r.mean()), win=float((r > 0).mean()))


def main():
    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读K线; manifest; allowlist
    arr = con.execute("SELECT code, date, open, high, close, volume FROM price_kline_qfq_tushare WHERE date >= '2019-01-01' AND close>0 ORDER BY code, date").fetchnumpy()  # rule-compliance: ok evidence=2019起留30周线预热(同selective_entry)
    con.close()
    codes = arr["code"]
    uniq, first = np.unique(codes, return_index=True)
    order = np.argsort(first)
    uniq, first = uniq[order], first[order]
    last = np.concatenate([first[1:], [len(codes)]])
    rng = np.random.RandomState(20260616)  # rule-compliance: ok evidence=固定种子复现random对照, 非业务参数

    combo_res = {ci: [] for ci in range(len(GRID))}
    rand = []
    base_ns = sorted({g["base_n"] for g in GRID})
    for si in range(len(uniq)):
        s, e = int(first[si]), int(last[si])
        d = arr["date"][s:e]
        c = arr["close"][s:e].astype(float)
        if len(c) < 160:
            continue
        o = arr["open"][s:e].astype(float)
        h = arr["high"][s:e].astype(float)
        v = arr["volume"][s:e].astype(float)
        state = _weekly_state(d, c)
        hh_cache = {bn: pd.Series(c).rolling(bn).max().to_numpy() for bn in base_ns}
        for ci, g in enumerate(GRID):
            p = dict(g)
            p["_base_n"] = g["base_n"]
            combo_res[ci].extend(scan_second(d, o, h, c, v, state, hh_cache[g["base_n"]], p))
        rand.extend(scan_random(d, o, h, c, state, rng, 0.02))  # rule-compliance: ok evidence=随机入场率2%(对照基线), 派生非业务参数

    rnd_all = _agg(rand)
    rnd_oos = _agg(rand, OOS_CUT)
    print(f"\n二次突破入场 alpha 鲁棒性扫描 (周线确认 context, 同出场+13bps; 随机基线 全{rnd_all['mean']*100:+.2f}%/OOS{rnd_oos['mean']*100:+.2f}%)")
    print(f"  {'#':>2} {'base/retr/hold/maxB/vol':24} {'n':>7} {'均值':>8} {'胜率':>7} {'比随机增量':>10} | {'OOS n':>6} {'OOS增量':>8}")
    summary_rows = []
    for ci, g in enumerate(GRID):
        a = _agg(combo_res[ci])
        oos = _agg(combo_res[ci], OOS_CUT)
        inc = a["mean"] - rnd_all["mean"]
        inc_oos = oos["mean"] - rnd_oos["mean"]
        tag = f"{g['base_n']}/{g['retr']}/{g['hold_tol']}/{g['max_base']}/{g['vol_mult']}"
        print(f"  {ci:>2} {tag:24} {a['n']:>7,} {a['mean']*100:>+7.2f}% {a['win']*100:>6.1f}% {inc*100:>+9.2f}% | {oos['n']:>6,} {inc_oos*100:>+7.2f}%")
        summary_rows.append(dict(combo=ci, tag=tag, n=a["n"], mean=round(a["mean"], 5), inc=round(inc, 5), n_oos=oos["n"], inc_oos=round(inc_oos, 5)))

    # 裁决: 找"增量保住(>+1.5%) 且 n 足够(>=2000)"的组合
    robust = [r for r in summary_rows if r["inc"] > 0.015 and r["n"] >= 2000 and r["inc_oos"] > 0]
    knife = all(r["n"] < 2000 or r["inc"] <= 0.015 for r in summary_rows[1:])  # 除原始外放宽就垮
    print(f"\n  --- 裁决 (能否在保增量下提频) ---")
    if robust:
        best = max(robust, key=lambda r: r["inc"])
        verdict = f"鲁棒: {len(robust)}组在增量>+1.5%下达n>=2000(OOS同向). 最优#{best['combo']}({best['tag']}) n={best['n']:,} 增量{best['inc']*100:+.2f}%/OOS{best['inc_oos']*100:+.2f}% → 值得Optuna系统搜+组合NAV"
    elif knife:
        verdict = "knife-edge: 一放宽增量即垮或样本仍不足 → 561笔alpha是稀有模式(运气成分大), 不投Optuna, 转stage-conditional因子排名(突破候选池内用因子/筹码/预期)"
    else:
        verdict = "部分: 有组合提频但增量衰减或OOS转负 → alpha随放宽递减, Optuna须把'比随机增量'当目标函数严守OOS, 提频与alpha有tradeoff"
    print(f"  → {verdict}")

    run_id = "yushen_entry_robustness_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with open_store() as store:
        record_verdict(store, run_id=run_id, family="yushen_entry_alpha", verdict="ROBUSTNESS_SWEEP",
                       judges={"rnd_mean": round(rnd_all["mean"], 5), "rnd_oos": round(rnd_oos["mean"], 5),
                               "grid": summary_rows, "n_robust": len(robust), "summary": verdict[:120]},
                       confirmed_by_owner=0)
    print(f"\n  [experiment_store] 已留档 family=yushen_entry_alpha verdict=ROBUSTNESS_SWEEP")


if __name__ == "__main__":
    main()
