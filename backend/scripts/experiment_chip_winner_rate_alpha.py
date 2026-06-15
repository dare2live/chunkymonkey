"""Phase D 信号: 筹码 winner_rate 趋势 (慢衰减绝对源) — 复用 phaseD_signal_eval harness。

owner=analysis/p3_execution_aware_verdict_20260615.md §4 (Phase D) + docs/strategy_validation_contract.md 判断法典。
预注册 (跑前冻结): winner_rate_trend[t] = winner_rate[t] - winner_rate[t-N] (N日获利盘比例变化, 上升=价格走强+筹码健康
  = 正绝对漂移假设, 与 mf_trend 结构平行/机制互补 资金vs筹码); 慢衰减 (筹码渐变), 月度调仓低换手。
  cyq_perf winner_rate 0-100 标度, PIT 盘后 t-1。判据 = 含成本 execution-aware 绝对收益 (R1, 非 IC)。
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from scripts.experiment_l0_baseline import load_kline  # noqa: E402
from services.duck_adapter import connect as duck_connect  # noqa: E402
from services.portfolio_walk_forward.oos_ic import forward_returns  # noqa: E402
from services.experiment_harness import leakage_gate  # noqa: E402
from services.phaseD_signal_eval import evaluate_signal  # noqa: E402

TREND_WINDOW = 20    # rule-compliance: ok evidence=pre-reg 月度筹码趋势窗 (慢衰减; 与 mf_trend 同口径)
REBALANCE_DAYS = 20  # rule-compliance: ok evidence=pre-reg 月度调仓 (慢衰减低换手 R2)
TOP_K = 20           # rule-compliance: ok evidence=pre-reg 固定选股数 (不优化防过拟合)
HORIZON = 5          # rule-compliance: ok evidence=IC 快筛 forward 窗 (同 L0 口径)
EMBARGO = 5
BOARD_PREFIXES = ("60", "00", "30", "68")  # rule-compliance: ok evidence=universe 主板/创业/科创
BASELINE_IC = 0.064  # rule-compliance: ok evidence=L0 reversal 标尺 (necessary 快筛对照)


def in_universe(code: str) -> bool:
    return code[:2] in BOARD_PREFIXES


def winner_trend_feature(wr_series: list, window: int = TREND_WINDOW) -> list:
    """winner_rate N日变化 (PIT: feat[i] 只用 <=i)。warmup 不足 -> None。"""
    out: list = [None] * len(wr_series)
    for i in range(len(wr_series)):
        j = i - window
        if j < 0 or wr_series[i] is None or wr_series[j] is None:
            continue
        out[i] = wr_series[i] - wr_series[j]
    return out


def load_winner_rate(start: str) -> dict[str, dict[str, float]]:
    """{code6: {YYYY-MM-DD: winner_rate}} (PIT 盘后 t-1)。"""
    sd = start.replace("-", "")
    conn = duck_connect("data/tushare_raw.duckdb", read_only=True)  # rule-compliance: ok evidence=read-only cyq_perf via central adapter
    try:
        rows = conn.execute(
            "SELECT ts_code, trade_date, winner_rate FROM raw_tushare_cyq_perf "
            "WHERE trade_date >= ? AND winner_rate IS NOT NULL", [sd]).fetchall()
    finally:
        conn.close()
    out: dict[str, dict] = defaultdict(dict)
    for ts, td, wr in rows:
        out[ts.split(".")[0]][f"{td[:4]}-{td[4:6]}-{td[6:8]}"] = float(wr)
    return dict(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2023-01-01")  # rule-compliance: ok evidence=对齐 L0/KPI 窗口
    ap.add_argument("--sizing", choices=["equal", "rank", "inverse_vol"], default="equal")  # rule-compliance: ok evidence=仓位 policy
    args = ap.parse_args(argv)

    print("[load] K线(OHLCV) + cyq_perf winner_rate ...", flush=True)
    by_code = load_kline(args.start, None, 0)
    wr = load_winner_rate(args.start)
    print(f"[load] K线 {len(by_code)} 股, winner_rate {len(wr)} 股", flush=True)

    bars_by_code: dict[str, dict] = {}
    signal: dict[str, dict] = {}
    fwd_src: dict[str, dict] = {}
    for code, bars in by_code.items():
        if not in_universe(code) or code not in wr:
            continue
        dates = bars["date"]
        wr_s = [wr[code].get(d) for d in dates]
        trend = winner_trend_feature(wr_s)
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

    # 事前 leakage 门 (winner_trend N日差分 PIT 行为门)
    sample = [c for c in list(bars_by_code)[:30] if len(signal.get(c, {})) >= 40]
    probe = [{"_wr": [wr[c].get(d) for d in by_code[c]["date"]], "close": by_code[c]["close"]} for c in sample]
    gate = leakage_gate(lambda b: winner_trend_feature(b["_wr"]), probe)
    if not gate["clean"]:
        print(f"[BLOCK] winner_trend 事前 leakage 门 FAIL: {gate['sample_violations']}"); return 1
    print(f"[leakage] 事前门 PASS (winner_trend x {gate['n_stocks']} 股)", flush=True)

    all_dates = sorted({d for bb in bars_by_code.values() for d in bb})
    evaluate_signal(
        signal_by_code=signal, bars_by_code=bars_by_code, calendar=all_dates, fwd_by_code=fwd_src,
        signal_name="chip_winner_rate_trend_alpha", run_id="phaseD_chip_winner_rate_trend_20260615",
        family="phaseD_slowdecay_absolute", snapshot=f"cyq_winner_rate_trend@{args.start}",
        out_path=REPO / "analysis" / "phaseD_chip_winner_rate_trend_20260615.json",
        consumer_id="cyq_perf|winner_rate_trend", ic_baseline=BASELINE_IC,
        rebalance_days=REBALANCE_DAYS, top_k=TOP_K, sizing=args.sizing, embargo=EMBARGO, gate=gate,
        extra={"signal": f"winner_rate_trend_w{TREND_WINDOW}_top{TOP_K}_monthly_{args.sizing}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
