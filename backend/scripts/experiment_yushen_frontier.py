"""experiment_yushen_frontier — return↔回撤前沿 (主会话主导, 2026-06-17, culmination)。

owner: 用户回撤特征化重构("拿到最高胜率/收益应该承受多大回撤") + 入场~12角度+出场轴均测尽的合成。
做法: 干净二次突破入场(原始结构参数 entries_A, 无任何选参=无peek, 修§8.7 entry-param-peek教训) + 复用 risk_harness
  run_combo(含成本组合NAV: regime门/sizing/max_pos/admission控/移动止盈) 扫风控+出场网格 → **报全前沿不挑樱桃**
  (每combo train年化/maxdd + OOS年化/maxdd + 小盘基准超额), 看 return↔回撤 tradeoff 曲线 + train-Pareto + OOS一致性。
诚实: 不选"最优"(避选择peek); OOS(2025-06+小盘牛)年化偏乐观, TRAIN(平/震荡市)是更保守 forward 代理。
PIT: 二次突破<=t/T+1 open/涨停剔; regime门用入场日HS300上周; sizing用入场前vol; 含成本13bps。
源: market K线 + tushare_raw.index_daily(HS300)。复用 entries_A(clean_baseline) + run_combo/_weekly_bull_by_date(risk_harness) DRY。
用法: PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_yushen_frontier.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from itertools import product

import duckdb  # rule-compliance: ok evidence=只读组合前沿; manifest; allowlist
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest
from services.experiment_store import open_store, record_verdict
from scripts.experiment_yushen_clean_baseline import _weekly_state, entries_A
from scripts.experiment_yushen_risk_harness import run_combo, _weekly_bull_by_date, TRAIN_START, OOS_CUT

log = logging.getLogger("frontier")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

GRID = dict(  # rule-compliance: ok evidence=风控+出场前沿网格(同risk_harness pre-reg§8.6范围), 非拟合值
    regime=["off", "hs300_bull"], sizing=["equal", "vol_inv"],
    max_pos=[10, 20, 30], trail=[0.85, 0.88, 0.92], max_hold=[60, 120],
)


def main():
    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读K线; manifest; allowlist
    arr = con.execute("SELECT code, date, open, high, close, volume FROM price_kline_qfq_tushare WHERE date>='2019-01-01' AND close>0 ORDER BY code, date").fetchnumpy()  # rule-compliance: ok evidence=2019起留周线预热
    con.execute(f"ATTACH '{mf.path_for('tushare_raw')}' AS tr (READ_ONLY)")
    hs = con.execute("SELECT trade_date, close FROM tr.raw_tushare_index_daily WHERE ts_code='000300.SH' AND trade_date>='20180101' ORDER BY trade_date").df()
    # 小盘基准 (中证500/1000 等权) — return↔回撤前沿的对标
    bench_rows = con.execute("SELECT ts_code, trade_date, close FROM tr.raw_tushare_index_daily WHERE ts_code IN ('000905.SH','000852.SH') AND trade_date>='20200101' ORDER BY ts_code, trade_date").df()
    con.close()
    hs_dates = pd.to_datetime(hs["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d").to_numpy()
    hs300_bull = _weekly_bull_by_date(hs_dates, hs["close"].to_numpy())

    def bench_ann(seg_lo, seg_hi):
        anns = []
        for code in ["000905.SH", "000852.SH"]:
            d = bench_rows[bench_rows["ts_code"] == code].copy()
            d["ds"] = pd.to_datetime(d["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
            d = d[(d["ds"] >= seg_lo) & (d["ds"] < seg_hi)].sort_values("ds")
            if len(d) > 20:
                yrs = max((pd.to_datetime(d["ds"].iloc[-1]) - pd.to_datetime(d["ds"].iloc[0])).days / 365.25, 0.1)
                anns.append((d["close"].iloc[-1] / d["close"].iloc[0]) ** (1 / yrs) - 1)
        return float(np.mean(anns)) if anns else 0.0
    bench = {"TRAIN": bench_ann(TRAIN_START, OOS_CUT), "OOS": bench_ann(OOS_CUT, "2099-01-01")}  # rule-compliance: ok evidence=日期上界哨兵(OOS取2025-06后全部), 非业务参数

    codes = arr["code"]; uniq, first = np.unique(codes, return_index=True)
    order = np.argsort(first); uniq, first = uniq[order], first[order]
    last = np.concatenate([first[1:], [len(codes)]])
    log.info("扫干净二次突破入场(原始参数无peek)...")
    stock_data, entries_by_code = {}, {}
    for si in range(len(uniq)):
        s, e = int(first[si]), int(last[si]); code = str(uniq[si])
        c = arr["close"][s:e].astype(float)
        if len(c) < 160:
            continue
        d = arr["date"][s:e].astype(str); o = arr["open"][s:e].astype(float)
        h = arr["high"][s:e].astype(float); v = arr["volume"][s:e].astype(float)
        state = _weekly_state(d, c)
        ents = entries_A(c, state, v)  # 干净原始二次突破 (BASE_N=60/RETR=0.08, 无peek)
        if not ents:
            continue
        logret = np.diff(np.log(np.clip(c, 1e-9, None)), prepend=np.log(max(c[0], 1e-9)))
        stock_data[code] = (d, o, h, c, v, state)
        entries_by_code[code] = [(ei, float(np.std(logret[max(ei - 19, 0):ei + 1])) or 0.02) for ei in ents]
    n_ent = sum(len(x) for x in entries_by_code.values())
    combos = [dict(zip(GRID.keys(), vals)) for vals in product(*GRID.values())]
    log.info("入场点 %d / %d 股; 扫 %d combos 含成本组合NAV...", n_ent, len(stock_data), len(combos))

    rows = []
    for combo in combos:
        train, oos, n_adm = run_combo(stock_data, entries_by_code, hs300_bull, combo)
        rows.append(dict(combo=combo, tr_ann=train["ann"], tr_mdd=train["mdd"], oos_ann=oos["ann"], oos_mdd=oos["mdd"], n=n_adm))

    print(f"\n二次突破 return↔回撤前沿 (干净入场无peek, 含成本组合NAV, {len(combos)}combos)")
    print(f"  入场点 {n_ent:,} | 小盘基准(中证500/1000): TRAIN年化{bench['TRAIN']*100:+.1f}% / OOS年化{bench['OOS']*100:+.1f}%")
    # train-Pareto 前沿: 高年化 + 浅回撤 (无支配)
    srt = sorted(rows, key=lambda r: -r["tr_ann"])
    pareto, best_mdd = [], -1.0
    for r in srt:
        if r["tr_mdd"] > best_mdd:  # 回撤更浅(mdd更大=更接近0)
            pareto.append(r); best_mdd = r["tr_mdd"]
    print(f"\n  TRAIN return↔回撤 Pareto前沿 (高收益/浅回撤无支配, {len(pareto)}点):")
    print(f"  {'regime/sizing/pos/trail/hold':30}{'TR年化':>8}{'TR回撤':>8}{'TR超额':>8} | {'OOS年化':>8}{'OOS回撤':>8}")
    for r in sorted(pareto, key=lambda x: x["tr_mdd"]):
        cb = r["combo"]; tag = f"{cb['regime']}/{cb['sizing']}/{cb['max_pos']}/{cb['trail']}/{cb['max_hold']}"
        print(f"  {tag:30}{r['tr_ann']*100:>+7.1f}%{r['tr_mdd']*100:>+7.1f}%{(r['tr_ann']-bench['TRAIN'])*100:>+7.1f}% | {r['oos_ann']*100:>+7.1f}%{r['oos_mdd']*100:>+7.1f}%")
    # 全网格统计
    tr_anns = np.array([r["tr_ann"] for r in rows]); tr_mdds = np.array([r["tr_mdd"] for r in rows])
    oos_anns = np.array([r["oos_ann"] for r in rows])
    print(f"\n  全网格 TRAIN年化 范围 [{tr_anns.min()*100:+.0f}%, {tr_anns.max()*100:+.0f}%] / 回撤 [{tr_mdds.min()*100:.0f}%, {tr_mdds.max()*100:.0f}%]")
    print(f"  TRAIN超额>0 占 {(tr_anns>bench['TRAIN']).mean()*100:.0f}% / OOS超额>0 占 {(oos_anns>bench['OOS']).mean()*100:.0f}%")

    # 裁决: 前沿上有没有点 train+OOS 都跑赢小盘基准 (真超额, 非纯beta)
    both_excess = [r for r in rows if r["tr_ann"] > bench["TRAIN"] and r["oos_ann"] > bench["OOS"]]
    print(f"\n  --- 裁决 (return↔回撤前沿特征 + 是否有真超额) ---")
    if both_excess:
        be = max(both_excess, key=lambda r: (r["tr_ann"] - bench["TRAIN"]) + (r["oos_ann"] - bench["OOS"]))
        cb = be["combo"]
        verdict = (f"{len(both_excess)}/{len(combos)} combo train+OOS双段跑赢小盘基准. 最优 {cb['regime']}/{cb['sizing']}/{cb['max_pos']}/{cb['trail']}/{cb['max_hold']}: "
                   f"TRAIN年化{be['tr_ann']*100:+.1f}%(超额{(be['tr_ann']-bench['TRAIN'])*100:+.1f}pp)/回撤{be['tr_mdd']*100:.0f}%, OOS年化{be['oos_ann']*100:+.1f}%/回撤{be['oos_mdd']*100:.0f}%. "
                   f"前沿: 要更高收益须承受更深回撤(time-in-market). 但'双段超额'仍须DSR/扩样本确认非多重比较, 且TRAIN是更可信forward代理(OOS含小盘牛beta)")
    else:
        verdict = (f"前沿上0个combo train+OOS双段跑赢小盘基准超额 → 二次突破组合本质≈小盘beta(择时/风控只调return↔回撤位置, 不产生超额). "
                   f"return↔回撤前沿成立(高收益↔深回撤tradeoff)但无alpha; 真金白银结论: 这是beta工具非alpha策略, 要超额须回到选股(已证难)或换思路")
    print(f"  → {verdict}")

    run_id = "yushen_frontier_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    judges = {"n_entries": n_ent, "bench_train": round(bench["TRAIN"], 4), "bench_oos": round(bench["OOS"], 4),
              "n_both_excess": len(both_excess), "n_combos": len(combos),
              "pareto": [{"combo": r["combo"], "tr_ann": round(r["tr_ann"], 4), "tr_mdd": round(r["tr_mdd"], 4),
                          "oos_ann": round(r["oos_ann"], 4), "oos_mdd": round(r["oos_mdd"], 4)} for r in pareto],
              "summary": verdict[:150]}
    vlabel = "FRONTIER_REAL_EXCESS" if both_excess else "FRONTIER_BETA_ONLY"
    with open_store() as store:
        record_verdict(store, run_id=run_id, family="yushen_frontier", verdict=vlabel, judges=judges, confirmed_by_owner=0)
    print(f"\n  [experiment_store] 已留档 family=yushen_frontier verdict={vlabel}")


if __name__ == "__main__":
    main()
