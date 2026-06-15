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
from services.portfolio_walk_forward.oos_ic import forward_returns  # noqa: E402
from services.experiment_harness import leakage_gate  # noqa: E402
from services.phaseD_signal_eval import evaluate_signal  # noqa: E402  共享评估 harness (IC快筛/含成本backtest/裁决/留档)

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

    # 评估委托共享 harness (IC necessary 快筛 → execution-aware 含成本 backtest → R1/C-WinReturn 裁决 → 留档)
    all_dates = sorted({d for bb in bars_by_code.values() for d in bb})
    evaluate_signal(
        signal_by_code=signal, bars_by_code=bars_by_code, calendar=all_dates, fwd_by_code=fwd_src,
        signal_name="moneyflow_trend_alpha", run_id="phaseD_moneyflow_trend_alpha_20260615",
        family="phaseD_slowdecay_absolute", snapshot=f"moneyflow_trend@{args.start}",
        out_path=REPO / "analysis" / "phaseD_moneyflow_trend_alpha_20260615.json",
        consumer_id="moneyflow|net_inflow_trend", ic_baseline=BASELINE_IC,
        rebalance_days=REBALANCE_DAYS, top_k=TOP_K, sizing=args.sizing, embargo=EMBARGO, gate=gate,
        extra={"signal": f"mf_net_inflow_trend_w{TREND_WINDOW}_top{TOP_K}_monthly_{args.sizing}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
