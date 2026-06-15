"""并行任务 (moneyflow 回补持 tushare_raw 锁期): K线价格动量 + Regime/Timing, 2019+ 多 regime 含成本验证。

只读 market 库 (price_kline_qfq_tushare 2019+), 不碰 tushare_raw 源 -> 回补持锁期可并行。
owner=docs/strategy_validation_contract.md 判断法典 + p3_execution_aware_verdict。
目的: K线已切 tushare 2019+ (含 2020 COVID/21牛/22杀/23-24熊/25修复 多 regime)。测 R1 对齐的**趋势信号**
  (动量, 正绝对漂移方向, 对比已证不可交易的 reversal/quality) 跨 regime 是否可交易 + regime 门是否削熊市。
  用 trailing 窗口看是否"曾经有效近期失效"或"跨 regime 稳健"。

预注册 (跑前冻结): 动量 = close[t]/close[t-N]-1 (N日价格动量, 高=趋势赢家, PIT 只用<=t); top-K 等权;
  月度调仓; + 市场代理 regime 门 (MA60 趋势, 在场/空仓); T+1 open 含成本 execution-aware。
  判据 = 含成本绝对收益 (R1) + trailing 多窗趋势; 对比 P3 reversal 全市场 -14% (2023+ 单regime)。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from scripts.experiment_l0_baseline import load_kline  # noqa: E402  只读 market 库
from scripts.experiment_phaseD_regime_timing import build_regime_ok  # noqa: E402  纯K线市场代理 regime
from services.portfolio_walk_forward.oos_ic import forward_returns  # noqa: E402
from services.phaseD_signal_eval import evaluate_signal  # noqa: E402

MOM_WINDOW = 60      # rule-compliance: ok evidence=pre-reg 季度价格动量窗 (趋势, 慢档)
REBALANCE_DAYS = 20  # rule-compliance: ok evidence=pre-reg 月度调仓
TOP_K = 20           # rule-compliance: ok evidence=pre-reg 固定选股数
HORIZON = 5          # rule-compliance: ok evidence=IC 快筛 forward 窗
EMBARGO = 5
REGIME_MA = 60       # rule-compliance: ok evidence=pre-reg 市场代理趋势均线 (与 regime_timing 同)
BOARD_PREFIXES = ("60", "00", "30", "68")  # rule-compliance: ok evidence=universe 主板/创业/科创
BASELINE_IC = 0.064  # rule-compliance: ok evidence=L0 标尺对照


def in_universe(code: str) -> bool:
    return code[:2] in BOARD_PREFIXES


def momentum_feature(closes: list, window: int = MOM_WINDOW) -> list:
    """N日价格动量 close[t]/close[t-N]-1 (PIT: feat[i] 只用 <=i)。warmup 不足 -> None。"""
    out: list = [None] * len(closes)
    for i in range(len(closes)):
        j = i - window
        if j < 0 or closes[i] in (None, 0) or closes[j] in (None, 0):
            continue
        out[i] = closes[i] / closes[j] - 1.0
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2019-01-01")  # rule-compliance: ok evidence=K线 tushare-qfq 起点 (全多regime窗)
    ap.add_argument("--sizing", choices=["equal", "rank", "inverse_vol"], default="equal")  # rule-compliance: ok evidence=仓位 policy
    ap.add_argument("--regime", action="store_true", help="加 regime/timing 门")  # rule-compliance: ok evidence=对比有/无门
    args = ap.parse_args(argv)

    print(f"[load] K线(OHLCV) tushare-qfq 2019+ (只读 market, 不碰 tushare_raw) ...", flush=True)
    by_code = load_kline(args.start, None, 0)
    print(f"[load] {len(by_code)} 股", flush=True)

    bars_by_code: dict[str, dict] = {}
    signal: dict[str, dict] = {}
    fwd_src: dict[str, dict] = {}
    for code, bars in by_code.items():
        if not in_universe(code):
            continue
        dates, closes = bars["date"], bars["close"]
        mom = momentum_feature(closes)
        fwd = forward_returns(dates, closes, HORIZON)
        bb, sig = {}, {}
        for i, d in enumerate(dates):
            c = closes[i]
            if c is not None:
                bb[d] = (bars["open"][i], bars["high"][i], bars["low"][i], c, bars["volume"][i])
            if mom[i] is not None:
                sig[d] = mom[i]
        bars_by_code[code] = bb
        signal[code] = sig
        fwd_src[code] = {d: fwd[i] for i, d in enumerate(dates) if fwd[i] is not None}

    all_dates = sorted({d for bb in bars_by_code.values() for d in bb})
    print(f"[window] {all_dates[0]} ~ {all_dates[-1]} ({len(all_dates)} 交易日, 多 regime)", flush=True)
    regime_ok = None
    tag = "no_regime"
    if args.regime:
        regime_ok = build_regime_ok(bars_by_code, all_dates, ma=REGIME_MA)
        on = sum(1 for d in all_dates if regime_ok[d])
        print(f"[regime] 市场代理 MA{REGIME_MA}: {on}/{len(all_dates)} 日 risk-on ({on/len(all_dates):.0%})", flush=True)
        tag = "regime"

    evaluate_signal(
        signal_by_code=signal, bars_by_code=bars_by_code, calendar=all_dates, fwd_by_code=fwd_src,
        signal_name=f"kline_momentum_w{MOM_WINDOW}_{tag}", run_id=f"phaseD_kline_momentum_{tag}_20260615",
        family="phaseD_kline_multiregime", snapshot=f"momentum_w{MOM_WINDOW}@{args.start}",
        out_path=REPO / "analysis" / f"phaseD_kline_momentum_{tag}_20260615.json",
        consumer_id=f"kline|momentum_w{MOM_WINDOW}_{tag}", ic_baseline=BASELINE_IC,
        rebalance_days=REBALANCE_DAYS, top_k=TOP_K, sizing=args.sizing, embargo=EMBARGO,
        regime_ok=regime_ok, extra={"signal": f"momentum_w{MOM_WINDOW}_top{TOP_K}_monthly_{tag}", "window": f"{all_dates[0]}~{all_dates[-1]}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
