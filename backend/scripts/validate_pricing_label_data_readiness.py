#!/usr/bin/env python3
"""Validate pricing/label data readiness before model work."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.pricing_policy import (  # noqa: E402
    load_pricing_label_policy,
    record_pricing_label_data_readiness_gate,
)
from services.schema_versions import record_actual_version  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate-run-id", default=None)
    parser.add_argument("--scope", default="model_training")
    parser.add_argument("--feature-table", action="append", dest="feature_tables")
    parser.add_argument("--fail-on-blockers", action="store_true")
    args = parser.parse_args()
    with get_conn() as conn:
        result = record_pricing_label_data_readiness_gate(
            conn,
            policy=load_pricing_label_policy(),
            gate_run_id=args.gate_run_id,
            gate_scope=args.scope,
            feature_tables=args.feature_tables,
        )
        for table in (
            "mart_pricing_label_policy",
            "mart_pricing_label_data_readiness_gate",
            "mart_follow_return_label_build",
            "mart_follow_return_label_quality",
        ):
            record_actual_version(conn, table)
        conn.commit()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if args.fail_on_blockers and result["gate_status"] != "pass":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
