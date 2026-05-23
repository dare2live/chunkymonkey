#!/usr/bin/env python3
"""Audit all historical strategies for ST / 退市 / BSE / ETF contamination.

Per 用户 push '评估之前的策略是不是都含应该被排除的股票' (2026-05-23).

Output:
- Table report per strategy
- JSON audit artifact for downstream tracking
- Verdict per strategy: CLEAN / CONTAMINATED

Usage:
  PYTHONPATH=backend python backend/scripts/audit_strategy_universe.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from services.duck_adapter import connect  # noqa: E402
from services.universe import audit_strategy_universe_contamination, get_active_universe  # noqa: E402

STRATEGIES = [
    ("mart_p0b_oos_predictions", "model_id", "lgbm_20260517_governance_v1_20d", "V4 champion (现 production)"),
    ("mart_p0b_lambdamart_v6_predictions", "model_id", "lgbm_phase5_stability_v6_20260522T071500Z", "v6 stability retrain (BLOCK)"),
    ("mart_p0b_lambdamart_v6_predictions", "model_id", "ensemble_v4_bestchoice_v1", "V4+BC rank-combine"),
    ("mart_p0b_lambdamart_v6_predictions", "model_id", "ensemble_v4_bc_stage_filtered_v1", "V4+BC stage-filtered"),
    ("mart_p0b_lambdamart_v6_predictions", "model_id", "ensemble_v4_intersect_bc_phase7_v1", "V4 ∩ BC + Phase 7"),
    ("mart_p0b_lambdamart_v6_predictions", "model_id", "ensemble_v4_intersect_bc_phase7_st_filtered_v1", "V4 ∩ BC + Phase 7 + ST"),
    ("mart_daily_formula_candidate_bestchoice_v1", "run_id", None, "BC daily picks (all run_ids)"),
]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-json",
                   default=str(REPO_ROOT / "data" / "reports" / "strategy_universe_contamination_audit.json"))
    args = p.parse_args()

    out = {
        "audited_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rationale": "用户 push: 评估历史 strategies 是否含 ST/退市/BSE/ETF",
        "canonical_clean_universe_size": None,
        "strategies": [],
    }

    with connect(str(REPO_ROOT / "data" / "smartmoney.duckdb"), read_only=True) as conn:
        clean_universe = get_active_universe(conn)
        out["canonical_clean_universe_size"] = len(clean_universe)
        print(f"Canonical clean universe (60/00/30/68 + no ST + no 退市): {len(clean_universe)} stocks\n")

        header = f"{'Strategy':<45} {'rows':>10} {'unique':>7} {'ST%':>6} {'退市%':>7} {'BSE%':>6} {'ETF%':>6} {'Verdict':<12}"
        print(header)
        print("-" * 100)

        for table, id_col, id_val, label in STRATEGIES:
            try:
                r = audit_strategy_universe_contamination(
                    conn, table=table, model_id_col=id_col,
                    model_id_filter=id_val,
                )
                if not r.get("total_picks"):
                    print(f"{label:<45} (no data)")
                    continue
                clean = (r["st_pct"] < 0.1 and r["delisted_pct"] < 0.1
                         and r["neeq_pct"] < 0.1 and r["etf_pct"] < 0.1)
                verdict = "CLEAN" if clean else "CONTAMINATED"
                print(f"{label:<45} {r['total_picks']:>10,} {r['unique_stocks']:>7,} "
                      f"{r['st_pct']:>5.1f}% {r['delisted_pct']:>6.2f}% "
                      f"{r['neeq_pct']:>5.2f}% {r['etf_pct']:>5.2f}% {verdict:<12}")
                r["verdict"] = verdict
                r["label"] = label
                out["strategies"].append(r)
            except Exception as e:
                print(f"{label:<45} ERROR: {str(e)[:60]}")
                out["strategies"].append({"label": label, "error": str(e)})

    out_path = Path(args.output_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nReport saved: {out_path}")

    contaminated = [s for s in out["strategies"] if s.get("verdict") == "CONTAMINATED"]
    if contaminated:
        print(f"\n[VERDICT] {len(contaminated)}/{len(out['strategies'])} strategies CONTAMINATED.")
        print("Recommend: 6/1 v7 retrain on panel v5 ST-filtered + 已退市-filtered (4558 stocks).")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
