#!/usr/bin/env python3
"""BC Phase 6c: register V4+BC ensemble result to mart_strategy_result_registry.

Writes 1 row to registry as ensemble_challenger with:
- KPI from paper_sim_v6_compare (Sharpe 1.83 / ann 74.39% / dd -16.85% / win 60%)
- production_status=candidate_forward_monitor (not promoted, waiting verification)
- decision=hold_challenger (per Phase 5 MILD bias verdict)
- baseline_result_id linked to V4 champion
- evidence_json with audit caveat + raw KPI

Idempotent: DELETE then INSERT per result_id.
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
from services.bestchoice_config import DEFAULT_BESTCHOICE_PIPELINE_CONFIG  # noqa: E402

DEFAULT_RESULT_ID = "ensemble_v4_bc_v1_20260522"
DEFAULT_COMPARISON_ID = "lm_v6_compare_20260522T074141"
DEFAULT_BASELINE_MODEL_ID = "lgbm_20260517_governance_v1_20d"
EVIDENCE_PERIOD_START = DEFAULT_BESTCHOICE_PIPELINE_CONFIG.ensemble_test_start_date
EVIDENCE_PERIOD_END = DEFAULT_BESTCHOICE_PIPELINE_CONFIG.ensemble_test_end_date


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-path", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    p.add_argument("--result-id", default=DEFAULT_RESULT_ID)
    p.add_argument("--comparison-id", default=DEFAULT_COMPARISON_ID)
    args = p.parse_args()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")  # Phase ψ.5 allowlist: registered_at lineage 非 trade_date
    evidence = {
        "method": "rank-percentile combine v4_score + bc_confidence",
        "audit_evidence": "data/reports/bestchoice_walkforward_lite/audit_20260522T104228.csv",
        "audit_verdict": "MILD bias (-16.4% drop W1 vs W4), 真 forward Sharpe 估 1.5-1.7",
        "paper_sim_kpi": {
            "sharpe": 1.83, "ann_ret": 0.7439, "max_dd": -0.1685,
            "win_rate": 0.60, "rank_ic": 0.0252,
        },
        "caveat": "BC selection bias mild; need 6-12 weeks forward monitor before promotion",
    }

    with connect(args.db_path, read_only=False) as conn:
        baseline = conn.execute(
            "SELECT result_id FROM mart_strategy_result_registry "
            "WHERE model_id = ? ORDER BY registered_at DESC LIMIT 1",
            [DEFAULT_BASELINE_MODEL_ID],
        ).fetchone()
        baseline_id = baseline[0] if baseline else None

        conn.execute("DELETE FROM mart_strategy_result_registry WHERE result_id = ?", [args.result_id])
        conn.execute(
            """
            INSERT INTO mart_strategy_result_registry
            (result_id, source_table, source_pk, result_type, model_id, sim_run_id, comparison_id,
             variant, model_label, period_start, period_end, annual_return, max_dd, sharpe,
             monthly_win_rate, rank_ic, leakage_flag, production_status, decision, decision_reason,
             evidence_json, built_at, registered_at, baseline_result_id, params_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                args.result_id,
                "mart_paper_sim_lambdamart_v6_kpi_compare",
                f"{args.comparison_id}/ensemble_v4_bestchoice_v1",
                "ensemble_challenger",
                "ensemble_v4_bestchoice_v1",
                f"{args.comparison_id}_lambdamart_v6",
                args.comparison_id,
                "v1", "ensemble_v4_bc",
                # rule-compliance: ok evidence=paper_sim period aligned with config-owned ensemble test window
                EVIDENCE_PERIOD_START, EVIDENCE_PERIOD_END,
                0.7439, -0.1685, 1.83, 0.60, 0.0252,
                True,  # leakage_flag — BC has MILD bias caveat
                "candidate_forward_monitor",
                "hold_challenger",
                "Phase 5 MILD bias verdict; ensemble 4/4 user targets met; need 6-12 weeks forward monitor",
                json.dumps(evidence, ensure_ascii=False),
                now, now, baseline_id,
                json.dumps({
                    "method": "rank_combine_v4_bc",
                    "v4_model": "lgbm_20260517_governance_v1_20d",
                    "bc_run": DEFAULT_BESTCHOICE_PIPELINE_CONFIG.bc_run_id,
                }),
            ],
        )
        conn.commit()
        r = conn.execute(
            "SELECT result_id, sharpe, annual_return, max_dd, monthly_win_rate, "
            "production_status, decision FROM mart_strategy_result_registry WHERE result_id = ?",
            [args.result_id],
        ).fetchone()
        print(f"[OK] BC ensemble registered: result_id={r[0]}")
        print(f"  sharpe={r[1]:.2f} ann={r[2]:.2%} dd={r[3]:.2%} win={r[4]:.2%}")
        print(f"  production_status={r[5]}, decision={r[6]}")
        print(f"  baseline_id={baseline_id}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
