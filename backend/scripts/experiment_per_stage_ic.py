"""Phase B per-stage L0 IC 实验 — reversal 在各 Weinstein 阶段的 OOS RankIC (证条件化)。

owner=analysis/conditional_stage_strategy_design_20260614.md。用户核心想法: 不是万能公式,
先给股票形态分类, 看哪个公式适配哪个形态。本实验是最便宜的"条件化 edge 存不存在"探针。

预注册判据 (跑前冻结, 防挪门柱 — measured not estimated):
  H: reversal OOS RankIC 因 Weinstein 阶段而异。Stage1(底部/低位, 超卖反转域)高, Stage2(上升趋势,
     动量域)低/负; 市场级 +0.064 是把所有形态平均了的稀释值。
  PIT (死亡条款泄漏死): reversal feature[t]=-(close[t]/close[t-20]-1) 只用 <=t; stage[t] 已核 as-of
     (technical_stage.py 每行只用 closes/vol[:t+1], 本会话审过); label=forward 5d; embargo=5;
     walk-forward expanding_monthly (selector 只看 OOS test 行)。
  判据:
    CONFIRM 条件化 IF >=1 stage OOS RankIC > 2x 市场基线 (>+0.128) 且 n_days>=60 且经济意义合理
      (低位/底部域 IC > 上升域 for reversal) → 解锁形态维 + 出(公式x形态)矩阵。
    REJECT IF 所有 stage IC 落 [+0.03,+0.09] (≈市场级, 无阶段依赖) → reversal 非条件化, 形态维不解锁。
    INCONCLUSIVE IF 高 IC 的 stage n_days 不足 (<60) → 欠功效。
  Anomaly (异常红线 §4.2): 任一 stage |IC| > 0.30 → leakage 警报, 不直接采信, 走核查。
  对照: 同时算 all-stages (市场级) IC, 须复现 ~+0.064 (否则管线 drift, 实验作废)。

用法: PYTHONPATH=backend python backend/scripts/experiment_per_stage_ic.py [--start 2023-01-01]
数据: market.duckdb v_price_kline_qfq (closes) + smartmoney.duckdb fact_stock_technical_stage (stage)。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]  # backend/scripts/X.py -> repo root
sys.path.insert(0, str(REPO / "backend"))

from scripts.experiment_l0_baseline import load_kline  # noqa: E402  rule-compliance: ok evidence=reuse market-db qfq loader
from services.duck_adapter import connect as duck_connect  # noqa: E402
from services.formula_engine.features import feature_reversal  # noqa: E402
from services.portfolio_walk_forward.oos_ic import PanelRow, forward_returns, oos_rank_ic  # noqa: E402

LOOKBACK = 20        # reversal lookback (L0 标尺最佳)  # measured: l0_search_v1 reversal best
HORIZON = 5          # forward 收益天数  # from spec: l0 forward 期
EMBARGO = 5          # embargo>=horizon  # from spec §4.1
MARKET_BASELINE = 0.064   # measured: l0_baseline reversal 市场级 OOS RankIC
CONFIRM_MULT = 2.0        # CONFIRM 阈 = 2x 市场基线
MIN_DAYS = 60             # 功效下限 (OOS 日)
ANOMALY = 0.30            # leakage 红线
STAGE_NAMES = {"1": "底部/低位", "1.5": "突破中", "2": "上升趋势", "3": "顶部分布", "4": "下跌趋势"}


def load_stage_map(start: str) -> dict[tuple[str, str], str]:
    conn = duck_connect("data/smartmoney.duckdb", read_only=True)  # rule-compliance: ok evidence=read-only stage source via central duck_adapter
    try:
        rows = conn.execute(
            "SELECT stock_code, date, stage FROM fact_stock_technical_stage WHERE date >= ?", [start]
        ).fetchall()
    finally:
        conn.close()
    return {(c, d): s for c, d, s in rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2023-01-01")  # rule-compliance: ok evidence=data start aligns L0 baseline window
    args = ap.parse_args(argv)

    print(f"[load] K线 (market-db v_price_kline_qfq, 全宇宙, >= {args.start}) ...", flush=True)
    by_code = load_kline(args.start, None, 0)
    print(f"[load] {len(by_code)} 股 K线", flush=True)
    stage_map = load_stage_map(args.start)
    print(f"[load] stage map {len(stage_map):,} (code,date) 行", flush=True)

    panels: dict[str, list[PanelRow]] = defaultdict(list)
    all_panel: list[PanelRow] = []
    n_no_stage = 0
    for code, bars in by_code.items():
        closes = bars["close"]
        dates = bars["date"]
        if len(closes) < HORIZON + LOOKBACK + 2:
            continue
        feat = feature_reversal(closes, lookback=LOOKBACK)
        fwd = forward_returns(dates, closes, HORIZON)
        for i, date in enumerate(dates):
            if feat[i] is None or fwd[i] is None:
                continue
            row = PanelRow(date=date, code=code, feature=feat[i], fwd_ret=fwd[i])
            all_panel.append(row)
            st = stage_map.get((code, date))
            if st is None:
                n_no_stage += 1
            else:
                panels[st].append(row)
    print(f"[panel] all={len(all_panel):,} 行; 无 stage={n_no_stage:,}; per-stage={[(s, len(panels[s])) for s in sorted(panels)]}", flush=True)

    print("[ic] computing walk-forward OOS RankIC (ALL + 5 stage) ...", flush=True)
    results: dict[str, dict] = {"ALL": oos_rank_ic(all_panel, embargo_days=EMBARGO)}
    for st in ["1", "1.5", "2", "3", "4"]:
        results[st] = oos_rank_ic(panels.get(st, []), embargo_days=EMBARGO)

    # 验收: 对照 ALL 复现 ~+0.064
    all_ic = results["ALL"].get("oos_rank_ic")
    pipeline_ok = all_ic is not None and abs(all_ic - MARKET_BASELINE) < 0.02
    # 阶段判据
    stage_ics = {s: results[s].get("oos_rank_ic") for s in ["1", "1.5", "2", "3", "4"]}
    confirm = []
    anomalies = []
    for s, ic in stage_ics.items():
        if ic is None:
            continue
        if abs(ic) > ANOMALY:
            anomalies.append((s, ic))
        if ic > MARKET_BASELINE * CONFIRM_MULT and results[s].get("n_days", 0) >= MIN_DAYS:
            confirm.append((s, ic))
    in_band = [ic for ic in stage_ics.values() if ic is not None and 0.03 <= ic <= 0.09]
    n_valid = sum(1 for ic in stage_ics.values() if ic is not None)
    # §4.2 相对红线: stage IC > 1.5x 基线 (+50% relative) → 必 ablation 验 PIT 才能信 (异常高=leakage 警报先怀疑)
    relative_redline = [(s, round(ic, 4), round(ic / MARKET_BASELINE, 2))
                        for s, ic in stage_ics.items() if ic is not None and ic > MARKET_BASELINE * 1.5]
    if anomalies:
        verdict = "ANOMALY_LEAKAGE_CHECK"
    elif confirm and relative_redline:
        verdict = "CONFIRM_CONDITIONAL_PENDING_ABLATION"  # 方向成立但触发相对红线, 须 ablation 才转正
    elif confirm:
        verdict = "CONFIRM_CONDITIONAL"
    elif n_valid > 0 and len(in_band) == n_valid:
        verdict = "REJECT_NOT_CONDITIONAL"
    else:
        verdict = "INCONCLUSIVE"

    print("\n===== per-stage L0 IC (reversal lookback=20, horizon=5, embargo=5) =====")
    print(f"{'stage':<8}{'形态':<12}{'OOS RankIC':>12}{'IC_IR':>9}{'n_windows':>11}{'n_days':>9}")
    print(f"{'ALL':<8}{'(市场级)':<12}{_fmt(all_ic):>12}{_fmt(results['ALL'].get('ic_ir')):>9}{results['ALL'].get('n_windows',0):>11}{results['ALL'].get('n_days',0):>9}")
    for s in ["1", "1.5", "2", "3", "4"]:
        r = results[s]
        print(f"{s:<8}{STAGE_NAMES[s]:<12}{_fmt(r.get('oos_rank_ic')):>12}{_fmt(r.get('ic_ir')):>9}{r.get('n_windows',0):>11}{r.get('n_days',0):>9}")
    print(f"\n对照(ALL≈+0.064 复现 pipeline OK): {pipeline_ok} (ALL={_fmt(all_ic)})")
    print(f"§4.2 相对红线 (stage IC > 1.5x 基线, 须 ablation): {relative_redline}")
    print(f"VERDICT: {verdict}  confirm={confirm}  anomalies={anomalies}")

    out = {
        "experiment": "per_stage_l0_ic",
        "params": {"lookback": LOOKBACK, "horizon": HORIZON, "embargo": EMBARGO, "start": args.start,
                   "market_baseline": MARKET_BASELINE, "confirm_mult": CONFIRM_MULT, "min_days": MIN_DAYS},
        "verdict": verdict, "pipeline_reproduces_baseline": pipeline_ok,
        "results": {k: {kk: vv for kk, vv in v.items() if kk != "per_window_ic"} for k, v in results.items()},
        "confirm": confirm, "anomalies": anomalies, "relative_redline": relative_redline,
    }
    out_path = REPO / "analysis" / "per_stage_l0_ic_result_20260615.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[out] {out_path}")
    return 0


def _fmt(x: float | None) -> str:
    return f"{x:+.4f}" if isinstance(x, (int, float)) else "None"


if __name__ == "__main__":
    sys.exit(main())
