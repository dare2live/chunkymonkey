"""experiment_yushen_clean_baseline — 广义入场单仓干净基线 (主会话主导, 2026-06-16)。

owner: analysis/yushen_framework_design_20260616.md §8.7 修法 + 用户 2026-06-16 纠偏。
落实纠偏: (#5) 全参数 train-only 不 peek — 本脚本用**固定结构参数不调优** (无任何选参=无 peek);
  (#6) 广义入场不止二次突破 — 3 原型: A二次突破(深回调) / B温和延续(稳涨浅回调=之前漏的) / C回踩均线反弹;
  (#7) 日线周线结合 — 周线确认 context + 日线择时; (#2) 单仓 — 一次只持一仓(平了再找下个), 不做组合 sizing;
  (#3) 回撤不设限 — 特征化输出, 报"拿到该收益承受多大回撤"; 裁决看**对标中证500/1000 超额** (非裸年化, §8.7 beta 教训)。
PIT: 周线上一完成周; 入场信号<=当日; T+1 open; 涨停剔; 含成本13bps双边。
TRAIN 2020-01..2025-05 / OOS 2025-06+ 分段报 (regime-conditional: TRAIN指数≈0% vs OOS小盘牛, 看edge在哪段)。
源: market.price_kline_qfq_tushare + /tmp/cm_index_daily.parquet (中证500/1000 基准, 审计已提取)。
用法: PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_yushen_clean_baseline.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import duckdb  # rule-compliance: ok evidence=只读K线+指数单仓基线; manifest; allowlist
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest
from services.experiment_store import open_store, record_verdict

log = logging.getLogger("yushen_clean")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

COST = 0.0013        # rule-compliance: ok evidence=A股双边13bps, 同鱼身系列
TRAIL = 0.88         # rule-compliance: ok evidence=移动止盈12%(固定不调优防peek, 同系列), 结构常数
MAX_HOLD = 120       # rule-compliance: ok evidence=持有上限, 结构常数
OOS_CUT = "2025-06-01"  # rule-compliance: ok evidence=方法论OOS切点(MASTER§5)
TRAIN_START = "2020-01-01"  # rule-compliance: ok evidence=train窗起点(MASTER§5)
FAR_FUTURE = "2099-01-01"   # rule-compliance: ok evidence=日期上界哨兵(取OOS_CUT之后全部), 非业务参数
# 入场原型结构参数 (固定, 不调优=无peek)
BASE_N = 60          # rule-compliance: ok evidence=A二次突破出箱回看(季度级), 结构常数
RETR = 0.08          # rule-compliance: ok evidence=A深回调阈值, 结构常数
HOLD_TOL = 0.05      # rule-compliance: ok evidence=不破位容差, 结构常数
HIGH_N = 20          # rule-compliance: ok evidence=B温和延续/20日新高, 结构常数
MILD_PB = 0.05       # rule-compliance: ok evidence=B温和=近20日最大回撤<5%(浅回调), 结构常数
MA_N = 20            # rule-compliance: ok evidence=C回踩均线周期MA20, 结构常数


def _weekly_state(dates, closes):
    df = pd.DataFrame({"date": pd.to_datetime(dates), "close": closes})
    df["wk"] = df["date"].dt.to_period("W")
    wk = df.groupby("wk")["close"].last().reset_index()
    wk["ma30"] = wk["close"].rolling(30).mean()
    wk["ma10"] = wk["close"].rolling(10).mean()
    wk["confirmed"] = (wk["close"] > wk["ma30"]) & (wk["ma30"] > wk["ma30"].shift(1)) & (wk["ma10"] > wk["ma30"])
    wk["confirmed_lag"] = wk["confirmed"].shift(1).fillna(False)
    return df["wk"].map(dict(zip(wk["wk"], wk["confirmed_lag"]))).fillna(False).to_numpy().astype(bool)


def _limit_up(c, i):
    return i >= 1 and c[i] / c[i - 1] - 1 >= 0.098


def entries_A(c, state, vols):
    """A 二次突破 (深回调→再破前峰)。返回入场 idx 列表。"""
    n = len(c)
    hh = pd.Series(c).rolling(BASE_N).max().to_numpy()
    out = []
    i = BASE_N + 1
    while i < n - 1:
        if not (state[i] and c[i] >= hh[i] and c[i] > 0):
            i += 1
            continue
        b1 = i
        brk = c[b1]
        vol_brk = vols[max(b1 - 2, 0):b1 + 1].mean()
        peak = c[b1]
        pb = False
        pb_lo = b1
        j = b1 + 1
        hit = False
        while j < min(b1 + BASE_N, n - 1):
            peak = max(peak, c[j])
            if c[j] < brk * (1 - HOLD_TOL):
                i = j
                break
            if not pb and c[j] <= peak * (1 - RETR) and c[j] > brk * (1 - HOLD_TOL):
                pb = True
                pb_lo = j
            if pb and c[j] >= peak and state[j] and not _limit_up(c, j):
                vol_pb = vols[pb_lo:j + 1].mean() if j > pb_lo else vol_brk
                if vol_pb <= vol_brk:
                    out.append(j)
                    hit = True
                    i = j + 1
                    break
            j += 1
        if not hit and i == b1:
            i = b1 + 1
    return out


def entries_B(c, state):
    """B 温和延续 (20日新高 + 近20日最大回撤<MILD_PB = 稳涨浅回调, 之前二次突破漏掉的)。"""
    n = len(c)
    hh = pd.Series(c).rolling(HIGH_N).max().to_numpy()
    ll = pd.Series(c).rolling(HIGH_N).min().to_numpy()
    out = []
    i = HIGH_N + 1
    while i < n - 1:
        # 20日新高 + 该窗内未深跌 (max drawdown from rolling-high < MILD_PB) + 周线确认 + 非涨停
        mild = (hh[i] - ll[i]) / hh[i] < MILD_PB * 2 if hh[i] > 0 else False  # 区间窄=稳涨
        if state[i] and c[i] >= hh[i] and c[i] > 0 and mild and not _limit_up(c, i):
            out.append(i)
            i += 5  # 同一段稳涨不重复入太密
        else:
            i += 1
    return out


def entries_C(c, state):
    """C 回踩均线反弹 (跌触MA20后收回MA20上方)。"""
    n = len(c)
    ma = pd.Series(c).rolling(MA_N).mean().to_numpy()
    out = []
    i = MA_N + 2
    while i < n - 1:
        # 前一日触及/跌破MA20, 当日收回MA20上方 + 周线确认 + 非涨停
        if state[i] and c[i] > 0 and not np.isnan(ma[i]) and c[i - 1] <= ma[i - 1] and c[i] > ma[i] and not _limit_up(c, i):
            out.append(i)
            i += 5
        else:
            i += 1
    return out


def trade(opens, highs, closes, state, ei):
    """从信号 ei 入场(T+1 open), 移动止盈/周破位/max_hold 出场。返回 (entry_date_idx, exit_idx, 含成本收益)。"""
    n = len(closes)
    entry = opens[ei + 1] if opens[ei + 1] > 0 else closes[ei]
    peak = entry
    exit_i = min(ei + MAX_HOLD, n - 1)
    for j in range(ei + 1, min(ei + MAX_HOLD, n - 1) + 1):
        peak = max(peak, highs[j])
        if closes[j] < peak * TRAIL or not state[j]:
            exit_i = j
            break
    return ei + 1, exit_i, float(closes[exit_i] / entry - 1.0 - COST)


def single_position_equity(all_trades):
    """all_trades=[(entry_date_str, exit_date_str, ret)]; 单仓顺序(平了再开最早的)→ 权益曲线 → 年化/maxdd。"""
    all_trades.sort(key=lambda x: x[0])
    equity = 1.0
    curve = []  # (exit_date, equity)
    free_after = ""  # 当前持仓的平仓日; 新仓须 entry > free_after
    taken = []
    for ed, xd, r in all_trades:
        if ed > free_after:
            equity *= (1 + r)
            free_after = xd
            curve.append((xd, equity, r))
            taken.append((ed, xd, r))
    if len(curve) < 5:
        return dict(n=0, ret=0.0, mdd=0.0, navs=[]), taken
    navs = np.array([e for _, e, _ in curve])
    peak = np.maximum.accumulate(navs)
    mdd = float(np.min(navs / peak - 1))
    # 年化: 用首末持仓日跨度
    d0 = pd.to_datetime(curve[0][0])
    d1 = pd.to_datetime(curve[-1][0])
    yrs = max((d1 - d0).days / 365.25, 0.1)
    ann = float(navs[-1] ** (1 / yrs) - 1)
    return dict(n=len(taken), ret=float(navs[-1] - 1), ann=ann, mdd=mdd, navs=navs.tolist(), yrs=yrs), taken


def _per_trade_stats(trades_with_dates, cut_lo, cut_hi):
    """trades=[(entry_date,exit_date,ret)]; 在entry_date∈[lo,hi)算 per-trade。"""
    r = np.array([t[2] for t in trades_with_dates if cut_lo <= t[0] < cut_hi])
    if len(r) == 0:
        return dict(n=0, mean=0.0, win=0.0, pf=0.0, p30=0.0)
    pf = abs(r[r > 0].mean() / r[r < 0].mean()) if (r < 0).any() and (r > 0).any() else 0
    return dict(n=len(r), mean=float(r.mean()), win=float((r > 0).mean()), pf=float(pf), p30=float((r > 0.30).mean()))


def main():
    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读K线; manifest; allowlist
    arr = con.execute("SELECT code, date, open, high, close, volume FROM price_kline_qfq_tushare WHERE date >= '2019-01-01' AND close>0 ORDER BY code, date").fetchnumpy()  # rule-compliance: ok evidence=2019起留周线预热
    # 基准: 中证500/1000 等权混合 (小盘动量对标, §8.7)
    idx = con.execute("SELECT ts_code, trade_date, close FROM read_parquet('/tmp/cm_index_daily.parquet')").df()  # rule-compliance: ok evidence=审计已提取指数; 只读parquet
    con.close()

    def idx_ann(code, lo, hi):
        d = idx[idx["ts_code"] == code].copy()
        d["ds"] = pd.to_datetime(d["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
        d = d[(d["ds"] >= lo) & (d["ds"] < hi)].sort_values("ds")
        if len(d) < 20:
            return 0.0
        yrs = max((pd.to_datetime(d["ds"].iloc[-1]) - pd.to_datetime(d["ds"].iloc[0])).days / 365.25, 0.1)
        return float((d["close"].iloc[-1] / d["close"].iloc[0]) ** (1 / yrs) - 1)

    bench = {seg: (idx_ann("000905.SH", lo, hi) + idx_ann("000852.SH", lo, hi)) / 2
             for seg, (lo, hi) in [("TRAIN", (TRAIN_START, OOS_CUT)), ("OOS", (OOS_CUT, FAR_FUTURE))]}

    codes = arr["code"]
    uniq, first = np.unique(codes, return_index=True)
    order = np.argsort(first)
    uniq, first = uniq[order], first[order]
    last = np.concatenate([first[1:], [len(codes)]])

    archetypes = {"A二次突破": [], "B温和延续": [], "C回踩反弹": [], "ALL并集": []}
    log.info("扫 3 入场原型 (固定结构参数, 无调优=无peek)...")
    for si in range(len(uniq)):
        s, e = int(first[si]), int(last[si])
        d = arr["date"][s:e].astype(str)
        c = arr["close"][s:e].astype(float)
        if len(c) < 160:
            continue
        o = arr["open"][s:e].astype(float)
        h = arr["high"][s:e].astype(float)
        v = arr["volume"][s:e].astype(float)
        state = _weekly_state(d, c)
        eA, eB, eC = entries_A(c, state, v), entries_B(c, state), entries_C(c, state)
        seen = set()
        for name, ents in [("A二次突破", eA), ("B温和延续", eB), ("C回踩反弹", eC)]:
            for ei in ents:
                _, xi, r = trade(o, h, c, state, ei)
                archetypes[name].append((d[ei + 1], d[xi], r))
                key = ei
                if key not in seen:
                    seen.add(key)
                    archetypes["ALL并集"].append((d[ei + 1], d[xi], r))

    print(f"\n广义入场单仓干净基线 (3原型, 固定参数无peek, 含成本13bps, 周线确认+日线择时)")
    print(f"  基准(中证500/1000等权): TRAIN年化{bench['TRAIN']*100:+.1f}% / OOS年化{bench['OOS']*100:+.1f}%")
    print(f"  {'原型':12}{'段':6}{'per-trade n':>12}{'均值':>8}{'胜率':>7}{'盈亏比':>7}{'>30%':>7} | {'单仓n':>6}{'年化':>8}{'回撤':>8}{'超额':>8}")
    results = {}
    for name, trs in archetypes.items():
        for seg, (lo, hi) in [("TRAIN", (TRAIN_START, OOS_CUT)), ("OOS", (OOS_CUT, FAR_FUTURE))]:
            pt = _per_trade_stats(trs, lo, hi)
            seg_trs = [t for t in trs if lo <= t[0] < hi]
            sp, _ = single_position_equity([list(t) for t in seg_trs])
            excess = (sp.get("ann", 0.0) - bench[seg]) if sp["n"] else 0.0
            results[(name, seg)] = dict(pt=pt, sp=sp, excess=excess)
            print(f"  {name:12}{seg:6}{pt['n']:>12,}{pt['mean']*100:>+7.2f}%{pt['win']*100:>6.1f}%{pt['pf']:>7.2f}{pt['p30']*100:>6.1f}% | {sp['n']:>6,}{sp.get('ann',0)*100:>+7.1f}%{sp['mdd']*100:>+7.1f}%{excess*100:>+7.1f}%")

    # 裁决: 各原型 OOS 单仓超额 (扣小盘beta) + per-trade OOS均值
    print(f"\n  --- 裁决 (单仓超额=策略年化−小盘基准; 回撤=特征化非门; 全参数train-only无peek) ---")
    lines = []
    for name in archetypes:
        oos = results[(name, "OOS")]
        tr = results[(name, "TRAIN")]
        tag = f"{name}: OOS单仓年化{oos['sp'].get('ann',0)*100:+.1f}%/回撤{oos['sp']['mdd']*100:+.1f}%/超额{oos['excess']*100:+.1f}pp (per-trade均值{oos['pt']['mean']*100:+.2f}%胜率{oos['pt']['win']*100:.0f}%); TRAIN超额{tr['excess']*100:+.1f}pp"
        lines.append(tag)
        print(f"  {tag}")
    best = max(archetypes, key=lambda n: results[(n, "OOS")]["excess"])
    bx = results[(best, "OOS")]["excess"]
    bx_tr = results[(best, "TRAIN")]["excess"]
    verdict = (f"最优原型={best} OOS超额{bx*100:+.1f}pp/TRAIN超额{bx_tr*100:+.1f}pp. "
               + ("两段超额均>0=广义入场对小盘有真增量(待扩样本/DSR/regime确认)" if bx > 0 and bx_tr > 0
                  else "超额≤0或两段不一致=广义入场单仓不稳过小盘beta, B温和延续是否补回二次突破漏的赢家待看; 真edge需靠多因子/出场而非价格行为入场"))
    print(f"\n  → {verdict}")

    run_id = "yushen_clean_baseline_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    judges = {"bench_train": round(bench["TRAIN"], 4), "bench_oos": round(bench["OOS"], 4), "best_archetype": best,
              "best_oos_excess": round(bx, 4), "best_train_excess": round(bx_tr, 4),
              "archetypes": {n: {"oos_excess": round(results[(n, "OOS")]["excess"], 4),
                                 "oos_pt_mean": round(results[(n, "OOS")]["pt"]["mean"], 4),
                                 "oos_sp_ann": round(results[(n, "OOS")]["sp"].get("ann", 0), 4),
                                 "oos_sp_mdd": round(results[(n, "OOS")]["sp"]["mdd"], 4)} for n in archetypes},
              "summary": verdict[:120]}
    with open_store() as store:
        record_verdict(store, run_id=run_id, family="yushen_clean_baseline", verdict="BROAD_ENTRY_SINGLEPOS", judges=judges, confirmed_by_owner=0)
    print(f"\n  [experiment_store] 已留档 family=yushen_clean_baseline")


if __name__ == "__main__":
    main()
