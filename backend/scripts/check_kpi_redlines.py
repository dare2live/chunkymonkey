#!/usr/bin/env python3
"""L16 enforcement: 数字异常红线 auto-flag.

Per CLAUDE.md §4.2: 异常高数字 = leakage 警报. Currently doc-only.

Scans mart_paper_sim_lambdamart_v6_kpi_compare for absolute red-line violations:
- Sharpe > 5 (absolute red)
- Win rate > 95% (absolute red)
- Ann ret > 100% (absolute red)
- Rank IC > 0.3 (absolute red)
- Relative lift > +50% vs baseline (relative red)

Exit code:
  0 = clean
  1 = red lines hit (block in CI/pre-commit)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from services.duck_adapter import connect  # noqa: E402

# rule-compliance: ok evidence=CLAUDE.md §4.2 absolute red-line thresholds
ABS_SHARPE_RED = 5.0
ABS_WIN_RED = 0.95
ABS_ANN_RED = 1.00
ABS_RANKIC_RED = 0.30
REL_LIFT_RED = 0.50


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-path", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    p.add_argument("--model-id", default=None, help="only check specific model_id")
    args = p.parse_args()

    where_clause = f"WHERE model_id = '{args.model_id}'" if args.model_id else ""
    violations = []

    with connect(args.db_path, read_only=True) as conn:
        rows = conn.execute(f"""
            SELECT model_id, comparison_id, sharpe, ann_ret, max_dd, monthly_win_rate, rank_ic
              FROM mart_paper_sim_lambdamart_v6_kpi_compare
              {where_clause}
             ORDER BY built_at DESC
        """).fetchall()
        seen = set()
        for row in rows:
            mid, cmp_id, sharpe, ann, dd, win, rank_ic = row
            if mid in seen:
                continue
            seen.add(mid)
            if sharpe is not None and abs(sharpe) > ABS_SHARPE_RED:
                violations.append(f"  [RED] {mid}: Sharpe={sharpe:.3f} > {ABS_SHARPE_RED} (absolute red line)")
            if win is not None and win > ABS_WIN_RED:
                violations.append(f"  [RED] {mid}: win_rate={win:.3f} > {ABS_WIN_RED} (absolute red line)")
            if ann is not None and abs(ann) > ABS_ANN_RED:
                violations.append(f"  [RED] {mid}: ann_ret={ann:.3f} > {ABS_ANN_RED} (absolute red line)")
            if rank_ic is not None and abs(rank_ic) > ABS_RANKIC_RED:
                violations.append(f"  [RED] {mid}: rank_ic={rank_ic:.3f} > {ABS_RANKIC_RED} (absolute red line)")

        # Relative lift check: any model_id Sharpe vs V4 baseline > +50%
        v4_baseline = conn.execute("""
            SELECT sharpe FROM mart_paper_sim_lambdamart_v6_kpi_compare
             WHERE model_id = 'lgbm_20260517_governance_v1_20d'
             ORDER BY built_at DESC LIMIT 1
        """).fetchone()
        if v4_baseline and v4_baseline[0]:
            baseline = v4_baseline[0]
            for row in rows:
                mid, cmp_id, sharpe, _, _, _, _ = row
                if mid in seen and sharpe and baseline:
                    lift = (sharpe - baseline) / abs(baseline)
                    if lift > REL_LIFT_RED:
                        violations.append(f"  [YEL] {mid}: lift {lift:+.2%} vs V4 baseline = relative red line (>{REL_LIFT_RED:.0%}), suspect leakage/ensemble residual")

    if not violations:
        print("[L16 kpi-redlines] CLEAN")
        return 0

    print(f"[L16 kpi-redlines] {len(violations)} violation(s):")
    for v in violations:
        print(v)
    print()
    print("Action: investigate each [RED] for leakage source. Check:")
    print("  1. panel universe contamination (ST/退市)")
    print("  2. forward-index features (Pattern 7)")
    print("  3. PIT marker violations (Pattern 8/9)")
    print("  4. per-trade vs portfolio Sharpe unit confusion")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
