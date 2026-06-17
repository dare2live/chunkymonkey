"""experiment_yushen_exit_timing — 出场择时对比 (主会话主导, 2026-06-17, 转出场轴)。

owner: 入场空间~12角度全测尽=regime-beta(买点近信噪比上限); 研究+框架(yushen §4/§8.7)裁定 edge 在出场择时+仓位。
"出场立为独立研究对象" (框架spec): 同一批二次突破入场, 比多种出场规则的含成本收益+回撤, 看哪种吃到最多鱼身躲掉鱼尾。
研究标的最高价值单项 = CYQ出货预警(筹码 winner_rate高+价超成本95分位=获利盘无套牢支撑=顶部风险, 0代码却最高价值)。
出场规则:
  E1 trail12  : 移动止盈 close<峰*0.88 (基线)
  E2 weekbreak: 周线破位 (周确认态转False)
  E3 deathcross: MACD死叉 (dif下穿dea) — 实测最差基线(中位-2.25%)
  E4 cyq_dist : CYQ出货预警 (winner_rate>=0.92 OR close>=cost_95pct = 获利盘饱和顶部风险)
  E5 cyq_trail: E4 OR E1 (筹码顶 + 移动止盈双触)
  E6 hold60   : 固定持有60日 (无择时基线)
裁定: 各出场的 per-trade含成本均值/中位/胜率 + 单仓顺序权益 年化/max_dd, train/OOS; 看 CYQ/智能出场是否>基线。
PIT: 二次突破入场<=t(复用), T+1 open; 出场信号<=持有日; cyq winner_rate/cost t-1盘后已知; 含成本13bps。
源: market K线 + tushare_raw.cyq_perf(winner_rate/cost_95pct)。复用 _weekly_state/entries_A(DRY)。
用法: PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_yushen_exit_timing.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import duckdb  # rule-compliance: ok evidence=只读出场择时对比; manifest; allowlist
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest
from services.experiment_store import open_store, record_verdict
from scripts.experiment_yushen_clean_baseline import _weekly_state, entries_A

log = logging.getLogger("exit_timing")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

COST = 0.0013        # rule-compliance: ok evidence=A股双边13bps, 同鱼身系列
TRAIL = 0.88         # rule-compliance: ok evidence=移动止盈12%, 同
MAX_HOLD = 120       # rule-compliance: ok evidence=持有硬上限(防无限持有)
OOS_CUT = "2025-06-01"  # rule-compliance: ok evidence=方法论OOS切点(MASTER§5)
WINNER_HI = 0.92     # rule-compliance: ok evidence=CYQ出货阈值(获利盘>=92%=饱和顶部风险, §4.5 px_pctile D9退出口径), 结构常数
EXIT_RULES = ["trail12", "weekbreak", "deathcross", "cyq_dist", "cyq_trail", "hold60"]


def _macd(c):
    s = pd.Series(c)
    dif = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    return dif.to_numpy(), dea.to_numpy()


def simulate_exit(o, h, c, state, dif, dea, wr, cost95, ei, rule):
    """从 ei 入场(T+1 open), 按 rule 找出场日, 返回 (含成本收益, 持有日数)。"""
    n = len(c)
    entry = o[ei + 1] if ei + 1 < n and o[ei + 1] > 0 else c[ei]
    peak = entry
    for j in range(ei + 1, min(ei + MAX_HOLD, n - 1) + 1):
        peak = max(peak, h[j])
        hit = False
        if rule == "trail12":
            hit = c[j] < peak * TRAIL
        elif rule == "weekbreak":
            hit = not state[j]
        elif rule == "deathcross":
            hit = j >= 1 and dif[j] < dea[j] and dif[j - 1] >= dea[j - 1]
        elif rule == "cyq_dist":
            hit = (not np.isnan(wr[j]) and wr[j] >= WINNER_HI) or (not np.isnan(cost95[j]) and cost95[j] > 0 and c[j] >= cost95[j])
        elif rule == "cyq_trail":
            cyq = (not np.isnan(wr[j]) and wr[j] >= WINNER_HI) or (not np.isnan(cost95[j]) and cost95[j] > 0 and c[j] >= cost95[j])
            hit = cyq or c[j] < peak * TRAIL
        elif rule == "hold60":
            hit = (j - ei) >= 60
        if hit:
            return float(c[j] / entry - 1.0 - COST), j - ei
    xi = min(ei + MAX_HOLD, n - 1)
    return float(c[xi] / entry - 1.0 - COST), xi - ei


def single_pos_equity(trades):
    """trades=[(entry_date,exit_date,ret)] 单仓顺序(平了再开最早), 年化+maxdd。"""
    trades.sort(key=lambda x: x[0])
    eq = 1.0; navs = []; free = ""
    for ed, xd, r in trades:
        if ed > free:
            eq *= (1 + r); free = xd; navs.append((xd, eq))
    if len(navs) < 5:
        return 0.0, 0.0, 0
    a = np.array([e for _, e in navs])
    peak = np.maximum.accumulate(a)
    mdd = float(np.min(a / peak - 1))
    yrs = max((pd.to_datetime(navs[-1][0]) - pd.to_datetime(navs[0][0])).days / 365.25, 0.1)
    ann = float(a[-1] ** (1 / yrs) - 1)
    return ann, mdd, len(navs)


def main():
    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读; manifest; allowlist
    con.execute(f"ATTACH '{mf.path_for('tushare_raw')}' AS tr (READ_ONLY)")
    log.info("载入 K线 + cyq 筹码 join...")
    big = con.execute("""
        SELECT k.code, k.date, k.open, k.high, k.close, k.volume, cy.winner_rate, cy.cost_95pct
        FROM price_kline_qfq_tushare k
        LEFT JOIN tr.raw_tushare_cyq_perf cy ON SUBSTR(cy.ts_code,1,6)=k.code AND cy.trade_date=REPLACE(k.date::VARCHAR,'-','')
        WHERE k.date>='2019-01-01' AND k.close>0 ORDER BY k.code, k.date
    """).df()
    con.close()

    out = {r: [] for r in EXIT_RULES}
    for code, g in big.groupby("code"):
        g = g.reset_index(drop=True)
        c = g["close"].to_numpy(float)
        if len(c) < 160:
            continue
        o, h, v = g["open"].to_numpy(float), g["high"].to_numpy(float), g["volume"].to_numpy(float)
        dates = g["date"].astype(str).to_numpy()
        wr = g["winner_rate"].to_numpy(float)
        cost95 = g["cost_95pct"].to_numpy(float)
        state = _weekly_state(dates, c)
        dif, dea = _macd(c)
        for ei in entries_A(c, state, v):
            if ei + 1 >= len(c):
                continue
            edate = dates[ei + 1]
            for rule in EXIT_RULES:
                r, hold = simulate_exit(o, h, c, state, dif, dea, wr, cost95, ei, rule)
                xi = min(ei + 1 + hold, len(dates) - 1)
                out[rule].append((edate, dates[xi], r, hold))

    print(f"\n出场择时对比 (同一批二次突破入场, 含成本13bps, per-trade + 单仓权益; train/OOS)")
    n_ent = len(out["trail12"])
    print(f"  二次突破入场 {n_ent:,} | 每规则同入场不同出场")
    print(f"  {'出场规则':12}{'段':6}{'均值':>8}{'中位':>8}{'胜率':>7}{'持有日':>7}{'>30%':>7} | {'单仓年化':>9}{'单仓maxdd':>10}")
    res = {}
    for rule in EXIT_RULES:
        for seg, lo, hi in [("TRAIN", "2019-01-01", OOS_CUT), ("OOS", OOS_CUT, "2099-12-31")]:  # rule-compliance: ok evidence=TRAIN/OOS分段边界哨兵(2019预热起/2099上界), 非业务参数
            sub = [(ed, xd, r, hold) for (ed, xd, r, hold) in out[rule] if lo <= ed < hi]
            if len(sub) < 20:
                continue
            rr = np.array([x[2] for x in sub]); hd = np.array([x[3] for x in sub])
            ann, mdd, npos = single_pos_equity([(ed, xd, r) for ed, xd, r, _ in sub])
            res[(rule, seg)] = dict(n=len(rr), mean=float(rr.mean()), med=float(np.median(rr)), win=float((rr > 0).mean()),
                                    hold=float(hd.mean()), p30=float((rr > 0.30).mean()), ann=ann, mdd=mdd)
            print(f"  {rule:12}{seg:6}{rr.mean()*100:>+7.2f}%{np.median(rr)*100:>+7.2f}%{(rr>0).mean()*100:>6.1f}%{hd.mean():>7.0f}{(rr>0.30).mean()*100:>6.1f}% | {ann*100:>+8.1f}%{mdd*100:>+9.1f}%")

    # 裁决: CYQ/智能出场 vs trail12 基线 (per-trade均值 + 单仓maxdd, train/OOS)
    def cmp(rule, seg, metric):
        return res.get((rule, seg), {}).get(metric, np.nan)
    print(f"\n  --- 裁决 (出场择时是否优于移动止盈基线 trail12) ---")
    base_tr, base_oos = cmp("trail12", "TRAIN", "mean"), cmp("trail12", "OOS", "mean")
    best_rule, best_score = None, -9
    for rule in EXIT_RULES:
        if rule == "trail12":
            continue
        d_tr = cmp(rule, "TRAIN", "mean") - base_tr
        d_oos = cmp(rule, "OOS", "mean") - base_oos
        dd_tr = cmp(rule, "TRAIN", "mdd") - cmp("trail12", "TRAIN", "mdd")  # 正=回撤更浅(更好)
        print(f"  {rule:12}: per-trade增量 TRAIN{d_tr*100:+.2f}pp/OOS{d_oos*100:+.2f}pp | 单仓maxdd vs基线 TRAIN{dd_tr*100:+.1f}pp")
        sc = (d_tr + d_oos) * 100  # 双段 per-trade 增量和
        if not np.isnan(sc) and sc > best_score:
            best_score, best_rule = sc, rule
    if best_rule and best_score > 0.3:
        verdict = f"出场择时有改进: {best_rule} per-trade双段增量和{best_score:+.2f}pp>基线trail12 → 出场轴有真 edge(入场已exhausted); 下一步 Optuna 调出场参数+组合NAV+叠仓位管理"
    elif best_rule and best_score > 0:
        verdict = f"出场择时边际改进: {best_rule} 双段和{best_score:+.2f}pp 略优, 但弱; 须配仓位管理/regime门看含成本组合KPI"
    else:
        verdict = f"现有出场规则均不显著优于trail12移动止盈: 出场单轴改不动 → edge须靠仓位管理(把edge/盈亏分布转收益/回撤)+regime beta捕获, 出场保持简单trail"
    print(f"\n  → {verdict}")

    run_id = "yushen_exit_timing_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    judges = {"n_entries": n_ent, "best_rule": best_rule, "best_score_pp": round(best_score, 3) if best_rule else None,
              "rules": {f"{r}_{s}": res[(r, s)] for (r, s) in res}, "summary": verdict[:150]}
    vlabel = "EXIT_IMPROVES" if (best_rule and best_score > 0.3) else ("EXIT_MARGINAL" if (best_rule and best_score > 0) else "EXIT_NO_EDGE")
    with open_store() as store:
        record_verdict(store, run_id=run_id, family="yushen_exit_timing", verdict=vlabel, judges=judges, confirmed_by_owner=0)
    print(f"\n  [experiment_store] 已留档 family=yushen_exit_timing verdict={vlabel}")


if __name__ == "__main__":
    main()
