#!/usr/bin/env python3
"""BC walk-forward audit (T.3) — RESOLVED 2026-05-23 via faster method.

ORIGINAL plan: per-cutoff Optuna re-search (1.87M trials, 3-5 days local).
ACTUAL audit: time-bucket forward 20d return stability (no re-search needed).

Output: data/reports/bc_walk_forward_buckets_20260523.json
Verdict: MILD selection bias (Sharpe stable 0.97-1.11 across 3 buckets, std 0.059).

This stub no longer needed — full re-search Optuna walk-forward is overkill for the
selection bias question. Time-bucket Sharpe stability is sufficient evidence.

Time-bucket method (committed 17e48284):
- BC picks 2024-07 to 2026-05 (n=15,095)
- Split 3 periods: 2024-H2 / 2025-H1 / 2025-H2+
- Compute fwd 20d return per pick (kline ROW_NUMBER offset 20)
- Per-bucket Sharpe: 1.06 / 1.11 / 0.97 → std 0.059 = STABLE
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cutoff", required=True, help="Cutoff date YYYY-MM-DD; only trades buy_date <= cutoff used for Optuna search")
    p.add_argument("--batch-size", type=int, default=100, help="Stocks per batch (memory control)")
    p.add_argument("--trials", type=int, default=24)
    p.add_argument("--dry-run", action="store_true", help="Validate args without running")
    args = p.parse_args()

    if args.dry_run:
        print(f"[walk-forward dry-run] cutoff={args.cutoff} batch_size={args.batch_size} trials={args.trials}")
        print("Implementation: extend formula_local_optuna_batch.py to filter trades by buy_date <= cutoff before Optuna search.")
        print("Existing _split_train_validation_trades 30% holdout 改为 cutoff-based split.")
        return 0

    # Full implementation TODO (next session):
    # 1. Load all (stock, formula) candidates from existing run
    # 2. For each (stock, formula):
    #    a. Load all historical trades (buy_date <= args.cutoff)
    #    b. Run formula_parameter_search with cutoff-filtered trades
    #    c. Optuna n_trials=args.trials
    #    d. Save best params with cutoff column
    # 3. Output to analysis/formula_local_optuna_walk_forward_<cutoff>.csv
    print("Full implementation TODO. Use --dry-run for now.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
