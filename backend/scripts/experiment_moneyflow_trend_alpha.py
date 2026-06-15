"""Phase D 第一刀: 资金流大单净流入趋势 (慢衰减绝对源) — 验方法论 + 第一个 Phase D 信号裁决。

owner=analysis/p3_execution_aware_verdict_20260615.md §4 (Phase D 方向) + docs/strategy_validation_contract.md 判断法典。
缘起: P3 实弹定论裸 K 线 reversal long-only A 股结构性不可交易 (R1+R2)。Phase D 转**慢衰减+绝对预测**源。
第一刀选 moneyflow 大单净流入趋势 (R1 对齐: 选主力吸筹/正绝对漂移 cohort, 非超卖下跌刀; PIT 干净 t-1; 慢衰减 monthly 低换手 R2 可活):

信号 (PIT): mf_trend[t] = trailing-N 日 sum(net_mf_amount) / sum(total_flow)  (净流入占总流占比, [-1,1] 截面可比)。
  net_mf_amount = tushare 主力净流入 (大单+特大单); total_flow = 全单买卖额之和 (万元同单位)。只用 <=t, 执行 T+1 open。

预注册 (跑前冻结, 防挪门柱):
  策略: 每 REBALANCE_DAYS 在全市场按 mf_trend 降序选 top-K 等权 (高=持续吸筹); T+1 open 执行; 含成本 (execution-aware)。
  宇宙: 板块前缀 60/00/30/68。窗口 2023+。
  验收 (法典): IC = necessary 快筛 (anomaly_verdict §4.2 + 报 IC); **裁决 = 含成本 execution-aware 绝对收益**
    (tradability_verdict R1: IC>0 且 net<=0 -> IC_POSITIVE_BUT_UNTRADABLE; kpi_verdict C-WinReturn 联合门)。
  判据: 含成本年化>0 且 kpi 联合门 -> 慢衰减绝对源方法论成立 (第一个可交易 Phase D 信号); 否则诚实记录阴性 + 换方向。
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
from services.duck_adapter import connect as duck_connect  # noqa: E402
from services.portfolio_walk_forward.oos_ic import PanelRow, forward_returns, oos_rank_ic  # noqa: E402
from services.portfolio_execbacktest import run_execution_backtest, ExecConfig  # noqa: E402
from services.experiment_store import open_store, record_ic_cell, record_verdict, record_pit_check, record_artifact  # noqa: E402
from services.experiment_harness import leakage_gate, anomaly_verdict, tradability_verdict, kpi_verdict  # noqa: E402

TREND_WINDOW = 20    # rule-compliance: ok evidence=pre-reg 月度趋势窗 (慢衰减; 对比 reversal 5日)
REBALANCE_DAYS = 20  # rule-compliance: ok evidence=pre-reg 月度调仓 (慢衰减->低换手, R2 成本可活)
TOP_K = 20           # rule-compliance: ok evidence=pre-reg 固定选股数 (不优化防过拟合)
HORIZON = 5          # rule-compliance: ok evidence=IC 快筛 forward 窗 (与 L0 同口径快筛)
EMBARGO = 5
BOARD_PREFIXES = ("60", "00", "30", "68")  # rule-compliance: ok evidence=universe 主板/创业/科创
BASELINE_IC = 0.064  # rule-compliance: ok evidence=L0 reversal 标尺 (necessary 快筛对照, 非 gate)


def in_universe(code: str) -> bool:
    return code[:2] in BOARD_PREFIXES


def mf_trend_feature(net_series: list, flow_series: list, window: int = TREND_WINDOW) -> list:
    """trailing-N 净流入占总流比 (PIT: feat[i] 只用 <=i)。warmup 不足 -> None。"""
    out: list = [None] * len(net_series)
    for i in range(len(net_series)):
        lo = i - window + 1
        if lo < 0:
            continue
        net_sum = sum(n for n in net_series[lo:i + 1] if n is not None)
        flow_sum = sum(f for f in flow_series[lo:i + 1] if f is not None)
        out[i] = (net_sum / flow_sum) if flow_sum and flow_sum > 0 else None
    return out


def load_moneyflow(start: str) -> dict[str, dict[str, tuple[float, float]]]:
    """{code6: {YYYY-MM-DD: (net_mf_amount, total_flow)}} (PIT: 盘后 t-1, 决策 <=t)。total_flow=全单买卖额和。"""
    sd = start.replace("-", "")
    conn = duck_connect("data/tushare_raw.duckdb", read_only=True)  # rule-compliance: ok evidence=read-only moneyflow via central adapter
    try:
        rows = conn.execute(
            "SELECT ts_code, trade_date, net_mf_amount, "
            "(buy_sm_amount+buy_md_amount+buy_lg_amount+buy_elg_amount"
            "+sell_sm_amount+sell_md_amount+sell_lg_amount+sell_elg_amount) AS total_flow "
            "FROM raw_tushare_moneyflow WHERE trade_date >= ? AND net_mf_amount IS NOT NULL", [sd]).fetchall()
    finally:
        conn.close()
    out: dict[str, dict] = defaultdict(dict)
    for ts, td, net, flow in rows:
        code = ts.split(".")[0]
        d = f"{td[:4]}-{td[4:6]}-{td[6:8]}"
        out[code][d] = (float(net), float(flow) if flow is not None else 0.0)
    return dict(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2023-01-01")  # rule-compliance: ok evidence=对齐 L0/KPI 窗口
    ap.add_argument("--sizing", choices=["equal", "rank", "inverse_vol"], default="equal")  # rule-compliance: ok evidence=仓位 policy (C-WinReturn)
    args = ap.parse_args(argv)

    print("[load] K线(OHLCV) + moneyflow ...", flush=True)
    by_code = load_kline(args.start, None, 0)
    mf = load_moneyflow(args.start)
    print(f"[load] K线 {len(by_code)} 股, moneyflow {len(mf)} 股", flush=True)

    # 每股 mf_trend 信号 (PIT, 按 K线 date 对齐) + bars_by_code (引擎) + fwd (IC 快筛)
    bars_by_code: dict[str, dict] = {}
    signal: dict[str, dict] = {}
    feat_panel_src: dict[str, dict] = {}
    fwd_src: dict[str, dict] = {}
    for code, bars in by_code.items():
        if not in_universe(code) or code not in mf:
            continue
        dates = bars["date"]
        # 对齐 moneyflow 到 K线日 (缺则 None)
        net_s = [mf[code].get(d, (None, None))[0] for d in dates]
        flow_s = [mf[code].get(d, (None, None))[1] for d in dates]
        trend = mf_trend_feature(net_s, flow_s)
        fwd = forward_returns(dates, bars["close"], HORIZON)
        bb, sig = {}, {}
        for i, d in enumerate(dates):
            c = bars["close"][i]
            if c is not None:
                bb[d] = (bars["open"][i], bars["high"][i], bars["low"][i], c, bars["volume"][i])
            if trend[i] is not None:
                sig[d] = trend[i]
        bars_by_code[code] = bb
        signal[code] = sig
        feat_panel_src[code] = {d: trend[i] for i, d in enumerate(dates) if trend[i] is not None}
        fwd_src[code] = {d: fwd[i] for i, d in enumerate(dates) if fwd[i] is not None}

    # 事前 leakage 门 (mf_trend trailing 累计 PIT 行为门: 追加未来不改过去)
    sample = [c for c in list(bars_by_code)[:30] if len(signal.get(c, {})) >= 40]
    def _mf_feat(b):
        return mf_trend_feature(b["_net"], b["_flow"])
    probe_bars = []
    for c in sample:
        dates = by_code[c]["date"]
        probe_bars.append({"_net": [mf[c].get(d, (None, None))[0] for d in dates],
                           "_flow": [mf[c].get(d, (None, None))[1] for d in dates],
                           "close": by_code[c]["close"]})
    gate = leakage_gate(lambda b: mf_trend_feature(b["_net"], b["_flow"]), probe_bars)
    if not gate["clean"]:
        print(f"[BLOCK] mf_trend 事前 leakage 门 FAIL: {gate['sample_violations']}"); return 1
    print(f"[leakage] 事前门 PASS (mf_trend x {gate['n_stocks']} 股)", flush=True)

    # IC 快筛 (necessary, 非 gate): walk-forward OOS RankIC
    panel = [PanelRow(date=d, code=c, feature=feat_panel_src[c][d], fwd_ret=fwd_src[c][d])
             for c in feat_panel_src for d in feat_panel_src[c] if d in fwd_src.get(c, {})]
    ic_res = oos_rank_ic(panel, embargo_days=EMBARGO)
    ic = ic_res.get("oos_rank_ic")
    av = anomaly_verdict(ic, baseline=BASELINE_IC)
    print(f"[IC 快筛] mf_trend OOS RankIC = {ic if ic is None else f'{ic:+.4f}'} (necessary, 非 gate); anomaly={av['verdict']}", flush=True)

    # 调仓表: 全局交易日每 REBALANCE_DAYS 选 top-K (mf_trend 高=持续吸筹)
    all_dates = sorted({d for bb in bars_by_code.values() for d in bb})
    rebalances = []
    for gi in range(0, len(all_dates) - 1, REBALANCE_DAYS):
        t = all_dates[gi]
        cands = [(c, signal[c][t]) for c in signal if t in signal[c]]
        if not cands:
            continue
        cands.sort(key=lambda x: x[1], reverse=True)
        rebalances.append((t, cands[:TOP_K]))
    print(f"[backtest] execution-aware ({len(rebalances)} 月度调仓, T+1 open, sizing={args.sizing}, 含成本) ...", flush=True)

    res = run_execution_backtest(rebalances, bars_by_code, all_dates,
                                 config=ExecConfig.load(), sizing=args.sizing, top_k=TOP_K)
    if not res["nav"]:
        print("[ERR] 空 NAV"); return 1
    m = res["metrics"]

    # 法典裁决: R1 对称门 + C-WinReturn 联合门 (钱的裁决, 非 IC)
    trad = tradability_verdict(ic, m["annual_return"])
    kpi = kpi_verdict(m)
    verdict = kpi["verdict"]

    def pct(x):
        return f"{x:+.2%}" if isinstance(x, (int, float)) else "None"

    print(f"\n===== Phase D 第一刀: moneyflow 大单净流入趋势 (top{TOP_K}, T+1 open, 月度, sizing={args.sizing}) =====")
    print(f"IC 快筛   = {ic if ic is None else f'{ic:+.4f}'} (necessary, 非裁决)")
    print(f"年化收益  = {pct(m['annual_return'])}  (KPI>=+30%: {'PASS' if kpi['passes']['annual_return'] else 'FAIL'})")
    print(f"最大回撤  = {pct(m['max_drawdown'])}  (KPI>=-20%: {'PASS' if kpi['passes']['max_drawdown'] else 'FAIL'})")
    print(f"Sharpe    = {m['sharpe']:.2f}   Calmar = {m['calmar']:.2f}")
    print(f"月胜率    = {pct(m['monthly_win_rate']) if m['monthly_win_rate'] else 'None'} (诊断量) 段胜率={pct(m['win_rate']) if m['win_rate'] else 'None'} 盈亏比={m['payoff_ratio']} 期望={m['expectancy']}")
    print(f"末NAV     = {res['final_nav']:.3f}  (成本拖累 {res['cost_drag']:.1%}, 均换手 {res['avg_turnover']:.2f}, 容量超阈率 {res['capacity_warn_rate']:.1%})")
    print(f"R1 可交易 = {trad['verdict']}")
    print(f"VERDICT   = {verdict}  ({'慢衰减绝对源方法论成立=第一个可交易 Phase D 信号' if verdict=='KPI_PASS' else '阴性: 含成本不达标, 诚实记录换方向'})")

    out = {"experiment": "moneyflow_trend_alpha", "engine": "portfolio_execbacktest_20260615",
           "signal": f"mf_net_inflow_trend_w{TREND_WINDOW}_top{TOP_K}_monthly_{args.sizing}",
           "ic_quick_screen": ic, "anomaly": av,
           "metrics": {**m, "final_nav": res["final_nav"], "cost_drag": res["cost_drag"],
                       "avg_turnover": res["avg_turnover"], "capacity_warn_rate": res["capacity_warn_rate"]},
           "tradability": trad, "kpi_verdict": kpi, "verdict": verdict, "n_rebalances": res["n_rebalances"],
           "note": "Phase D 慢衰减绝对源第一刀; IC necessary 快筛, 裁决=含成本 execution-aware 绝对收益 (R1/C-WinReturn)"}
    out_path = REPO / "analysis" / "phaseD_moneyflow_trend_alpha_20260615.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[out] {out_path}")

    run_id = "phaseD_moneyflow_trend_alpha_20260615"
    with open_store() as st:
        record_pit_check(st, run_id=run_id, step="leakage_gate", check_name="mf_trend_pit_behavioral",
                         passed=gate["clean"], detail=gate)
        if ic is not None:
            record_ic_cell(st, run_id=run_id, data_snapshot=f"moneyflow_trend@{args.start}",
                           consumer_id="moneyflow|net_inflow_trend", metric="oos_rank_ic",
                           value=ic, n_windows=ic_res.get("n_days"))
        record_verdict(st, run_id=run_id, family="phaseD_slowdecay_absolute", verdict=verdict,
                       judges={"ic_quick_screen": ic, "metrics": out["metrics"], "tradability": trad, "kpi_verdict": kpi},
                       confirmed_by_owner=0)
        record_artifact(st, run_id=run_id, artifact_path=out_path)
    print(f"[store] 留档 Phase D verdict={verdict} R1={trad['verdict']} (run_id={run_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
