#!/usr/bin/env python3
"""Optuna v4 progress monitor — read log + DB, estimate ETA + show top trials.

Usage:
    PYTHONPATH=backend python backend/scripts/monitor_optuna_v4.py
    PYTHONPATH=backend python backend/scripts/monitor_optuna_v4.py --log <path>

Reports:
- Process status (PID, elapsed, RSS)
- LGBM fits done
- Trial completions (best so far)
- ETA estimate
- Comparison vs baseline 0.0246
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb

from services.db import DB_PATH

BASELINE_RANK_IC = 0.0246  # rule-compliance: ok evidence=governance-v1-baseline-published


def find_latest_log(log_dir: Path) -> Path | None:
    logs = sorted(log_dir.glob("optuna_v4_*.log"))
    return logs[-1] if logs else None


def parse_log(log_path: Path) -> dict:
    stats = {
        "stage1_done_at": None,
        "lgbm_fits": 0,
        "trials_completed": [],  # list of (trial_n, mean_ic, std_ic, score)
        "started_at": None,
    }
    if not log_path.exists():
        return stats
    with open(log_path) as f:
        for line in f:
            if "Stage 1 done" in line:
                stats["stage1_done_at"] = line.split(" ")[0]
            if "warnings.warn" in line:
                stats["lgbm_fits"] += 1
            m = re.search(r"trial (\d+): mean_ic=([0-9.\-]+) std_ic=([0-9.\-]+) → score=([0-9.\-]+)", line)
            if m:
                stats["trials_completed"].append((
                    int(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))
                ))
            if "run_id=" in line and not stats["started_at"]:
                stats["started_at"] = line.split(" ")[0]
    return stats


def get_pid_status(pid: int) -> dict:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "pid,etime,rss"],
            capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            return {"alive": False}
        lines = result.stdout.strip().splitlines()
        if len(lines) < 2:
            return {"alive": False}
        parts = lines[1].split()
        return {"alive": True, "pid": parts[0], "etime": parts[1],
                "rss_mb": int(parts[2]) // 1024 if len(parts) > 2 else 0}
    except Exception as e:
        # rule-compliance: ok evidence=monitor-best-effort
        return {"alive": False, "err": str(e)}


def get_db_trials(run_id_prefix: str = "p0b_optuna_v4_") -> list:
    try:
        con = duckdb.connect(str(DB_PATH), read_only=True)
        rows = con.execute(
            "SELECT trial_number, state, value, rank_ic_mean, rank_ic_std, n_windows "
            "FROM mart_p1_optuna_trials "
            "WHERE run_id LIKE ? AND state='COMPLETE' "
            "ORDER BY value DESC LIMIT 10",
            [f"{run_id_prefix}%"]
        ).fetchall()
        con.close()
        return rows
    except Exception as e:
        # rule-compliance: ok evidence=monitor-best-effort
        return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, default=47508)  # rule-compliance: ok evidence=current-optuna-v4-pid
    parser.add_argument("--log", default=None)
    parser.add_argument("--n-trials", type=int, default=50)  # rule-compliance: ok evidence=optuna-v4-config
    args = parser.parse_args()

    log_dir = Path(__file__).resolve().parents[2] / "data" / "audit" / "logs"
    log_path = Path(args.log) if args.log else find_latest_log(log_dir)
    if not log_path:
        print(f"No optuna_v4 log found in {log_dir}")
        return 1

    print(f"=== Optuna v4 monitor ({datetime.now().isoformat(timespec='seconds')}) ===")
    print(f"Log: {log_path}")
    print()

    proc = get_pid_status(args.pid)
    if proc["alive"]:
        print(f"Process: PID {proc['pid']}, elapsed {proc['etime']}, RSS {proc['rss_mb']}MB")
    else:
        print(f"Process PID {args.pid}: NOT alive (may have finished or crashed)")
    print()

    stats = parse_log(log_path)
    n_completed = len(stats["trials_completed"])
    print(f"LGBM fits: {stats['lgbm_fits']} (~{stats['lgbm_fits']/16:.1f} trials worth)")
    print(f"Trial completions: {n_completed} / {args.n_trials}")
    print()

    if stats["trials_completed"]:
        # Top 5 trials by score
        sorted_trials = sorted(stats["trials_completed"], key=lambda t: t[3], reverse=True)
        print(f"Top 5 trials by score (mean - 0.5*std):")
        for t, ic, std, score in sorted_trials[:5]:
            status = "BEAT baseline" if ic > BASELINE_RANK_IC else "below baseline"
            print(f"  trial {t}: mean_ic={ic:.4f}, std={std:.3f}, score={score:.4f}  [{status}]")
        print()

        best_ic = max(t[1] for t in stats["trials_completed"])
        baseline_gap = (best_ic - BASELINE_RANK_IC) / BASELINE_RANK_IC * 100
        print(f"Best rank_ic so far: {best_ic:.4f}")
        print(f"Baseline:            {BASELINE_RANK_IC:.4f}")
        print(f"Gap:                 {baseline_gap:+.1f}%")
        print()

    # ETA estimate
    if proc["alive"] and n_completed >= 1:
        # Parse "MM:SS" or "HH:MM:SS"
        etime_parts = proc["etime"].split(":")
        if len(etime_parts) == 3:
            h, m, s = int(etime_parts[0]), int(etime_parts[1]), int(etime_parts[2])
        else:
            h, m, s = 0, int(etime_parts[0]), int(etime_parts[1])
        elapsed_min = h * 60 + m + s / 60
        avg_min_per_trial = elapsed_min / max(n_completed, 0.5)
        remaining_trials = args.n_trials - n_completed
        eta_min = remaining_trials * avg_min_per_trial
        eta_hours = eta_min / 60
        print(f"Avg min/trial: {avg_min_per_trial:.1f}")
        print(f"Remaining: {remaining_trials} trials × {avg_min_per_trial:.1f} min = {eta_min:.0f} min ({eta_hours:.1f} h)")

    print()
    db_trials = get_db_trials()
    print(f"DB trials persisted: {len(db_trials)}")
    for row in db_trials[:5]:
        print(f"  {row}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
