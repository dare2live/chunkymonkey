#!/usr/bin/env python3
"""Sizer ablation: equal vs score_rank_diff_v1 (Codex round 19 #1 user "差异化到底").

Run paper_sim with 2 different position_sizing strategies on same period/model,
compare KPIs to validate user's "差异化到底" hypothesis.

Expected per Codex round 19:
- alpha 是根因, sizing alone ≤ +2-8pp 年化
- score_rank_diff_v1 给 top stocks 更大权重 (rank^p × vol haircut)
- 集中度更高 → max_dd 可能更深, 月胜率可能更不稳

usage:
    PYTHONPATH=backend python backend/scripts/run_paper_sim_sizer_ablation.py \\
        --start 2024-07-01 --end 2026-04-13

prerequisite:
- Optuna PID 释放 DB writer
- mart_p0b_oos_predictions 有 lgbm_v4 model 输出 (新 retrain 后)
- 2 yaml configs ready:
    backend/config/paper_sim_ml_score_governance_v1.yaml (equal sizer)
    backend/config/paper_sim_ml_score_governance_v1_rank_diff.yaml (score_rank_diff_v1)
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("paper_sim_sizer_ablation")


REPO = Path(__file__).resolve().parents[2]

VARIANTS = [
    {
        "label": "equal",
        "config": "backend/config/paper_sim_ml_score_governance_v1.yaml",
        "desc": "Equal weight (1/N) baseline",
    },
    {
        "label": "score_rank_diff_v1",
        "config": "backend/config/paper_sim_ml_score_governance_v1_rank_diff.yaml",
        "desc": "Codex round 19 #1 — rank^p × vol haircut tilt (30/23/17/10/5)",
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Sizer ablation paper_sim 2-variant")
    parser.add_argument("--start", default="2024-07-01")  # rule-compliance: ok evidence=Codex-C-D-paper-sim-起点
    parser.add_argument("--end", default="2026-04-13")    # rule-compliance: ok evidence=alpha158-panel-实测范围
    parser.add_argument("--dry-run", action="store_true", help="只列 cmd, 不跑")
    args = parser.parse_args()

    log.info(f"=== Sizer ablation ({args.start} → {args.end}) ===")
    log.info("Variants:")
    for v in VARIANTS:
        log.info(f"  - {v['label']}: {v['desc']}")
    log.info("")

    results = []
    for v in VARIANTS:
        cfg_path = REPO / v["config"]
        if not cfg_path.exists():
            log.error(f"Config missing: {cfg_path}")
            return 1

        cmd = [
            "python", "backend/scripts/run_paper_sim_v2.py",
            "--variant", f"sizer_ablation_{v['label']}",
            "--config-path", str(cfg_path),
            "--start", args.start,
            "--end", args.end,
        ]
        log.info(f"\n=== Variant: {v['label']} ===")
        log.info(f"cmd: {' '.join(cmd)}")
        if args.dry_run:
            continue

        t0 = time.time()
        env = {"PYTHONPATH": "backend"}
        result = subprocess.run(cmd, env={**dict(__import__('os').environ), **env},
                                 cwd=REPO, capture_output=True, text=True)
        elapsed = time.time() - t0
        log.info(f"  exit code: {result.returncode}, elapsed: {elapsed:.1f}s")
        if result.returncode != 0:
            log.error(f"  stderr: {result.stderr[-500:]}")
            return 1
        log.info(f"  stdout tail: {result.stdout[-500:]}")
        results.append({"label": v["label"], "elapsed": elapsed, "rc": result.returncode})

    # Compare KPIs via SQL (mart_paper_sim_kpi)
    if not args.dry_run and len(results) == 2:
        log.info("\n=== KPI Comparison ===")
        import duckdb
        from services.db import DB_PATH
        con = duckdb.connect(str(DB_PATH), read_only=True)
        try:
            rows = con.execute("""
                SELECT variant, annual_return, max_dd, monthly_win_rate,
                       excess_vs_hs300, sharpe, total_trades, avg_holding_days
                  FROM mart_paper_sim_kpi
                 WHERE variant LIKE 'sizer_ablation_%'
                 ORDER BY built_at DESC
                 LIMIT 2
            """).fetchall()
            cols = ["variant", "ann_ret", "max_dd", "monthly_win", "excess_hs300",
                    "sharpe", "n_trades", "avg_hold"]
            log.info(f"  {'|'.join(f'{c:>14}' for c in cols)}")
            for r in rows:
                log.info(f"  {'|'.join(f'{str(v):>14}' for v in r)}")
        finally:
            con.close()

    log.info("=== Done ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
