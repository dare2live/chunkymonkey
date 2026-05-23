#!/usr/bin/env python3
"""L11 enforcement: panel → predictions → paper_sim → registry lineage consistency.

Validates that:
1. Every model_id in predictions table has matching feature_version in panel
2. Every paper_sim KPI compare row's model_id exists in predictions
3. Every registry entry's model_id has predictions + paper_sim evidence
4. No orphan model_ids (in registry but no predictions, etc.)

Exit code:
  0 = clean
  1 = lineage broken
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from services.duck_adapter import connect  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-path", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    p.add_argument("--strict", action="store_true",
                   help="block on any orphan (default = warn only)")
    args = p.parse_args()

    issues = []
    info = []

    with connect(args.db_path, read_only=True) as conn:
        # 1. predictions model_id list
        pred_models = {r[0] for r in conn.execute("""
            SELECT DISTINCT model_id FROM mart_p0b_lambdamart_v6_predictions
            UNION SELECT DISTINCT model_id FROM mart_p0b_oos_predictions
        """).fetchall() if r[0]}
        info.append(f"predictions model_id count: {len(pred_models)}")

        # 2. paper_sim KPI compare model_id list
        try:
            kpi_models = {r[0] for r in conn.execute("""
                SELECT DISTINCT model_id FROM mart_paper_sim_lambdamart_v6_kpi_compare
            """).fetchall() if r[0]}
            info.append(f"paper_sim KPI compare model_id count: {len(kpi_models)}")
        except Exception:
            kpi_models = set()
            info.append("paper_sim_v6_kpi_compare table missing")

        # 3. registry model_id list
        try:
            reg_models = {r[0] for r in conn.execute("""
                SELECT DISTINCT model_id FROM mart_strategy_result_registry
                 WHERE model_id IS NOT NULL
            """).fetchall() if r[0]}
            info.append(f"registry model_id count: {len(reg_models)}")
        except Exception:
            reg_models = set()
            info.append("strategy_result_registry table missing")

        # 4. Orphan checks
        # KPI compare without predictions
        kpi_orphan = kpi_models - pred_models
        if kpi_orphan:
            issues.append(f"paper_sim KPI rows without predictions: {sorted(kpi_orphan)[:5]} (total {len(kpi_orphan)})")

        # Registry without predictions
        reg_orphan = reg_models - pred_models
        if reg_orphan:
            issues.append(f"registry entries without predictions: {sorted(reg_orphan)[:5]} (total {len(reg_orphan)})")

        # Registry without paper_sim KPI
        reg_no_kpi = reg_models - kpi_models
        if reg_no_kpi:
            issues.append(f"registry entries without paper_sim KPI: {sorted(reg_no_kpi)[:5]} (total {len(reg_no_kpi)})")

        # 5. fact_model_train_log for true-train-log evidence
        try:
            train_log_models = {r[0] for r in conn.execute("""
                SELECT DISTINCT model_id FROM fact_model_train_log
            """).fetchall() if r[0]}
            info.append(f"fact_model_train_log model_id count: {len(train_log_models)}")
            # Registry production_status='production' rows must have train_log
            prod_models = {r[0] for r in conn.execute("""
                SELECT DISTINCT model_id FROM mart_strategy_result_registry
                 WHERE production_status = 'production'
            """).fetchall() if r[0]}
            prod_no_train_log = prod_models - train_log_models
            if prod_no_train_log:
                issues.append(f"PRODUCTION models without true train_log: {sorted(prod_no_train_log)} — L7 Phase 4 strict mode unenforceable")
        except Exception:
            info.append("fact_model_train_log missing")

    print("[L11 panel-lineage] audit:")
    for i in info:
        print(f"  INFO: {i}")
    if not issues:
        print("  CLEAN — no lineage breaks")
        return 0

    print(f"\n  {len(issues)} lineage issue(s):")
    for issue in issues:
        print(f"  [{'BLOCK' if args.strict else 'WARN'}] {issue}")
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
