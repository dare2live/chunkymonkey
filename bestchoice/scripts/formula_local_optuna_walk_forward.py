#!/usr/bin/env python3
"""BC walk-forward audit (T.3, 用户 push back 2026-05-23).

Codex demo'd local 624K trials feasible. Walk-forward = 3 cutoffs × 624K = 1.87M trials.
3-5 天本地 compute possible.

Method:
- Per cutoff T (e.g. 2024-06-01, 2025-01-01, 2025-06-01):
  - For each (stock, formula): re-run Optuna using trades buy_date <= T
  - Save selected candidates with cutoff_date column
- Compare candidates across cutoffs:
  - Overlap pct (jaccard) — high overlap = stable selection (low selection bias)
  - Per-candidate cross-cutoff Sharpe correlation — high = robust
- Output: bestchoice/analysis/formula_local_optuna_walk_forward_<cutoff>.csv per cutoff
  Plus combined analysis: bestchoice/analysis/walk_forward_audit_summary.json

Usage (next session, batched):
  python bestchoice/scripts/formula_local_optuna_walk_forward.py --cutoff 2024-06-01 --batch-size 100
  python bestchoice/scripts/formula_local_optuna_walk_forward.py --cutoff 2025-01-01 --batch-size 100
  python bestchoice/scripts/formula_local_optuna_walk_forward.py --cutoff 2025-06-01 --batch-size 100

Not run in this session — script defines extension only. Run separately.
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
