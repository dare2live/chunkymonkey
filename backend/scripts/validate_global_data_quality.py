#!/usr/bin/env python3
"""Run the global zero-silent-missing data quality gate."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.data_quality import record_global_data_quality_gate  # noqa: E402
from services.db import get_conn  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate-run-id", default=None)
    parser.add_argument("--scope", default="model_training")
    parser.add_argument(
        "--feature-table",
        action="append",
        dest="feature_tables",
        help="Feature table to scan. Repeatable. Defaults to production and candidate panels.",
    )
    parser.add_argument("--skip-market", action="store_true")
    parser.add_argument("--skip-institution-events", action="store_true")
    parser.add_argument("--skip-pipeline-performance", action="store_true")
    parser.add_argument("--no-strict-feature-nulls", action="store_true")
    parser.add_argument("--recent-pipeline-limit", type=int, default=200)
    parser.add_argument("--example-limit", type=int, default=5)
    args = parser.parse_args()

    with get_conn() as conn:
        result = record_global_data_quality_gate(
            conn,
            gate_run_id=args.gate_run_id,
            gate_scope=args.scope,
            feature_tables=args.feature_tables,
            include_market=not args.skip_market,
            include_institution_events=not args.skip_institution_events,
            include_pipeline_performance=not args.skip_pipeline_performance,
            strict_feature_nulls=not args.no_strict_feature_nulls,
            recent_pipeline_limit=args.recent_pipeline_limit,
            example_limit=args.example_limit,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["gate_status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
