"""Tier-2 含成本 backtest — 用 execution-aware 引擎把突破中 reversal +0.156 RankIC 翻成真金白银 KPI 裁决。

owner=docs/strategy_validation_contract.md (判断法典 C-R1/C-R2/C-WinReturn)。2026-06-15 P1 重建:
旧 portfolio_returnbacktest (close 无条件成交=无摩擦, R2 缺陷) 退役删除, 改用 portfolio_execbacktest
(T+1 open 入场 / 涨停一字板剔篮 / 非对称成本栈 / 停牌冻结 / 容量诊断 / 仓位 sizing) = 真裁决。

裁决用法典工具 (非裸 KPI):
  - tradability_verdict(cohort IC, 含成本净年化): R1 对称门 — IC>0 但 net<=0 -> IC_POSITIVE_BUT_UNTRADABLE。
  - kpi_verdict(metrics): C-WinReturn 联合门 — 年化 AND max_dd AND 月胜率 AND 胜率x盈亏比期望; 胜率=诊断量。

预注册 (跑前冻结, 防挪门柱):
  策略: 每 rebalance_days 在 Stage 突破中股按 reversal 排序选 top-K 等权(默认)/rank; T+1 **open** 执行; 含成本(config)。
  宇宙: 板块前缀 60/00/30/68; K线含已退市 (停牌冻结/退市归零防生存者偏差)。
  PIT: 事前 leakage_gate; 信号决策日定, T+1 执行。
  真金白银: 含成本 execution-aware 回测是钱的裁决; +0.156 RankIC 必然 > 含成本收益 (§4.5)。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from scripts.experiment_l0_baseline import load_kline  # noqa: E402
from scripts.experiment_per_stage_ic import load_stage_map  # noqa: E402
from services.formula_engine.features import feature_reversal  # noqa: E402
from services.portfolio_execbacktest import run_execution_backtest, ExecConfig  # noqa: E402
from services.experiment_store import open_store, record_verdict, record_pit_check, record_artifact  # noqa: E402
from services.experiment_harness import leakage_gate, tradability_verdict, kpi_verdict  # noqa: E402
from scripts.experiment_layered_segment_ic import load_daily_basic, _tier  # noqa: E402

TOP_K = 20           # rule-compliance: ok evidence=pre-reg 固定选股数 (不优化防过拟合)
REBALANCE_DAYS = 5   # rule-compliance: ok evidence=pre-reg 周度调仓 = reversal horizon 5
LOOKBACK = 20        # measured: l0_search_v1 reversal best lookback
STAGE = "1.5"        # rule-compliance: ok evidence=Gate2 确认 edge regime (突破中)
STAGE15_IC = 0.156   # rule-compliance: ok evidence=per_stage_ic 实测 Stage1.5 reversal OOS RankIC (cohort IC, R1 对称门入参)
BOARD_PREFIXES = ("60", "00", "30", "68")  # rule-compliance: ok evidence=universe 主板/创业/科创, 排北交所/三板


def in_universe(code: str) -> bool:
    return code[:2] in BOARD_PREFIXES


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2023-01-01")  # rule-compliance: ok evidence=对齐 L0/KPI 窗口起点
    ap.add_argument("--sizing", choices=["equal", "rank", "inverse_vol"], default="equal")  # rule-compliance: ok evidence=仓位 policy (C-WinReturn 一等轴)
    ap.add_argument("--rebalance-days", type=int, default=REBALANCE_DAYS)  # rule-compliance: ok evidence=诊断换手 vs 成本
    ap.add_argument("--cap-tier", choices=["all", "low", "mid", "high"], default="all")  # rule-compliance: ok evidence=市值子格过滤
    ap.add_argument("--turnover-tier", choices=["all", "low", "mid", "high"], default="all")  # rule-compliance: ok evidence=换手子格过滤
    args = ap.parse_args(argv)
    rebalance_days = args.rebalance_days

    print("[load] K线(OHLCV) + stage ...", flush=True)
    by_code = load_kline(args.start, None, 0)
    stage_map = load_stage_map(args.start)
    print(f"[load] {len(by_code)} 股, stage {len(stage_map):,}", flush=True)

    # 事前 leakage 门
    gate = leakage_gate(lambda b: feature_reversal(b["close"], lookback=LOOKBACK), list(by_code.values())[:20])
    if not gate["clean"]:
        print(f"[BLOCK] reversal 事前 leakage 门 FAIL: {gate['sample_violations']}"); return 1
    print("[leakage] 事前门 PASS", flush=True)

    # bars_by_code {code:{date:(o,h,l,c,v)}} (execution 引擎接口) + 每股 reversal 信号
    bars_by_code: dict[str, dict] = {}
    reversal: dict[str, dict] = {}
    for code, bars in by_code.items():
        if not in_universe(code):
            continue
        dates = bars["date"]
        feat = feature_reversal(bars["close"], lookback=LOOKBACK)
        bb, rv = {}, {}
        for i, d in enumerate(dates):
            c = bars["close"][i]
            if c is not None:
                bb[d] = (bars["open"][i], bars["high"][i], bars["low"][i], c, bars["volume"][i])
            if feat[i] is not None:
                rv[d] = feat[i]
        bars_by_code[code] = bb
        reversal[code] = rv

    all_dates = sorted({d for bb in bars_by_code.values() for d in bb})
    rebal_idx = list(range(0, len(all_dates) - 1, rebalance_days))
    print(f"[signal] 全局 {len(all_dates)} 交易日, {len(rebal_idx)} 调仓点", flush=True)

    # 市值/换手 cell 过滤 (best 子格 = cap_low × turnover_high)
    cell_filter = args.cap_tier != "all" or args.turnover_tier != "all"
    db = load_daily_basic(args.start) if cell_filter else {}
    if cell_filter:
        print(f"[cell] 过滤子格 cap={args.cap_tier} turnover={args.turnover_tier} (daily_basic {len(db):,})", flush=True)

    # 调仓表: 决策日 t 选 Stage top-K reversal (引擎做 T+1 open + 含成本 execution-aware)
    rebalances = []
    for gi in rebal_idx:
        t = all_dates[gi]
        cands = [(c, reversal[c][t]) for c in reversal
                 if t in reversal[c] and stage_map.get((c, t)) == STAGE]
        if cell_filter and cands:
            wb = [(c, rv) for c, rv in cands if (c, t) in db]
            if len(wb) >= 9:  # rule-compliance: ok evidence=截面分位最小样本
                mvs = sorted(db[(c, t)][0] for c, _ in wb)
                tos = sorted(db[(c, t)][1] for c, _ in wb)
                k = len(mvs); mv_lo, mv_hi = mvs[k // 3], mvs[2 * k // 3]; to_lo, to_hi = tos[k // 3], tos[2 * k // 3]
                cands = [(c, rv) for c, rv in wb
                         if (args.cap_tier == "all" or _tier(db[(c, t)][0], mv_lo, mv_hi) == args.cap_tier)
                         and (args.turnover_tier == "all" or _tier(db[(c, t)][1], to_lo, to_hi) == args.turnover_tier)]
            else:
                cands = []
        if not cands:
            continue
        cands.sort(key=lambda x: x[1], reverse=True)   # reversal 高 = 超卖 = 多 (signal 降序, 供 rank sizing)
        rebalances.append((t, cands[:TOP_K]))
    print(f"[backtest] execution-aware 引擎 ({len(rebalances)} 调仓, T+1 open, sizing={args.sizing}, 含成本+涨跌停+容量) ...", flush=True)

    res = run_execution_backtest(rebalances, bars_by_code, all_dates,
                                 config=ExecConfig.load(), sizing=args.sizing, top_k=TOP_K)
    if not res["nav"]:
        print("[ERR] 空 NAV"); return 1
    m = res["metrics"]

    # 法典裁决: R1 对称门 + C-WinReturn 联合门
    trad = tradability_verdict(STAGE15_IC, m["annual_return"])
    kpi = kpi_verdict(m)
    verdict = kpi["verdict"]   # KPI_PASS / KPI_FAIL

    def pct(x):
        return f"{x:+.2%}" if isinstance(x, (int, float)) else "None"

    print(f"\n===== Tier-2 execution-aware backtest (Stage{STAGE} reversal top{TOP_K}, T+1 open, sizing={args.sizing}) =====")
    print(f"年化收益   = {pct(m['annual_return'])}  (KPI>=+30%: {'PASS' if kpi['passes']['annual_return'] else 'FAIL'})")
    print(f"最大回撤   = {pct(m['max_drawdown'])}  (KPI>=-20%: {'PASS' if kpi['passes']['max_drawdown'] else 'FAIL'})")
    print(f"年化Sharpe = {m['sharpe']:.2f}   Calmar = {m['calmar']:.2f}")
    print(f"月胜率     = {pct(m['monthly_win_rate']) if m['monthly_win_rate'] else 'None'} (诊断量, KPI>=55%: {'PASS' if kpi['passes']['monthly_win_rate'] else 'FAIL'})")
    print(f"段胜率     = {pct(m['win_rate']) if m['win_rate'] else 'None'}  盈亏比 = {m['payoff_ratio'] if m['payoff_ratio'] else 'None'}  期望 = {m['expectancy'] if m['expectancy'] else 'None'}")
    print(f"末NAV      = {res['final_nav']:.3f}  ({len(res['nav'])} 交易日, 成本拖累 {res['cost_drag']:.1%}, 均换手 {res['avg_turnover']:.2f})")
    print(f"容量       = 均参与度 {res['avg_participation']}  max {res['max_participation']}  超阈率 {res['capacity_warn_rate']:.1%}")
    print(f"R1 可交易性 = {trad['verdict']} ({trad['action'][:60]})")
    print(f"VERDICT    = {verdict}  (超额HS300 待基准; 小盘 cohort 须对标中证1000/2000)")

    out = {"experiment": "tier2_conditional_backtest", "engine": "portfolio_execbacktest_20260615",
           "strategy": f"Stage{STAGE}_reversal_top{TOP_K}_T1open_{args.sizing}",
           "metrics": {**m, "final_nav": res["final_nav"], "cost_drag": res["cost_drag"],
                       "avg_turnover": res["avg_turnover"], "avg_participation": res["avg_participation"],
                       "max_participation": res["max_participation"], "capacity_warn_rate": res["capacity_warn_rate"]},
           "tradability": trad, "kpi_verdict": kpi, "verdict": verdict, "n_rebalances": res["n_rebalances"],
           "note": "execution-aware 含成本 OOS (T+1 open/涨跌停/非对称成本/停牌冻结/容量); 超额HS300待基准"}
    out_path = REPO / "analysis" / "tier2_conditional_backtest_20260615.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[out] {out_path}")

    run_id = "phaseb_tier2_conditional_backtest_20260615"
    with open_store() as st:
        record_pit_check(st, run_id=run_id, step="leakage_gate", check_name="reversal_pit_behavioral",
                         passed=gate["clean"], detail=gate)
        # judges 含 kpi_verdict + tradability = 含成本绝对收益证据 (C-R1 转正 guard 放行依据)
        record_verdict(st, run_id=run_id, family="tier2_backtest", verdict=verdict,
                       judges={"metrics": out["metrics"], "kpi_verdict": kpi, "tradability": trad},
                       confirmed_by_owner=0)   # KPI_FAIL 不转正; 即便 PASS 转正也由 owner 显式定
        record_artifact(st, run_id=run_id, artifact_path=out_path)
    print(f"[store] experiment_store 留档 Tier-2 verdict={verdict} R1={trad['verdict']} (run_id={run_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
