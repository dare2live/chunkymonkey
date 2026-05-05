#!/usr/bin/env python3
"""Seed mart_pipeline_run_manifest with the current audited baseline.

This is intentionally explicit: it records the local state that was already
validated before the productionization plan started, so later runs have a
baseline to compare against in the UI.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import record_pipeline_run  # noqa: E402


BASELINE_ROWS = [
    {
        "run_id": "baseline_71de3a43_data_health_20260505",
        "pipeline_name": "data_health_snapshot",
        "status": "success",
        "duration_s": None,
        "input_tables": ["dim_data_asset"],
        "output_tables": ["mart_data_health"],
        "perf_summary": {"green": 147, "yellow": 0, "red": 0, "dry_run": True},
    },
    {
        "run_id": "baseline_71de3a43_pytest_20260505",
        "pipeline_name": "pytest",
        "status": "success",
        "duration_s": None,
        "input_tables": ["backend/tests"],
        "output_tables": [],
        "perf_summary": {"passed": 367, "python_jobs": ["3.10", "3.11"]},
    },
    {
        "run_id": "cleanup_full_multidim_v2_base_dense_v2_20260505_093800",
        "pipeline_name": "train_multidim_model",
        "status": "success",
        "duration_s": 3144.0,
        "input_tables": ["fact_feature_panel"],
        "output_tables": ["mart_multidim_model", "mart_multidim_prediction"],
        "model_id": "cleanup_full_multidim_v2_base_dense_v2_20260505_093800",
        "feature_group": "base_dense_v2",
        "label_name": "forward_ret_20d",
        "holding_period": 20,
        "perf_summary": {
            "rows": 3910880,
            "codes": 5187,
            "dates": 783,
            "n_features": 54,
            "trials": 2,
            "num_round": 80,
            "holdout_rank_ic": 0.07137715675573271,
            "portfolio_nav": 1.126,
            "portfolio_annualized": 0.2888,
            "portfolio_max_drawdown": -0.1633,
            "portfolio_sharpe": 1.14,
        },
    },
    {
        "run_id": "walkforward_20260505_100126",
        "pipeline_name": "run_multidim_walkforward",
        "status": "success",
        "duration_s": None,
        "input_tables": ["fact_feature_panel", "mart_multidim_model"],
        "output_tables": ["mart_model_walkforward_fold", "mart_model_walkforward_prediction"],
        "model_id": "cleanup_full_multidim_v2_base_dense_v2_20260505_093800",
        "feature_group": "base_dense_v2",
        "label_name": "forward_ret_20d",
        "holding_period": 20,
        "perf_summary": {
            "folds": 2,
            "avg_rank_ic": 0.17727377638220787,
            "std_rank_ic": 0.08735843145303804,
            "fold_rank_ic": [0.1155, 0.2390],
            "quality": "ok",
        },
    },
    {
        "run_id": "deploy_gate_cleanup_full_multidim_v2_base_dense_v2_20260505_093800",
        "pipeline_name": "check_deploy_gate",
        "status": "success",
        "duration_s": None,
        "input_tables": [
            "mart_multidim_model",
            "mart_model_lifecycle",
            "mart_feature_drift",
            "mart_model_walkforward_fold",
        ],
        "output_tables": ["mart_model_lifecycle"],
        "model_id": "cleanup_full_multidim_v2_base_dense_v2_20260505_093800",
        "feature_group": "base_dense_v2",
        "label_name": "forward_ret_20d",
        "holding_period": 20,
        "gate_result": "reject",
        "blockers": [
            "ic_walkforward_std=0.0874 > max=0.0300",
            "drift_score=0.4948 > max=0.2500",
        ],
        "perf_summary": {
            "uplift_vs_champion": 0.13985876816876863,
            "drift_score": 0.4948120365598783,
            "ic_walkforward_std": 0.08735843145303804,
        },
    },
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit-sha", default="71de3a43")
    args = parser.parse_args()

    conn = get_conn()
    try:
        for item in BASELINE_ROWS:
            record_pipeline_run(
                conn,
                commit_sha=args.commit_sha,
                cwd=str(Path(__file__).resolve().parent.parent.parent),
                command=f"seed_pipeline_manifest.py --commit-sha {args.commit_sha}",
                started_at="2026-05-05T00:00:00",
                ended_at="2026-05-05T00:00:00",
                **item,
            )
        print(f"seeded {len(BASELINE_ROWS)} manifest rows")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
