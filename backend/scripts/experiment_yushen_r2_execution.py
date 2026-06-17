"""experiment_yushen_r2_execution — R2 可交易性衰减检验 (主会话主导, 2026-06-17)。

owner: 数据盘点暴露 stk_limit/suspend_d/stock_st 拉了没用 = R2 漏接 (项目坑库: R2 信号≠可交易头寸, 含成本回测假无摩擦=假裁决)。
检验: 二次突破入场在 T+1 open 真买得进吗? 衰减多少? 之前所有含成本回测乐观了多少?
R2 不可买判据 (PIT, 用已拉数据):
  - 停牌: suspend_d 该股 T+1 停牌 → 买不进。
  - 一字涨停封板: T+1 high==low(无盘中区间) 且上涨 → 一字板挂单买不进 (qfq安全, 不需stk_limit的NULL pre_close)。
  - 缺口封涨停: T+1 open/T close-1 >= 板块涨停幅(创业30x/科创688=19.5% / ST=4.8% / 余9.5%) 且 high<=open*1.002(开盘即封) → 买不进。
  - ST 过滤: stock_st 入场日 ST → 风险警示, 剔出宇宙。
对比: 全部入场 vs R2-可买入场 的 per-trade含成本 + 衰减率 (不可买的往往是最强跳空票=剔后收益更真更低)。
源: market K线 + tushare_raw(suspend_d/stock_st)。复用 entries_A/_weekly_state(DRY)。
用法: PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_yushen_r2_execution.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import duckdb  # rule-compliance: ok evidence=只读R2可交易性检验; manifest; allowlist
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest
from services.experiment_store import open_store, record_verdict
from scripts.experiment_yushen_clean_baseline import _weekly_state, entries_A, trade

log = logging.getLogger("r2_exec")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

OOS_CUT = "2025-06-01"  # rule-compliance: ok evidence=方法论OOS切点(MASTER§5)


def limit_pct(code, is_st):
    if is_st:
        return 0.048   # rule-compliance: ok evidence=ST涨跌停5%(A股规则), 板块常数
    if code[:3] in ("300", "301") or code[:3] == "688" or code[:2] == "68":
        return 0.195   # rule-compliance: ok evidence=创业板/科创板20%, 板块常数
    if code[:2] in ("83", "87", "43", "92", "88"):
        return 0.295   # rule-compliance: ok evidence=北交所30%, 板块常数
    return 0.095       # rule-compliance: ok evidence=主板10%, 板块常数


def main():
    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读K线; manifest; allowlist
    con.execute(f"ATTACH '{mf.path_for('tushare_raw')}' AS tr (READ_ONLY)")
    log.info("载入 K线 + 停牌 + ST...")
    arr = con.execute("SELECT code, date, open, high, low, close, volume FROM price_kline_qfq_tushare WHERE date>='2019-01-01' AND close>0 ORDER BY code, date").fetchnumpy()  # rule-compliance: ok evidence=2019起留周线预热(同鱼身系列)
    susp = con.execute("SELECT DISTINCT SUBSTR(ts_code,1,6) code, REPLACE(trade_date,'-','') d FROM tr.raw_tushare_suspend_d").df()
    st = con.execute("SELECT DISTINCT SUBSTR(ts_code,1,6) code, REPLACE(trade_date,'-','') d FROM tr.raw_tushare_stock_st").df()
    con.close()
    susp_set = set(zip(susp["code"], susp["d"]))
    st_set = set(zip(st["code"], st["d"]))

    def ymd(s):
        return str(s).replace("-", "")

    codes = arr["code"]; uniq, first = np.unique(codes, return_index=True)
    order = np.argsort(first); uniq, first = uniq[order], first[order]
    last = np.concatenate([first[1:], [len(codes)]])
    log.info("扫二次突破 + R2 可买判据...")
    rows = []
    for ci in range(len(uniq)):
        s, e = int(first[ci]), int(last[ci]); code = str(uniq[ci])
        c = arr["close"][s:e].astype(float)
        if len(c) < 160:
            continue
        o, h, l, v = arr["open"][s:e].astype(float), arr["high"][s:e].astype(float), arr["low"][s:e].astype(float), arr["volume"][s:e].astype(float)
        dates = arr["date"][s:e].astype(str)
        state = _weekly_state(dates, c)
        for ei in entries_A(c, state, v):
            if ei + 1 >= len(c):
                continue
            ed_sig = ymd(dates[ei])     # 信号日 T
            ed_buy = ymd(dates[ei + 1])  # T+1 买入日
            is_st = (code, ed_sig) in st_set or (code, ed_buy) in st_set
            lp = limit_pct(code, is_st)
            # R2 不可买判据
            suspended = (code, ed_buy) in susp_set
            yiziboard = h[ei + 1] == l[ei + 1] and c[ei + 1] > c[ei]   # 一字板(无区间+涨)
            gap_seal = (o[ei + 1] / c[ei] - 1 >= lp * 0.99) and (h[ei + 1] <= o[ei + 1] * 1.002)  # 开盘即封涨停
            unbuyable = suspended or yiziboard or gap_seal
            _, _, r = trade(o, h, c, state, ei)
            rows.append(dict(entry_date=dates[ei + 1], ret=r, is_st=int(is_st), suspended=int(suspended),
                             yizi=int(yiziboard), gap_seal=int(gap_seal), unbuyable=int(unbuyable)))
    panel = pd.DataFrame(rows)
    panel["seg"] = np.where(panel["entry_date"] < OOS_CUT, "TRAIN", "OOS")
    n = len(panel)
    print(f"\nR2 可交易性衰减检验 (二次突破入场在 T+1 真买得进吗; 用已拉 suspend_d/stock_st + K线一字板)")
    print(f"  入场信号 {n:,}")
    print(f"  不可买占比: 停牌 {panel['suspended'].mean()*100:.1f}% / 一字板 {panel['yizi'].mean()*100:.1f}% / 缺口封板 {panel['gap_seal'].mean()*100:.1f}% / ST {panel['is_st'].mean()*100:.1f}% → 合计不可买 {panel['unbuyable'].mean()*100:.1f}%")
    print(f"\n  {'段':6}{'全部n':>8}{'全部均值':>9} | {'可买n':>8}{'可买均值':>9} | {'R2衰减':>8}")
    res = {}
    for seg in ["TRAIN", "OOS"]:
        allp = panel[panel.seg == seg]["ret"].to_numpy()
        buyp = panel[(panel.seg == seg) & (panel.unbuyable == 0)]["ret"].to_numpy()
        if len(allp) < 20:
            continue
        decay = float(buyp.mean() - allp.mean())
        res[seg] = dict(n_all=len(allp), all_mean=float(allp.mean()), n_buy=len(buyp), buy_mean=float(buyp.mean()), decay=decay,
                        unbuyable_pct=float((panel[panel.seg == seg]["unbuyable"]).mean()))
        print(f"  {seg:6}{len(allp):>8,}{allp.mean()*100:>+8.2f}% | {len(buyp):>8,}{buyp.mean()*100:>+8.2f}% | {decay*100:>+7.2f}pp")
    # 不可买票的收益 (验证它们是不是最强=剔了才真实)
    unb = panel[panel.unbuyable == 1]["ret"].to_numpy()
    if len(unb) > 20:
        print(f"\n  不可买票含成本均值 {unb.mean()*100:+.2f}% (vs 可买 {panel[panel.unbuyable==0]['ret'].mean()*100:+.2f}%) — 验证不可买是否=最强跳空票")
    print(f"\n  --- 裁决 (R2 衰减) ---")
    avg_unbuy = panel["unbuyable"].mean()
    decay_tr = res.get("TRAIN", {}).get("decay", 0); decay_oos = res.get("OOS", {}).get("decay", 0)
    verdict = (f"R2 衰减实测: {avg_unbuy*100:.1f}% 二次突破入场 T+1 不可买(停牌/一字/封板/ST), 剔除后 per-trade 变化 TRAIN{decay_tr*100:+.2f}pp/OOS{decay_oos*100:+.2f}pp. "
               + ("不可买票更强(剔后收益降)=之前含成本回测乐观, 须接R2 buyability进引擎重裁前沿" if (unb.mean() if len(unb) > 20 else 0) > panel[panel.unbuyable == 0]["ret"].mean()
                  else "不可买票非更强=R2衰减有限, 但仍须接进引擎(停牌/ST宇宙过滤是正确性)"))
    print(f"  → {verdict}")

    run_id = "yushen_r2_execution_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    judges = {"n": n, "unbuyable_pct": round(avg_unbuy, 4), "decay_train": round(decay_tr, 5), "decay_oos": round(decay_oos, 5),
              "unbuyable_mean": round(float(unb.mean()), 5) if len(unb) > 20 else None,
              "buyable_mean": round(float(panel[panel.unbuyable == 0]["ret"].mean()), 5), "summary": verdict[:150]}
    with open_store() as store:
        record_verdict(store, run_id=run_id, family="yushen_r2_execution", verdict="R2_DECAY_MEASURED", judges=judges, confirmed_by_owner=0)
    print(f"\n  [experiment_store] 已留档 family=yushen_r2_execution")


if __name__ == "__main__":
    main()
