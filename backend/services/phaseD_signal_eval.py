"""Phase D 慢衰减绝对源信号评估 harness — 共享评估逻辑 (2026-06-15, 同逻辑 3 次重构, owner=CLAUDE §3)。

把 Phase D 每个信号的评估流水线固化: IC necessary 快筛 → execution-aware 含成本 backtest → R1/C-WinReturn 裁决 → 留档。
每个信号实验只需构建 signal_by_code (PIT) + fwd_by_code + 跑事前 leakage_gate, 调本 harness。想漏法典都漏不掉。

裁决范式 (R1, owner=docs/strategy_validation_contract.md): IC=necessary 快筛 (anomaly_verdict §4.2, 报但不 gate);
  含成本 execution-aware backtest 绝对收益 = sufficient gate (tradability_verdict + kpi_verdict)。选信号按含成本绝对收益不按 IC。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.portfolio_walk_forward.oos_ic import PanelRow, oos_rank_ic
from services.portfolio_execbacktest import run_execution_backtest, ExecConfig
from services.experiment_store import open_store, record_ic_cell, record_verdict, record_pit_check, record_artifact
from services.experiment_harness import anomaly_verdict, tradability_verdict, kpi_verdict


def _pct(x):
    return f"{x:+.2%}" if isinstance(x, (int, float)) else "None"


def evaluate_signal(*, signal_by_code: dict, bars_by_code: dict, calendar: list,
                    fwd_by_code: dict, signal_name: str, run_id: str, family: str,
                    snapshot: str, out_path: Path, consumer_id: str,
                    ic_baseline: float = 0.064, rebalance_days: int = 20, top_k: int = 20,
                    sizing: str = "equal", embargo: int = 5, gate: dict | None = None,
                    regime_ok: dict | None = None, extra: dict | None = None) -> dict:
    """评估一个慢衰减绝对源信号 (signal_by_code={code:{date:val}} 已 PIT; 高=优先做多)。返回 result dict。

    regime_ok (第四轴 Regime/Timing, N6/R1): {date: bool} 绝对方向门。某调仓日 regime_ok=False -> 该期持现金
    (空篮, 引擎自然降仓), 实现 "在对的时候在场" 削 max_dd。None=不择时 (全程满仓 long-only)。
    """
    # IC necessary 快筛 (报, 非 gate)
    panel = [PanelRow(date=d, code=c, feature=signal_by_code[c][d], fwd_ret=fwd_by_code[c][d])
             for c in signal_by_code for d in signal_by_code[c] if d in fwd_by_code.get(c, {})]
    ic_res = oos_rank_ic(panel, embargo_days=embargo)
    ic = ic_res.get("oos_rank_ic")
    av = anomaly_verdict(ic, baseline=ic_baseline)
    print(f"[IC 快筛] {signal_name} OOS RankIC = {ic if ic is None else f'{ic:+.4f}'} (necessary, 非 gate); anomaly={av['verdict']}", flush=True)

    # 调仓表: 每 rebalance_days 选 top-K (signal 高=优先); Regime/Timing 门 risk-off 期持现金 (空篮)
    rebalances = []
    n_cash = 0
    for gi in range(0, len(calendar) - 1, rebalance_days):
        t = calendar[gi]
        if regime_ok is not None and not regime_ok.get(t, True):
            rebalances.append((t, []))   # risk-off -> 现金 (第四轴择时削 max_dd)
            n_cash += 1
            continue
        cands = [(c, signal_by_code[c][t]) for c in signal_by_code if t in signal_by_code[c]]
        if not cands:
            continue
        cands.sort(key=lambda x: x[1], reverse=True)
        rebalances.append((t, cands[:top_k]))
    if regime_ok is not None:
        print(f"[regime] 第四轴择时门: {n_cash}/{len(rebalances)} 调仓期 risk-off 持现金", flush=True)
    print(f"[backtest] execution-aware ({len(rebalances)} 调仓/{rebalance_days}日, T+1 open, sizing={sizing}, 含成本) ...", flush=True)

    res = run_execution_backtest(rebalances, bars_by_code, calendar,
                                 config=ExecConfig.load(), sizing=sizing, top_k=top_k)
    if not res["nav"]:
        print("[ERR] 空 NAV"); return {"verdict": "EMPTY_NAV", "ic_quick_screen": ic}
    m = res["metrics"]
    trad = tradability_verdict(ic, m["annual_return"])
    kpi = kpi_verdict(m)
    verdict = kpi["verdict"]

    print(f"\n===== Phase D: {signal_name} (top{top_k}, T+1 open, {rebalance_days}日调仓, sizing={sizing}) =====")
    print(f"IC 快筛  = {ic if ic is None else f'{ic:+.4f}'} (necessary)")
    print(f"年化收益 = {_pct(m['annual_return'])}  (KPI>=+30%: {'PASS' if kpi['passes']['annual_return'] else 'FAIL'})")
    print(f"最大回撤 = {_pct(m['max_drawdown'])}  (KPI>=-20%: {'PASS' if kpi['passes']['max_drawdown'] else 'FAIL'})")
    print(f"Sharpe   = {m['sharpe']:.2f}  段胜率={_pct(m['win_rate']) if m['win_rate'] else 'None'} 盈亏比={m['payoff_ratio']} 期望={m['expectancy']}")
    print(f"末NAV    = {res['final_nav']:.3f} (成本拖累 {res['cost_drag']:.1%}, 均换手 {res['avg_turnover']:.2f}, 容量超阈率 {res['capacity_warn_rate']:.1%})")
    print(f"R1 可交易= {trad['verdict']}  VERDICT={verdict}")

    out = {"experiment": signal_name, "engine": "portfolio_execbacktest_20260615",
           "ic_quick_screen": ic, "anomaly": av,
           "metrics": {**m, "final_nav": res["final_nav"], "cost_drag": res["cost_drag"],
                       "avg_turnover": res["avg_turnover"], "capacity_warn_rate": res["capacity_warn_rate"]},
           "tradability": trad, "kpi_verdict": kpi, "verdict": verdict, "n_rebalances": res["n_rebalances"],
           "rebalance_days": rebalance_days, "top_k": top_k, "sizing": sizing,
           "note": "Phase D 慢衰减绝对源; IC necessary 快筛, 裁决=含成本 execution-aware 绝对收益 (R1/C-WinReturn)"}
    if extra:
        out.update(extra)
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[out] {out_path}")

    with open_store() as st:
        if gate is not None:
            record_pit_check(st, run_id=run_id, step="leakage_gate", check_name=f"{signal_name}_pit_behavioral",
                             passed=gate.get("clean", False), detail=gate)
        if ic is not None:
            record_ic_cell(st, run_id=run_id, data_snapshot=snapshot, consumer_id=consumer_id,
                           metric="oos_rank_ic", value=ic, n_windows=ic_res.get("n_days"))
        record_verdict(st, run_id=run_id, family=family, verdict=verdict,
                       judges={"ic_quick_screen": ic, "metrics": out["metrics"], "tradability": trad, "kpi_verdict": kpi},
                       confirmed_by_owner=0)
        record_artifact(st, run_id=run_id, artifact_path=out_path)
    print(f"[store] 留档 {signal_name} verdict={verdict} R1={trad['verdict']} (run_id={run_id})")
    return out
