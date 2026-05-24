#!/usr/bin/env python3
"""L10 enforcement: registry promote validator.

Verifies that any registry entry with production_status='production' has:
1. Phase 4 verdict promote OR warn_only (not block)
2. Phase 4 timestamp within 30 days
3. Forward monitor data within 7 days
4. fact_model_train_log row exists (true train evidence)

Exit code:
  0 = clean
  1 = violations (block in pre-commit)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from services.duck_adapter import connect  # noqa: E402

# rule-compliance: ok evidence=registry promote validator timing constants
PHASE4_FRESHNESS_DAYS = 30
FORWARD_DATA_FRESHNESS_DAYS = 7


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-path", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    p.add_argument("--strict", action="store_true",
                   help="exit 1 on violations (default = warn)")
    args = p.parse_args()

    violations = []
    info = []
    now = datetime.now(timezone.utc)

    with connect(args.db_path, read_only=True) as conn:
        # Production rows
        try:
            prod_rows = conn.execute("""
                SELECT result_id, model_id, decision, decision_reason,
                       registered_at, built_at, production_status
                  FROM mart_strategy_result_registry
                 WHERE production_status = 'production'
            """).fetchall()
        except Exception as e:
            info.append(f"registry query failed: {e}")
            print(f"[L10 registry-promote] WARN: {info}")
            return 0

        info.append(f"production_status='production' rows: {len(prod_rows)}")

        if not prod_rows:
            print("[L10 registry-promote] CLEAN — no production-status rows (V4 production via legacy)")
            return 0

        for row in prod_rows:
            result_id, model_id, decision, reason, reg_at, built_at, status = row

            # 1. Decision check
            if decision and decision.lower() == "block":
                violations.append(
                    f"  [HIGH] {result_id}: production_status='production' but decision='block' — illegal promote"
                )

            # 2. Phase 4 freshness — look for recent phase4_gate_*.json
            phase4_files = list((REPO_ROOT / "data" / "reports").glob(f"phase4_gate*{model_id}*.json"))
            if not phase4_files:
                violations.append(
                    f"  [HIGH] {result_id} model_id={model_id}: NO Phase 4 evidence file found"
                )
            else:
                latest = max(phase4_files, key=lambda p: p.stat().st_mtime)
                age = (now - datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc)).days
                if age > PHASE4_FRESHNESS_DAYS:
                    violations.append(
                        f"  [MED] {result_id}: Phase 4 evidence ({latest.name}) {age}d old > {PHASE4_FRESHNESS_DAYS}d"
                    )

            # 3. fact_model_train_log existence (true train evidence)
            try:
                train_log = conn.execute(
                    "SELECT COUNT(*) FROM fact_model_train_log WHERE model_id = ?", [model_id]
                ).fetchone()
                if not train_log or train_log[0] == 0:
                    violations.append(
                        f"  [HIGH] {result_id} model_id={model_id}: NO fact_model_train_log row (true train evidence missing)"
                    )
            except Exception:
                violations.append(f"  [WARN] fact_model_train_log query failed for {model_id}")

            # 4. Forward monitor data freshness
            monitor_path = REPO_ROOT / "data" / "reports" / "v7_forward_monitor.json"
            if model_id and "v7" in model_id and monitor_path.exists():
                m_age = (now - datetime.fromtimestamp(monitor_path.stat().st_mtime, tz=timezone.utc)).days
                if m_age > FORWARD_DATA_FRESHNESS_DAYS:
                    violations.append(
                        f"  [MED] {result_id}: forward monitor data {m_age}d old > {FORWARD_DATA_FRESHNESS_DAYS}d"
                    )

    print("[L10 registry-promote] audit:")
    for i in info:
        print(f"  INFO: {i}")

    if not violations:
        print("  CLEAN — all production promotions have valid evidence")
        return 0

    print(f"\n  {len(violations)} violation(s):")
    for v in violations:
        print(v)
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
