"""Phase B 完整分层: Stage1.5 × 市值3档 × 换手3档 reversal OOS IC — 验"市值/换手 behave 不同"。

owner=analysis/segment_taxonomy_design_20260615.md (S2 规模 + S3 流动性逐层解锁)。用户 push back: 之前
Tier-2 只用单层 (Stage1.5), 没用市值/换手细分。本实验在确认的 Stage1.5 edge 上加市值×换手 9 子格, 找
是否有子格 reversal edge 远强 (更选择性 -> 更高 gross/笔, 或更慢衰减) -> 值得单独 backtest。

预注册 (跑前冻结): 市值/换手 = 每决策日 Stage1.5 内**截面分位**(只用该日数据=PIT); reversal feat[t] 只用<=t;
  label fwd5; embargo5; walk-forward OOS。判据: 子格 IC > Stage1.5 整体 +0.156 且 n_days>=60 -> 该子格更强。
  多重比较: 9 子格, 任何高格须 anomaly_verdict + 后续 ablation 才转正 (单看=selection bias)。
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
from services.duck_adapter import connect as duck_connect  # noqa: E402
from services.formula_engine.features import feature_reversal  # noqa: E402
from services.portfolio_walk_forward.oos_ic import PanelRow, forward_returns, oos_rank_ic  # noqa: E402
from services.experiment_store import open_store, record_ic_cells, record_pit_check, record_verdict, record_artifact  # noqa: E402
from services.experiment_harness import leakage_gate, anomaly_verdict  # noqa: E402

LOOKBACK = 20   # measured: l0 reversal best
HORIZON = 5
EMBARGO = 5
STAGE = "1.5"   # rule-compliance: ok evidence=Gate2 确认 edge regime
STAGE15_IC = 0.156  # rule-compliance: ok evidence=Stage1.5 整体基线 (per_stage_ic 实测)


def load_daily_basic(start: str) -> dict[tuple[str, str], tuple[float, float]]:
    """{(code6, YYYY-MM-DD): (circ_mv, turnover_rate)} (PIT: 决策日 t 用 t 的值)。"""
    sd = start.replace("-", "")
    conn = duck_connect("data/tushare_raw.duckdb", read_only=True)  # rule-compliance: ok evidence=read-only daily_basic via central adapter
    try:
        rows = conn.execute(
            "SELECT ts_code, trade_date, circ_mv, turnover_rate FROM raw_tushare_daily_basic "
            "WHERE trade_date >= ? AND circ_mv IS NOT NULL", [sd]).fetchall()
    finally:
        conn.close()
    out = {}
    for ts, td, mv, to in rows:
        code = ts.split(".")[0]
        d = f"{td[:4]}-{td[4:6]}-{td[6:8]}"
        out[(code, d)] = (float(mv), float(to))
    return out


def _tier(value: float, lo: float, hi: float) -> str:
    return "low" if value <= lo else ("high" if value > hi else "mid")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2023-01-01")  # rule-compliance: ok evidence=对齐 L0 窗口
    args = ap.parse_args(argv)

    print("[load] K线 + stage + daily_basic ...", flush=True)
    by_code = load_kline(args.start, None, 0)
    stage_map = load_stage_map(args.start)
    db = load_daily_basic(args.start)
    print(f"[load] {len(by_code)} 股, stage {len(stage_map):,}, daily_basic {len(db):,}", flush=True)

    gate = leakage_gate(lambda b: feature_reversal(b["close"], lookback=LOOKBACK), list(by_code.values())[:20])
    if not gate["clean"]:
        print(f"[BLOCK] leakage: {gate['sample_violations']}"); return 1
    print("[leakage] 事前门 PASS", flush=True)

    # 每股 reversal + fwd
    feat_map: dict[str, dict[str, float]] = {}
    fwd_map: dict[str, dict[str, float]] = {}
    for code, bars in by_code.items():
        closes, dates = bars["close"], bars["date"]
        rv = feature_reversal(closes, lookback=LOOKBACK)
        fw = forward_returns(dates, closes, HORIZON)
        feat_map[code] = {d: rv[i] for i, d in enumerate(dates) if rv[i] is not None}
        fwd_map[code] = {d: fw[i] for i, d in enumerate(dates) if fw[i] is not None}

    # 每决策日: Stage1.5 内截面分 市值3档 × 换手3档, 建 9 子格 panel
    panels: dict[str, list[PanelRow]] = defaultdict(list)
    dates_all = sorted({d for c in feat_map for d in feat_map[c]})
    for d in dates_all:
        # 该日 Stage1.5 且有 feat/fwd/daily_basic 的股
        members = [(c, db[(c, d)][0], db[(c, d)][1]) for c in feat_map
                   if d in feat_map[c] and d in fwd_map.get(c, {})
                   and stage_map.get((c, d)) == STAGE and (c, d) in db]
        if len(members) < 9:  # rule-compliance: ok evidence=截面分9格最小样本
            continue
        mvs = sorted(m[1] for m in members)
        tos = sorted(m[2] for m in members)
        n = len(mvs)
        mv_lo, mv_hi = mvs[n // 3], mvs[2 * n // 3]
        to_lo, to_hi = tos[n // 3], tos[2 * n // 3]
        for c, mv, to in members:
            cell = f"mv_{_tier(mv, mv_lo, mv_hi)}|to_{_tier(to, to_lo, to_hi)}"
            panels[cell].append(PanelRow(date=d, code=c, feature=feat_map[c][d], fwd_ret=fwd_map[c][d]))

    print("[ic] 9 子格 walk-forward OOS RankIC ...", flush=True)
    cells: dict[str, dict] = {}
    for cell, panel in panels.items():
        r = oos_rank_ic(panel, embargo_days=EMBARGO)
        cells[cell] = {"oos_rank_ic": r.get("oos_rank_ic"), "ic_ir": r.get("ic_ir"),
                       "n_days": r.get("n_days"), "n_rows": len(panel)}

    def _f(x, fmt):
        return format(x, fmt) if isinstance(x, (int, float)) else "None"

    print("\n===== Stage1.5 × 市值 × 换手 reversal OOS IC (vs Stage1.5 整体 +0.156) =====")
    print(f"{'子格':<20}{'OOS RankIC':>12}{'IC_IR':>8}{'n_days':>8}{'n_rows':>10}")
    for cell in sorted(cells, key=lambda k: -(cells[k]["oos_rank_ic"] or -9)):
        c = cells[cell]
        flag = " <<<" if (c["oos_rank_ic"] or 0) > STAGE15_IC and (c["n_days"] or 0) >= 60 else ""
        print(f"{cell:<20}{_f(c['oos_rank_ic'], '+.4f'):>12}{_f(c['ic_ir'], '.2f'):>8}{(c['n_days'] or 0):>8}{c['n_rows']:>10}{flag}")

    # 事后异常核查 + 最强子格
    best = max((k for k in cells if cells[k]["oos_rank_ic"] is not None and (cells[k]["n_days"] or 0) >= 60),
               key=lambda k: cells[k]["oos_rank_ic"], default=None)
    av = anomaly_verdict(cells[best]["oos_rank_ic"], baseline=STAGE15_IC) if best else {"verdict": "NONE", "action": ""}
    stronger = bool(best and cells[best]["oos_rank_ic"] > STAGE15_IC)
    verdict = "LAYERING_FINDS_STRONGER_CELL" if stronger else "LAYERING_NO_LIFT"
    best_ic_s = _f(cells[best]["oos_rank_ic"], "+.4f") if best else "None"
    print(f"\n最强子格: {best} = {best_ic_s} (vs Stage1.5 +0.156); anomaly={av['verdict']}")
    print(f"VERDICT: {verdict}  ({'子格更强值得单独 backtest' if stronger else '细分未超整体, 市值/换手不显著提升 reversal edge'})")

    out = {"experiment": "layered_segment_ic", "cells": cells, "best_cell": best,
           "best_ic": cells[best]["oos_rank_ic"] if best else None, "stage15_baseline": STAGE15_IC,
           "anomaly": av, "verdict": verdict, "note": "9子格多重比较, 高格须ablation转正"}
    out_path = REPO / "analysis" / "layered_segment_ic_20260615.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[out] {out_path}")

    run_id = "phaseb_layered_segment_ic_20260615"
    with open_store() as st:
        record_pit_check(st, run_id=run_id, step="leakage_gate", check_name="reversal_pit_behavioral", passed=gate["clean"], detail=gate)
        n = record_ic_cells(st, run_id=run_id, data_snapshot=f"stage15_x_mv_x_turnover@{args.start}", cells=cells)
        record_verdict(st, run_id=run_id, family="conditional_segment", verdict=verdict,
                       judges={"best_cell": best, "best_ic": cells[best]["oos_rank_ic"] if best else None, "anomaly": av})
        record_artifact(st, run_id=run_id, artifact_path=out_path)
    print(f"[store] 留档 {n} IC cells + verdict (run_id={run_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
