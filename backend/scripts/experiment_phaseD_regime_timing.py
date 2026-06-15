"""Phase D 第四轴 Regime/Timing: 市场趋势择时门削 max_dd (R1 核心: long-only 钱来自'在对的时候在场')。

owner=docs/strategy_validation_contract.md 判断法典 C-WinReturn (Regime/Timing 一等轴) + design_deficiencies_extension2 §3.1 (N6)。
背景: mf_trend 单信号 TRADABLE 但 max_dd -31% 超 KPI(-20%); 收益是正的, binding 约束是回撤。第四轴绝对方向门:
  市场代理(全宇宙等权)处下跌趋势 -> risk-off 持现金 -> 削 max_dd。这是 R1 被减掉的 cohort 绝对漂移层的直接利用。

预注册 (跑前冻结): regime 信号 = 全宇宙等权日收益累计 NAV; regime_ok[t] = market_nav[t] >= MA_N(market_nav)[t]
  (在 N 日均线上=上行=risk-on, 否则 risk-off 持现金)。PIT: 只用 <=t 价格。N=REGIME_MA。择时门叠加到 mf_trend top-K。
  判据: 有 regime 门 vs 无门 对比, max_dd 显著降 (削回撤) 且年化不崩 -> 第四轴有效。含成本 execution-aware (R1)。
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from scripts.experiment_l0_baseline import load_kline  # noqa: E402
from scripts.experiment_moneyflow_trend_alpha import (  # noqa: E402
    load_moneyflow, mf_trend_feature, in_universe, TREND_WINDOW, BASELINE_IC,
)
from services.portfolio_walk_forward.oos_ic import forward_returns  # noqa: E402
from services.experiment_harness import leakage_gate  # noqa: E402
from services.phaseD_signal_eval import evaluate_signal  # noqa: E402

REBALANCE_DAYS = 20  # rule-compliance: ok evidence=pre-reg 月度调仓 (与 mf_trend 同口径)
TOP_K = 20           # rule-compliance: ok evidence=pre-reg 固定选股数
HORIZON = 5          # rule-compliance: ok evidence=IC 快筛 forward 窗
EMBARGO = 5
REGIME_MA = 60       # rule-compliance: ok evidence=pre-reg 季度趋势均线 (市场 regime 慢档, V0)


def build_regime_ok(bars_by_code: dict, calendar: list, ma: int = REGIME_MA) -> dict[str, bool]:
    """全宇宙等权市场代理趋势 regime 门 (PIT): market_nav[t] >= MA_N(market_nav)[t] -> risk-on。"""
    # 每日全宇宙等权日收益
    by_date_rets: dict[str, list] = defaultdict(list)
    for c, bb in bars_by_code.items():
        ds = sorted(bb)
        for i in range(1, len(ds)):
            p0, p1 = bb[ds[i - 1]][3], bb[ds[i]][3]
            if p0 not in (None, 0) and p1 not in (None, 0):
                by_date_rets[ds[i]].append(p1 / p0 - 1.0)
    nav, navs = 1.0, {}
    for d in calendar:
        if by_date_rets.get(d):
            nav *= (1.0 + float(np.mean(by_date_rets[d])))
        navs[d] = nav
    # regime_ok[t] = nav[t] >= 过去 ma 日均值 (PIT: 只用 <=t)
    nav_series = [navs[d] for d in calendar]
    regime: dict[str, bool] = {}
    for i, d in enumerate(calendar):
        if i < ma:
            regime[d] = True  # warmup 默认在场
        else:
            regime[d] = nav_series[i] >= float(np.mean(nav_series[i - ma + 1:i + 1]))
    return regime


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2023-01-01")  # rule-compliance: ok evidence=对齐 L0/KPI 窗口
    ap.add_argument("--sizing", choices=["equal", "rank", "inverse_vol"], default="equal")  # rule-compliance: ok evidence=仓位 policy
    args = ap.parse_args(argv)

    print("[load] K线(OHLCV) + moneyflow ...", flush=True)
    by_code = load_kline(args.start, None, 0)
    mf = load_moneyflow(args.start)
    print(f"[load] K线 {len(by_code)} 股, moneyflow {len(mf)} 股", flush=True)

    bars_by_code: dict[str, dict] = {}
    signal: dict[str, dict] = {}
    fwd_src: dict[str, dict] = {}
    for code, bars in by_code.items():
        if not in_universe(code) or code not in mf:
            continue
        dates = bars["date"]
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
        fwd_src[code] = {d: fwd[i] for i, d in enumerate(dates) if fwd[i] is not None}

    # 事前 leakage 门 (mf_trend)
    sample = [c for c in list(bars_by_code)[:30] if len(signal.get(c, {})) >= 40]
    probe = [{"_net": [mf[c].get(d, (None, None))[0] for d in by_code[c]["date"]],
              "_flow": [mf[c].get(d, (None, None))[1] for d in by_code[c]["date"]],
              "close": by_code[c]["close"]} for c in sample]
    gate = leakage_gate(lambda b: mf_trend_feature(b["_net"], b["_flow"]), probe)
    if not gate["clean"]:
        print(f"[BLOCK] mf_trend 事前门 FAIL"); return 1
    print(f"[leakage] 事前门 PASS", flush=True)

    all_dates = sorted({d for bb in bars_by_code.values() for d in bb})
    regime_ok = build_regime_ok(bars_by_code, all_dates, ma=REGIME_MA)
    on = sum(1 for d in all_dates if regime_ok[d])
    print(f"[regime] 市场代理 MA{REGIME_MA} 趋势门: {on}/{len(all_dates)} 日 risk-on ({on/len(all_dates):.0%})", flush=True)

    # A) baseline 无择时 (对照, 已知 +2.53%/-31%)
    print("\n##### A) mf_trend 无 regime 门 (baseline) #####")
    base = evaluate_signal(
        signal_by_code=signal, bars_by_code=bars_by_code, calendar=all_dates, fwd_by_code=fwd_src,
        signal_name="mf_trend_no_regime", run_id="phaseD_regime_baseline_20260615",
        family="phaseD_regime_timing", snapshot=f"mf_trend@{args.start}",
        out_path=REPO / "analysis" / "phaseD_regime_baseline_20260615.json",
        consumer_id="moneyflow|net_inflow_trend", ic_baseline=BASELINE_IC,
        rebalance_days=REBALANCE_DAYS, top_k=TOP_K, sizing=args.sizing, embargo=EMBARGO, gate=gate)

    # B) +第四轴 regime/timing 门
    print("\n##### B) mf_trend + Regime/Timing 第四轴择时门 #####")
    reg = evaluate_signal(
        signal_by_code=signal, bars_by_code=bars_by_code, calendar=all_dates, fwd_by_code=fwd_src,
        signal_name="mf_trend_regime_timing", run_id="phaseD_regime_timing_20260615",
        family="phaseD_regime_timing", snapshot=f"mf_trend_regimeMA{REGIME_MA}@{args.start}",
        out_path=REPO / "analysis" / "phaseD_regime_timing_20260615.json",
        consumer_id="moneyflow|net_inflow_trend+regimeMA60", ic_baseline=BASELINE_IC,
        rebalance_days=REBALANCE_DAYS, top_k=TOP_K, sizing=args.sizing, embargo=EMBARGO, gate=gate,
        regime_ok=regime_ok, extra={"regime_ma": REGIME_MA, "regime_on_pct": round(on / len(all_dates), 4)})

    # 对比裁决
    bm, rm = base["metrics"], reg["metrics"]
    print(f"\n===== 第四轴对比 (mf_trend, top{TOP_K}, 月度) =====")
    print(f"{'指标':<10}{'无门(A)':>14}{'+regime门(B)':>16}")
    print(f"{'年化':<10}{bm['annual_return']:>13.2%}{rm['annual_return']:>15.2%}")
    print(f"{'max_dd':<10}{bm['max_drawdown']:>13.2%}{rm['max_drawdown']:>15.2%}")
    print(f"{'Sharpe':<10}{bm['sharpe']:>13.2f}{rm['sharpe']:>15.2f}")
    print(f"{'Calmar':<10}{bm['calmar']:>13.2f}{rm['calmar']:>15.2f}")
    dd_cut = (rm['max_drawdown'] - bm['max_drawdown'])
    print(f"\n第四轴裁决: max_dd {bm['max_drawdown']:+.1%} -> {rm['max_drawdown']:+.1%} ({'削' if dd_cut>0 else '恶化'} {abs(dd_cut):.1%}); "
          f"年化 {bm['annual_return']:+.2%} -> {rm['annual_return']:+.2%}")
    print(f"  -> {'第四轴有效 (回撤削且收益不崩), 值得 Optuna 搜 regime 参' if dd_cut>0 and rm['annual_return']>=bm['annual_return']*0.5 else '第四轴此配置未显著改善, 需调 regime 信号/阈值'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
