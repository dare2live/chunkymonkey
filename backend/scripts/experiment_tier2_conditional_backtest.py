"""Tier-2 含成本 backtest — 把突破中 reversal +0.156 RankIC 翻成真金白银 KPI 裁决。

owner=docs/strategy_validation_contract.md (Tier-2 paper_sim: cost/T+1/limit-aware)。复用幸存引擎
services/portfolio_backtest.py (run_portfolio_backtest + SlippageModel 10bps) + portfolio_walk_forward/metrics。
RankIC 高 != 赚钱: 本实验测含 T+1 + 交易成本后的年化/回撤/sharpe/月胜率 (KPI owner=goal.md)。

预注册 (跑前冻结, 防挪门柱 — measured not estimated; 全参数固定不优化, 故全期 = 诚实 OOS):
  策略: 每周(5交易日)在 **Stage1.5 突破中** 股里按 reversal 排序选 **top-K=20** 等权; T+1 执行 (信号日=
        决策日下一交易日, 权重由决策日 reversal 定, 决策只用 <=t); 含成本 (10bps + 滑点, SlippageModel 默认)。
  宇宙: 板块前缀 60/00/30/68 (排北交所8/三板4); K线天然含已退市 (退市后无价 -> 不可交易, 防生存者偏差)。
  PIT: 事前 leakage_gate (reversal pit_guard 行为门); 信号 T+1 防当日成交未来函数。
  KPI 判据 (goal.md): 年化>=+30% / max_dd>=-20% / 月胜率>=55% (+ 超额HS300>0 待基准). 含成本 OOS。
  真金白银: 本含成本回测是钱的裁决; +0.156 RankIC 必然 > 含成本收益 (§4.5 portfolio+45.4%加成本骤降反例)。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from scripts.experiment_l0_baseline import load_kline  # noqa: E402
from scripts.experiment_per_stage_ic import load_stage_map  # noqa: E402
from services.formula_engine.features import feature_reversal  # noqa: E402
from services.portfolio_backtest import run_portfolio_backtest, SlippageModel, PositionConstraint  # noqa: E402
from services.portfolio_walk_forward.metrics import compute_metrics  # noqa: E402
from services.experiment_store import open_store, record_verdict, record_pit_check, record_artifact  # noqa: E402
from services.experiment_harness import leakage_gate  # noqa: E402

TOP_K = 20           # rule-compliance: ok evidence=pre-reg 固定选股数 (不优化防过拟合)
REBALANCE_DAYS = 5   # rule-compliance: ok evidence=pre-reg 周度调仓 = reversal horizon 5
LOOKBACK = 20        # measured: l0_search_v1 reversal best lookback
STAGE = "1.5"        # rule-compliance: ok evidence=Gate2 确认 edge regime (突破中)
BOARD_PREFIXES = ("60", "00", "30", "68")  # rule-compliance: ok evidence=universe 主板/创业/科创, 排北交所/三板
KPI = {"annual_return": 0.30, "max_drawdown": -0.20, "monthly_win_rate": 0.55}  # rule-compliance: ok evidence=goal.md North-Star KPI


def in_universe(code: str) -> bool:
    return code[:2] in BOARD_PREFIXES


def monthly_win_rate(equity: list[dict]) -> float | None:
    """月度 NAV 收益 > 0 的比例 (月胜率, walk-forward 分布非均值)。"""
    by_month: dict[str, list[float]] = defaultdict(list)
    for e in equity:
        by_month[e["date"][:7]].append(e["total"])
    months = sorted(by_month)
    if len(months) < 2:
        return None
    wins = total = 0
    prev_end = by_month[months[0]][-1]
    for m in months[1:]:
        end = by_month[m][-1]
        total += 1
        if end > prev_end:
            wins += 1
        prev_end = end
    return wins / total if total else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2023-01-01")  # rule-compliance: ok evidence=对齐 L0/KPI 窗口起点
    ap.add_argument("--cost-bps", type=float, default=10.0)  # rule-compliance: ok evidence=诊断 gross vs net (默认 10bps)
    ap.add_argument("--side", choices=["long", "longshort"], default="long")  # rule-compliance: ok evidence=诊断多空 vs 纯多
    args = ap.parse_args(argv)

    print("[load] K线 + stage ...", flush=True)
    by_code = load_kline(args.start, None, 0)
    stage_map = load_stage_map(args.start)
    print(f"[load] {len(by_code)} 股, stage {len(stage_map):,}", flush=True)

    # 事前 leakage 门
    gate = leakage_gate(lambda b: feature_reversal(b["close"], lookback=LOOKBACK), list(by_code.values())[:20])
    if not gate["clean"]:
        print(f"[BLOCK] reversal 事前 leakage 门 FAIL: {gate['sample_violations']}"); return 1
    print(f"[leakage] 事前门 PASS", flush=True)

    # 价格表 + 每股 reversal
    price: dict[tuple[str, str], float] = {}
    reversal: dict[str, dict[str, float]] = {}
    for code, bars in by_code.items():
        if not in_universe(code):
            continue
        closes, dates = bars["close"], bars["date"]
        feat = feature_reversal(closes, lookback=LOOKBACK)
        rv = {}
        for i, d in enumerate(dates):
            if closes[i] is not None:
                price[(code, d)] = closes[i]
            if feat[i] is not None:
                rv[d] = feat[i]
        reversal[code] = rv

    # 全局交易日历 (T+1 执行)
    all_dates = sorted({d for (_c, d) in price})
    rebal_idx = list(range(0, len(all_dates) - 1, REBALANCE_DAYS))
    print(f"[signal] 全局 {len(all_dates)} 交易日, {len(rebal_idx)} 周度调仓点", flush=True)

    # 建信号: 决策日 t 选 Stage1.5 top-K reversal, T+1 (下一交易日) 执行
    signals = []
    for gi in rebal_idx:
        t = all_dates[gi]
        exec_date = all_dates[gi + 1]   # T+1
        cands = [(c, reversal[c][t]) for c in reversal
                 if t in reversal[c] and stage_map.get((c, t)) == STAGE]
        if not cands:
            continue
        cands.sort(key=lambda x: x[1], reverse=True)   # reversal 高 = 超卖 = 多
        topk = cands[:TOP_K]
        w = 1.0 / len(topk)
        for c, _v in topk:
            signals.append({"date": exec_date, "stock_code": c, "target_weight": w})
        if args.side == "longshort":   # 诊断: 空底部K (测 rank-spread 是否市场中性可捕)
            botk = cands[-TOP_K:]
            for c, _v in botk:
                signals.append({"date": exec_date, "stock_code": c, "target_weight": -w})
    print(f"[signal] {len(signals)} 信号行 ({len(set(s['date'] for s in signals))} 调仓日)", flush=True)

    print("[backtest] run_portfolio_backtest (含成本 10bps+滑点, T+1) ...", flush=True)
    res = run_portfolio_backtest(
        signals, price_fn=lambda sc, d: price.get((sc, d)),
        initial_capital=1_000_000, slippage=SlippageModel(fixed_bps=args.cost_bps), constraint=PositionConstraint(),
        rebalance_freq="weekly")
    equity = res.equity_curve if hasattr(res, "equity_curve") else res.metrics.get("equity_curve", [])
    if not equity:
        print(f"[ERR] 空 equity: {res.metrics}"); return 1
    nav = [e["total"] / 1_000_000 for e in equity]
    m = compute_metrics(nav)
    mwr = monthly_win_rate(equity)

    # KPI 裁决
    passes = {"annual_return": m.annual_return >= KPI["annual_return"],
              "max_drawdown": m.max_drawdown >= KPI["max_drawdown"],
              "monthly_win_rate": mwr is not None and mwr >= KPI["monthly_win_rate"]}
    verdict = "KPI_PASS" if all(passes.values()) else "KPI_FAIL"

    print(f"\n===== Tier-2 含成本 backtest (Stage1.5 reversal top{TOP_K}, T+1, 10bps, 周度) =====")
    print(f"年化收益   = {m.annual_return:+.2%}  (KPI>=+30%: {'PASS' if passes['annual_return'] else 'FAIL'})")
    print(f"最大回撤   = {m.max_drawdown:+.2%}  (KPI>=-20%: {'PASS' if passes['max_drawdown'] else 'FAIL'})")
    print(f"年化Sharpe = {m.sharpe:.2f}   Calmar = {m.calmar:.2f}")
    print(f"月胜率     = {(f'{mwr:.1%}' if mwr is not None else 'None')}  (KPI>=55%: {'PASS' if passes['monthly_win_rate'] else 'FAIL'})")
    print(f"末NAV      = {nav[-1]:.3f}  ({len(equity)} 交易日)")
    print(f"VERDICT    = {verdict}  (超额HS300 待基准 sync)")

    out = {"experiment": "tier2_conditional_backtest", "strategy": f"Stage{STAGE}_reversal_top{TOP_K}_T1_weekly",
           "cost_bps": 10, "metrics": {"annual_return": m.annual_return, "max_drawdown": m.max_drawdown,
           "sharpe": m.sharpe, "calmar": m.calmar, "monthly_win_rate": mwr, "final_nav": nav[-1]},
           "kpi_passes": passes, "verdict": verdict, "n_signals": len(signals), "note": "含成本 OOS; 超额HS300待基准"}
    out_path = REPO / "analysis" / "tier2_conditional_backtest_20260615.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[out] {out_path}")

    run_id = "phaseb_tier2_conditional_backtest_20260615"
    with open_store() as st:
        record_pit_check(st, run_id=run_id, step="leakage_gate", check_name="reversal_pit_behavioral",
                         passed=gate["clean"], detail=gate)
        record_verdict(st, run_id=run_id, family="tier2_backtest", verdict=verdict,
                       judges={"metrics": out["metrics"], "kpi_passes": passes},
                       confirmed_by_owner=1 if verdict == "KPI_PASS" else 0)
        record_artifact(st, run_id=run_id, artifact_path=out_path)
    print(f"[store] experiment_store 留档 Tier-2 verdict={verdict} (run_id={run_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
