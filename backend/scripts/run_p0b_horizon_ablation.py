#!/usr/bin/env python3
"""P0b horizon ablation — 同特征 × 3 horizon (5/10/20) 跑 walk-forward.

PLAN_V3 §3 #5 数据决定的决策点: "label horizon 5/10/20 三套 label 训练,
OOS composite 高者胜". 此 script 跑 3 个完整 P0b walk-forward,
打印 RankIC × horizon 对比.

用法:
    PYTHONPATH=backend python backend/scripts/run_p0b_horizon_ablation.py \
        --start-date 2024-01-01 --end-date 2026-04-30
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("p0b_horizon_ablation")


def main() -> int:
    parser = argparse.ArgumentParser()
    # CLI defaults 跟 alpha158 panel 实测范围 (2023-01-03..2026-04-23) 对齐, 用户可覆盖.
    # rule-compliance: ok evidence=alpha158-panel-实测范围
    parser.add_argument("--start-date", default="2024-01-01")  # rule-compliance: ok evidence=alpha158-panel-实测范围
    parser.add_argument("--end-date", default="2026-04-30")  # rule-compliance: ok evidence=alpha158-panel-实测范围
    parser.add_argument("--n-estimators", type=int, default=200)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    horizons = ["fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d"]
    results = {}

    for label in horizons:
        run_id = f"p0b_horizon_{label.replace('fwd_cost_after_', '')}"
        model_id = f"lgbm_baseline_{label.replace('fwd_cost_after_', '')}"
        log.info(f"=== Running {label} ===")
        r = subprocess.run(
            ["python", "backend/scripts/train_p0b_lightgbm.py",
             "--label", label,
             "--run-id", run_id,
             "--model-id", model_id,
             "--start-date", args.start_date,
             "--end-date", args.end_date,
             "--n-estimators", str(args.n_estimators)],
            cwd=repo_root,
            env={**__import__("os").environ, "PYTHONPATH": "backend"},
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            log.error(f"{label} FAILED: {r.stderr[-500:]}")
            continue
        # 解析 stdout 找 OOS RankIC
        rank_ic = None
        ic_ir = None
        n_dates = None
        for line in r.stdout.splitlines():
            if "mean:" in line and rank_ic is None:
                try:
                    rank_ic = float(line.split("mean:")[1].strip())
                except (ValueError, IndexError):
                    pass
            elif "IC IR:" in line and ic_ir is None:
                try:
                    ic_ir = float(line.split("IC IR:")[1].strip())
                except (ValueError, IndexError):
                    pass
            elif "n_dates:" in line and n_dates is None:
                try:
                    n_dates = int(line.split("n_dates:")[1].split(",")[0].strip())
                except (ValueError, IndexError):
                    pass
        results[label] = {
            "rank_ic": rank_ic, "ic_ir": ic_ir, "n_dates": n_dates,
            "passed": (rank_ic or 0) >= 0.03 and (n_dates or 0) >= 30,
        }
        log.info(f"  {label}: RankIC={rank_ic} IC IR={ic_ir} n_dates={n_dates}")

    log.info("")
    log.info("=== Horizon Ablation Summary ===")
    log.info(f"{'horizon':25s} {'RankIC':>8s} {'IC IR':>8s} {'n_dates':>8s} {'Gate':>6s}")
    for label in horizons:
        r = results.get(label, {})
        gate = "PASS" if r.get("passed") else "FAIL"
        log.info(
            f"{label:25s} {r.get('rank_ic', float('nan')):>8.4f} "
            f"{r.get('ic_ir', float('nan')):>8.4f} {r.get('n_dates', 0):>8d} {gate:>6s}"
        )
    best = max(results.items(), key=lambda kv: kv[1].get("rank_ic") or -1, default=None)
    if best:
        log.info(f"\nBest horizon: {best[0]} (RankIC={best[1].get('rank_ic')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
