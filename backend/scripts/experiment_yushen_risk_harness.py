"""experiment_yushen_risk_harness — 鱼身风控+出场网格寻优 (主会话主导, 2026-06-16, 攻墙2回撤)。

pre-reg owner: analysis/yushen_framework_design_20260616.md §8.6 (冻结判据)。
三道墙: 墙1入场edge已清(二次突破比随机+2~3.4%稳健); 墙2回撤(裸基-46.8%→需≥-20%)=本实验攻; 墙3组合NAV年化。
设计 (controller, 第一性原理): 入场alpha不解决回撤 → **固定入场在鲁棒最优, 搜风控(regime门/仓位/max_pos)+出场(trail/max_hold)**。
  目标函数 = 含成本组合 TRAIN 年化, s.t. max_dd≥-20% (违约罚) — 禁IC/per-trade当目标(C-R1)。
  walk-forward: TRAIN 2020-01..2025-05 选参; OOS 2025-06+ 完全留出只报一次(防peek)。
  治理: plan_validator.enforce_search_space_nonempty 守门; DSR(deflated_sharpe)防72组多重比较过拟合;
  optuna_config max_realistic_sharpe 异常守; experiment_store 留档。capacity admission control(max_pos FIFO)= R2 容量约束雏形。
PIT: 周线上一完成周; 二次突破<=当日; T+1 open入场; 涨停剔; regime门用入场日HS300上周状态。含成本13bps双边。
源: market.price_kline_qfq_tushare + tushare_raw.raw_tushare_index_daily(HS300 regime)。
用法: PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_yushen_risk_harness.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from itertools import product

import duckdb  # rule-compliance: ok evidence=只读K线+指数组合NAV寻优; manifest; allowlist
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest
from services.experiment_store import open_store, record_verdict
from services.optimization.plan_validator import enforce_search_space_nonempty
from services.optimization.deflated_sharpe import deflated_sharpe_ratio

log = logging.getLogger("yushen_risk")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

COST = 0.0013       # rule-compliance: ok evidence=A股双边13bps含印花, 同鱼身系列
OOS_CUT = "2025-06-01"  # rule-compliance: ok evidence=方法论OOS切点(MASTER§5), pre-reg冻结
TRAIN_START = "2020-01-01"  # rule-compliance: ok evidence=train窗起点(MASTER§5)
MAX_DD_FLOOR = -0.20    # rule-compliance: ok evidence=KPI max_dd约束(goal.md), 违约罚
# 入场固定鲁棒最优 #3 (§8.5 鲁棒性扫描: 1007笔/+3.09%增量/OOS+3.33%, 频率与alpha均衡)
ENTRY = dict(base_n=40, retr=0.05, hold_tol=0.05, max_base=60, vol_mult=1.0)  # rule-compliance: ok evidence=robustness#3, §8.5

# 搜索空间 (风控+出场, 攻墙2) — pre-reg §8.6 范围内
GRID = dict(  # rule-compliance: ok evidence=pre-reg§8.6冻结搜索空间(风控+出场), 非拟合值
    regime=["off", "hs300_bull"],
    sizing=["equal", "vol_inv"],
    max_pos=[10, 20, 30],
    trail=[0.85, 0.88, 0.92],
    max_hold=[60, 120],
)


def _weekly_bull_by_date(dates, closes):
    """指数周线多头态 (close>MA30 & MA30上行), PIT 用上一完成周, 返回 {date_str: bool}。"""
    df = pd.DataFrame({"date": pd.to_datetime(dates), "close": closes})
    df["wk"] = df["date"].dt.to_period("W")
    wk = df.groupby("wk")["close"].last().reset_index()
    wk["ma30"] = wk["close"].rolling(30).mean()
    wk["bull"] = (wk["close"] > wk["ma30"]) & (wk["ma30"] > wk["ma30"].shift(1))
    wk["bull_lag"] = wk["bull"].shift(1).fillna(False)
    m = dict(zip(wk["wk"], wk["bull_lag"]))
    return {d.strftime("%Y-%m-%d"): bool(m.get(p, False)) for d, p in zip(df["date"], df["wk"])}


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


def find_entries(closes, state, vols):
    """二次突破入场点 (入场参数固定 ENTRY), 返回 [(entry_idx, vol_at_entry)]。"""
    n = len(closes)
    hh = pd.Series(closes).rolling(ENTRY["base_n"]).max().to_numpy()
    logret = np.diff(np.log(np.clip(closes, 1e-9, None)), prepend=np.log(max(closes[0], 1e-9)))
    out = []
    i = ENTRY["base_n"] + 1
    while i < n - 1:
        if not (state[i] and closes[i] >= hh[i] and closes[i] > 0):
            i += 1
            continue
        b1 = i
        brk = closes[b1]
        vol_brk = vols[max(b1 - 2, 0):b1 + 1].mean()
        peak = closes[b1]
        pb = False
        pb_lo = b1
        j = b1 + 1
        hit = False
        while j < min(b1 + ENTRY["max_base"], n - 1):
            peak = max(peak, closes[j])
            if closes[j] < brk * (1 - ENTRY["hold_tol"]):
                i = j
                break
            if not pb and closes[j] <= peak * (1 - ENTRY["retr"]) and closes[j] > brk * (1 - ENTRY["hold_tol"]):
                pb = True
                pb_lo = j
            if pb and closes[j] >= peak and state[j] and not _limit_up(closes, j):
                vol_pb = vols[pb_lo:j + 1].mean() if j > pb_lo else vol_brk
                if vol_pb <= vol_brk * ENTRY["vol_mult"]:
                    rv = float(np.std(logret[max(j - 19, 0):j + 1])) or 0.02  # rule-compliance: ok evidence=20日波动用于vol_inv仓位; 0.02兜底防除零
                    out.append((j, rv))
                    hit = True
                    i = j + 1
                    break
            j += 1
        if not hit and i == b1:
            i = b1 + 1
    return out


def simulate_holding(opens, highs, closes, state, ei, trail, max_hold):
    """从入场信号 ei 模拟持有, 返回 (entry_idx+1, exit_idx, [(idx, daily_ret)])。"""
    n = len(closes)
    entry = opens[ei + 1] if opens[ei + 1] > 0 else closes[ei]
    peak = entry
    exit_i = min(ei + max_hold, n - 1)
    for j in range(ei + 1, min(ei + max_hold, n - 1) + 1):
        peak = max(peak, highs[j])
        if closes[j] < peak * trail or not state[j]:
            exit_i = j
            break
    days = []
    prev = entry
    for j in range(ei + 1, exit_i + 1):
        days.append((j, closes[j] / prev - 1.0))
        prev = closes[j]
    return ei + 1, exit_i, days


def _metrics(daily_rets_by_date, cut_lo, cut_hi):
    """daily_rets_by_date: {date_str: net_ret}; 在 [cut_lo, cut_hi) 区间算 年化/max_dd/sharpe。"""
    ds = sorted(d for d in daily_rets_by_date if cut_lo <= d < cut_hi)
    if len(ds) < 20:
        return dict(ann=0.0, mdd=0.0, sharpe=0.0, sharpe_d=0.0, ndays=len(ds))
    rets = np.array([daily_rets_by_date[d] for d in ds])
    nav = np.cumprod(1 + rets)
    ann = float(nav[-1] ** (252 / len(rets)) - 1)
    peak = np.maximum.accumulate(nav)
    mdd = float(np.min(nav / peak - 1))
    sharpe_d = float(rets.mean() / rets.std()) if rets.std() > 0 else 0.0  # 日频(给DSR, n=ndays)
    return dict(ann=ann, mdd=mdd, sharpe=sharpe_d * np.sqrt(252), sharpe_d=sharpe_d, ndays=len(ds))


def run_combo(stock_data, entries_by_code, hs300_bull, combo):
    """一个风控+出场组合 → 含成本组合 NAV 日序 → train/OOS 指标。"""
    regime, sizing, max_pos, trail, max_hold = combo["regime"], combo["sizing"], combo["max_pos"], combo["trail"], combo["max_hold"]
    # 1) 生成候选持仓 (regime门 + sizing weight)
    cands = []  # (entry_date_str, exit_date_str, weight, [(date_str, ret)])
    for code, (dates, opens, highs, closes, vols, state) in stock_data.items():
        for ei, rv in entries_by_code[code]:
            edate = dates[ei + 1] if ei + 1 < len(dates) else dates[ei]
            edate = str(edate)
            if regime == "hs300_bull" and not hs300_bull.get(str(dates[ei]), False):
                continue
            _, exit_i, days = simulate_holding(opens, highs, closes, state, ei, trail, max_hold)
            if not days:
                continue
            w = 1.0 if sizing == "equal" else 1.0 / max(rv, 1e-3)
            cands.append((edate, str(dates[exit_i]), w, [(str(dates[idx]), r) for idx, r in days]))
    # 2) capacity admission control (max_pos FIFO, R2容量约束雏形)
    cands.sort(key=lambda x: x[0])
    open_exits = []  # heap-ish list of exit_date for currently held
    admitted = []
    for edate, xdate, w, days in cands:
        open_exits = [x for x in open_exits if x >= edate]  # 释放已平仓
        if len(open_exits) < max_pos:
            open_exits.append(xdate)
            admitted.append((edate, xdate, w, days))
    # 3) 按日聚合 (加权均值 + 换手成本)
    from collections import defaultdict
    day_wr = defaultdict(list)   # date -> [(w, ret)]
    entry_cnt = defaultdict(int)
    exit_cnt = defaultdict(int)
    for edate, xdate, w, days in admitted:
        entry_cnt[edate] += 1
        exit_cnt[xdate] += 1
        for d, r in days:
            day_wr[d].append((w, r))
    net_by_date = {}
    for d, wr in day_wr.items():
        tw = sum(w for w, _ in wr)
        port_ret = sum(w * r for w, r in wr) / tw if tw > 0 else 0.0
        held = len(wr)
        cost = ((entry_cnt.get(d, 0) + exit_cnt.get(d, 0)) * COST) / max(held, 1)
        net_by_date[d] = port_ret - cost
    train = _metrics(net_by_date, TRAIN_START, OOS_CUT)
    oos = _metrics(net_by_date, OOS_CUT, "2099-01-01")  # rule-compliance: ok evidence=日期上界哨兵(取OOS_CUT之后全部), 非业务参数
    return train, oos, len(admitted)


def main():
    # plan_validator 守门 (搜索空间非空)
    spaces = {"yushen_risk_harness": {k: v for k, v in GRID.items()}}  # rule-compliance: ok evidence=传搜索空间给plan_validator守门
    enforce_search_space_nonempty(["yushen_risk_harness"], spaces=spaces)
    combos = [dict(zip(GRID.keys(), vals)) for vals in product(*GRID.values())]
    log.info("plan_validator PASS; 组合数=%d", len(combos))

    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读K线; manifest; allowlist
    arr = con.execute("SELECT code, date, open, high, close, volume FROM price_kline_qfq_tushare WHERE date >= '2019-01-01' AND close>0 ORDER BY code, date").fetchnumpy()  # rule-compliance: ok evidence=2019起留周线预热
    con.execute(f"ATTACH '{mf.path_for('tushare_raw')}' AS tr (READ_ONLY)")
    hs = con.execute("SELECT trade_date, close FROM tr.raw_tushare_index_daily WHERE ts_code='000300.SH' AND trade_date>='20180101' ORDER BY trade_date").df()
    con.close()
    hs_dates = pd.to_datetime(hs["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d").to_numpy()
    hs300_bull = _weekly_bull_by_date(hs_dates, hs["close"].to_numpy())

    codes = arr["code"]
    uniq, first = np.unique(codes, return_index=True)
    order = np.argsort(first)
    uniq, first = uniq[order], first[order]
    last = np.concatenate([first[1:], [len(codes)]])
    log.info("载入 %d 股; 扫二次突破入场 (固定参数)...", len(uniq))
    stock_data = {}
    entries_by_code = {}
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
        ents = find_entries(c, state, v)
        if ents:
            code = str(uniq[si])
            stock_data[code] = (d, o, h, c, v, state)
            entries_by_code[code] = ents
    n_entries = sum(len(v) for v in entries_by_code.values())
    log.info("入场点 %d 个 (跨 %d 股); 跑 %d 组合 NAV...", n_entries, len(stock_data), len(combos))

    results = []
    for ci, combo in enumerate(combos):
        train, oos, n_adm = run_combo(stock_data, entries_by_code, hs300_bull, combo)
        results.append(dict(combo=combo, train=train, oos=oos, n_admitted=n_adm))
        if (ci + 1) % 12 == 0:
            log.info("  %d/%d combos done", ci + 1, len(combos))

    # 选参: TRAIN 年化 s.t. max_dd约束 (违约罚=每1%超额扣2%年化)
    def score(r):
        t = r["train"]
        pen = max(0.0, MAX_DD_FLOOR - t["mdd"]) * 2.0  # mdd更负=超约束=罚
        return t["ann"] - pen
    results.sort(key=score, reverse=True)
    best = results[0]

    # DSR: 72组 train sharpe 多重比较去通胀 (用日频sharpe + n=ndays, 年化sharpe会使sqrt(T)爆炸误判)
    train_sharpes_d = [r["train"]["sharpe_d"] for r in results]
    best_sh_d = best["train"]["sharpe_d"]
    dsr_p = deflated_sharpe_ratio(best_sh_d, n_trials=len(combos), n_observations=max(best["train"]["ndays"], 2), sharpe_variance=float(np.var(train_sharpes_d)) or 1.0)
    sharpe_anom = best["train"]["sharpe"] > 5.0  # rule-compliance: ok evidence=optuna_config max_realistic_sharpe=5.0 异常守(年化sharpe>5=leakage/bug信号)

    print(f"\n鱼身风控+出场网格寻优 (入场固定#3, 含成本组合NAV, plan_validator PASS, {len(combos)}组)")
    print(f"  入场点 {n_entries:,} | TRAIN {TRAIN_START}..{OOS_CUT} 选参 / OOS {OOS_CUT}+ 留出验")
    print(f"  {'#':>2} {'regime':10}{'sizing':8}{'pos':>4}{'trail':>6}{'hold':>5} | {'TRAIN年化':>9}{'TRAINdd':>9}{'shrp':>6} | {'OOS年化':>9}{'OOSdd':>9}{'仓数':>6}")
    for r in results[:10]:
        cb = r["combo"]
        t, o = r["train"], r["oos"]
        print(f"  {' ':>2} {cb['regime']:10}{cb['sizing']:8}{cb['max_pos']:>4}{cb['trail']:>6}{cb['max_hold']:>5} | {t['ann']*100:>+8.1f}%{t['mdd']*100:>+8.1f}%{t['sharpe']:>6.2f} | {o['ann']*100:>+8.1f}%{o['mdd']*100:>+8.1f}%{r['n_admitted']:>6,}")

    bt, bo, bc = best["train"], best["oos"], best["combo"]
    print(f"\n  --- 最优组合 (TRAIN年化 s.t. max_dd≥-20% 选, OOS单次验) ---")
    print(f"  {bc} ")
    print(f"  TRAIN: 年化{bt['ann']*100:+.1f}% / max_dd{bt['mdd']*100:+.1f}% / sharpe{bt['sharpe']:.2f}")
    print(f"  OOS  : 年化{bo['ann']*100:+.1f}% / max_dd{bo['mdd']*100:+.1f}% / sharpe{bo['sharpe']:.2f}")
    print(f"  DSR p={dsr_p:.3f} (>0.95才算真alpha非72组试错噪音); sharpe异常={sharpe_anom}")
    # KPI 裁决 (含成本组合, OOS)
    kpi_oos = (bo["ann"] >= 0.30 and bo["mdd"] >= MAX_DD_FLOOR)
    wall2 = bt["mdd"] >= MAX_DD_FLOOR  # 墙2: train回撤能否控到-20%
    if sharpe_anom or dsr_p < 0.95:
        verdict = f"过拟合/异常守门: DSR p={dsr_p:.3f}<0.95 OR sharpe>{5.0} → 72组最优可能是试错噪音, 不可信, 需扩样本/降搜索空间"
    elif kpi_oos:
        verdict = f"墙2+墙3 OOS同时过: 含成本OOS年化{bo['ann']*100:+.1f}%≥30% AND max_dd{bo['mdd']*100:+.1f}%≥-20% → 候选转正(待R2 execution-aware复核+超额HS300)"
    elif wall2 and bt["ann"] > 0:
        verdict = f"墙2清(TRAIN max_dd{bt['mdd']*100:+.1f}%≥-20%)但墙3未过(OOS年化{bo['ann']*100:+.1f}%<30%) → 回撤可控但收益不够, 需叠context因子提收益/换入场"
    elif wall2:
        verdict = f"墙2清(回撤控住{bt['mdd']*100:+.1f}%)但TRAIN年化{bt['ann']*100:+.1f}%≤0 → 风控压没了收益, regime门/仓位过严, tradeoff不划算"
    else:
        verdict = f"墙2未清: 最优组合TRAIN max_dd仍{bt['mdd']*100:+.1f}%<-20% → 风控+出场不足以控回撤, 鱼身base风险结构性偏高, 需更强regime/止损或重估论题"
    print(f"\n  → 裁决: {verdict}")

    run_id = "yushen_risk_harness_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    judges = {
        "best_combo": bc, "train_ann": round(bt["ann"], 4), "train_mdd": round(bt["mdd"], 4),
        "oos_ann": round(bo["ann"], 4), "oos_mdd": round(bo["mdd"], 4), "oos_sharpe": round(bo["sharpe"], 3),
        "dsr_p": round(dsr_p, 4), "n_combos": len(combos), "n_entries": n_entries,
        "wall2_train_dd_ok": bool(wall2), "kpi_oos": bool(kpi_oos), "summary": verdict[:120],
    }
    verdict_label = "KPI_CANDIDATE" if kpi_oos and not sharpe_anom and dsr_p >= 0.95 else ("WALL2_CLEARED" if wall2 else "WALL2_FAIL")
    with open_store() as store:
        record_verdict(store, run_id=run_id, family="yushen_risk_harness", verdict=verdict_label, judges=judges, confirmed_by_owner=0)
    print(f"\n  [experiment_store] 已留档 family=yushen_risk_harness verdict={verdict_label}")


if __name__ == "__main__":
    main()
