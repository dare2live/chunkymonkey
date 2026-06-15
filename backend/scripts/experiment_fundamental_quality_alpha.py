"""Phase D base 信号: 财务质量 (扣非ROE) — canonical 慢衰减绝对源, 严谨 ann_date as-of PIT。

owner=docs/strategy_validation_contract.md 判断法典 + p3_execution_aware_verdict §4 (Phase D)。用户选: 财务质量作更强 base。
质量溢价 = 教科书 long-only 绝对 alpha, 季度更新=最慢衰减→极低换手→成本可活 (R2)。

**leakage 红线 (mythos §3a/§8, 真金白银)**: 财报 PIT 锚 = **ann_date (披露日) 不是 end_date (报告期末)**。
  Q2(end_date 0630) ~8月才披露; 决策日 t 只能用 ann_date<=t 的报告。用 end_date = 漏未来已披露=泄漏死。
  as-of 规则: 决策日 t 的质量 = {所有 ann_date<=t 的报告} 中 max(end_date) 的值 (最新已披露财季);
  同 end_date 多 ann_date (修订 38组) 取最新 ann_date<=t 的值。该日前无任何披露 -> None (标 unknown 不取未来)。

预注册 (跑前冻结): 信号=as-of 扣非ROE (roe_dt, 高=质量好=正绝对漂移); top-K 等权; 月度调仓 (信号季度更新故换手天然低);
  T+1 open 含成本。判据=含成本 execution-aware 绝对收益 (R1)。事前 leakage 门 + 显式 ann_date<=决策日 抽查。
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
from services.phaseD_signal_eval import evaluate_signal  # noqa: E402

REBALANCE_DAYS = 20  # rule-compliance: ok evidence=pre-reg 月度调仓 (财务季度更新, 换手天然低)
TOP_K = 20           # rule-compliance: ok evidence=pre-reg 固定选股数
HORIZON = 5          # rule-compliance: ok evidence=IC 快筛 forward 窗
EMBARGO = 5
METRIC = "roe_dt"    # rule-compliance: ok evidence=pre-reg 扣非ROE (剔非经常损益, 质量更干净)
BOARD_PREFIXES = ("60", "00", "30", "68")  # rule-compliance: ok evidence=universe 主板/创业/科创
BASELINE_IC = 0.064  # rule-compliance: ok evidence=L0 标尺 (necessary 快筛对照)


def in_universe(code: str) -> bool:
    return code[:2] in BOARD_PREFIXES


def load_quality_reports(metric: str = METRIC) -> dict[str, list]:
    """{code6: [(ann_date_iso, end_date, value)]} 按 ann_date 升序 (含 start 前的历史报告, as-of 需要)。"""
    conn = duck_connect("data/tushare_raw.duckdb", read_only=True)  # rule-compliance: ok evidence=read-only fina_indicator via central adapter
    try:
        rows = conn.execute(
            f"SELECT ts_code, ann_date, end_date, {metric} FROM raw_tushare_fina_indicator "
            f"WHERE {metric} IS NOT NULL AND ann_date IS NOT NULL AND end_date IS NOT NULL "
            "ORDER BY ts_code, ann_date").fetchall()
    finally:
        conn.close()
    out: dict[str, list] = defaultdict(list)
    for ts, ann, end, val in rows:
        a = f"{ann[:4]}-{ann[4:6]}-{ann[6:8]}"
        out[ts.split(".")[0]].append((a, end, float(val)))
    return dict(out)


def asof_quality_series(dates: list[str], reports: list) -> list:
    """as-of 扣非ROE (PIT: 决策日 d 只用 ann_date<=d 的报告, 取已披露 max(end_date) 的最新修订值)。

    reports = [(ann_date, end_date, value)] 已按 ann_date 升序。返回对齐 dates 的 list (无披露->None)。
    """
    out: list = [None] * len(dates)
    known: dict[str, tuple] = {}   # end_date -> (ann_date, value); 同 end_date 后到的 ann_date 覆盖 (修订)
    ri = 0
    n = len(reports)
    for i, d in enumerate(dates):
        while ri < n and reports[ri][0] <= d:   # ann_date <= 决策日 d 才纳入 (PIT 核心)
            a, end, val = reports[ri]
            known[end] = (a, val)
            ri += 1
        if known:
            latest_end = max(known)              # 已披露的最新财季
            out[i] = known[latest_end][1]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2023-01-01")  # rule-compliance: ok evidence=对齐 L0/KPI 窗口
    ap.add_argument("--sizing", choices=["equal", "rank", "inverse_vol"], default="equal")  # rule-compliance: ok evidence=仓位 policy
    args = ap.parse_args(argv)

    print("[load] K线(OHLCV) + fina_indicator (扣非ROE, 含历史报告) ...", flush=True)
    by_code = load_kline(args.start, None, 0)
    reports = load_quality_reports(METRIC)
    print(f"[load] K线 {len(by_code)} 股, 财务报告 {len(reports)} 股", flush=True)

    bars_by_code: dict[str, dict] = {}
    signal: dict[str, dict] = {}
    fwd_src: dict[str, dict] = {}
    pit_violation = 0
    pit_checked = 0
    for code, bars in by_code.items():
        if not in_universe(code) or code not in reports:
            continue
        dates = bars["date"]
        q = asof_quality_series(dates, reports[code])
        fwd = forward_returns(dates, bars["close"], HORIZON)
        bb, sig = {}, {}
        for i, d in enumerate(dates):
            c = bars["close"][i]
            if c is not None:
                bb[d] = (bars["open"][i], bars["high"][i], bars["low"][i], c, bars["volume"][i])
            if q[i] is not None:
                sig[d] = q[i]
        bars_by_code[code] = bb
        signal[code] = sig
        fwd_src[code] = {d: fwd[i] for i, d in enumerate(dates) if fwd[i] is not None}

    # 显式 ann_date<=决策日 PIT 抽查 (真金白银: 财报 PIT 锚=ann_date, 漏未来=泄漏死)
    for code in list(signal)[:50]:
        reps = reports[code]
        for d in list(signal[code])[:10]:
            pit_checked += 1
            # 该日信号值必来自 ann_date<=d 的报告
            disclosed = [r for r in reps if r[0] <= d]
            if not disclosed:
                pit_violation += 1
                continue
            latest_end = max(r[1] for r in disclosed)
            expect = [r[2] for r in disclosed if r[1] == latest_end][-1]
            if abs(signal[code][d] - expect) > 1e-9:
                pit_violation += 1
    print(f"[PIT] ann_date<=决策日 抽查: {pit_checked} 样本, 违规 {pit_violation}", flush=True)
    if pit_violation > 0:
        print(f"[BLOCK] 财报 PIT 抽查发现 {pit_violation} 违规 (ann_date 泄漏), 拒绝跑"); return 1

    all_dates = sorted({d for bb in bars_by_code.values() for d in bb})
    evaluate_signal(
        signal_by_code=signal, bars_by_code=bars_by_code, calendar=all_dates, fwd_by_code=fwd_src,
        signal_name="fundamental_quality_roe_dt", run_id="phaseD_fundamental_quality_20260615",
        family="phaseD_slowdecay_absolute", snapshot=f"fina_indicator_{METRIC}_asof@{args.start}",
        out_path=REPO / "analysis" / "phaseD_fundamental_quality_20260615.json",
        consumer_id=f"fina_indicator|{METRIC}_asof", ic_baseline=BASELINE_IC,
        rebalance_days=REBALANCE_DAYS, top_k=TOP_K, sizing=args.sizing, embargo=EMBARGO,
        gate={"clean": True, "verdict": "PIT_ANN_DATE_ASOF", "n_checked": pit_checked, "violation": pit_violation},
        extra={"signal": f"{METRIC}_asof_top{TOP_K}_monthly_{args.sizing}", "pit_anchor": "ann_date"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
