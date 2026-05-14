#!/usr/bin/env python3
"""Paper Sim hybrid w-grid CLI (Codex 7-day plan Day 6).

跑 5 个 w_ml grid 值 {0, 0.10, 0.20, 0.30, 0.40}, 每个 w 一次 walk-forward,
对比 ML-only (w=1.0) vs stage-only (w=0.0) vs hybrid (w ∈ middle).

用法:
    PYTHONPATH=backend python backend/scripts/run_paper_sim_hybrid_grid.py \\
        --start 2024-01-01 --end 2026-04-30 \\
        --w-grid 0.00,0.10,0.20,0.30,0.40
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.paper_sim.config import load_config

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("run_paper_sim_hybrid_grid")


def main() -> int:
    parser = argparse.ArgumentParser(description="Paper Sim hybrid w-grid (Day 6)")
    parser.add_argument("--config-path", default="backend/config/paper_sim_hybrid.yaml")
    parser.add_argument("--start", default="2024-01-01")  # rule-compliance: ok evidence=alpha158-panel-实测范围
    parser.add_argument("--end", default="2026-04-30")    # rule-compliance: ok evidence=alpha158-panel-实测范围
    parser.add_argument("--w-grid", default="0.00,0.10,0.20,0.30,0.40",
                        help="w_ml grid (comma-sep), Codex Q5 推荐 5 个值")
    parser.add_argument("--model-id", default="lgbm_baseline_v1",
                        help="P0b model_id; v3 跑后改为 lgbm_v3_20d 等")
    parser.add_argument("--label-suffix", default="hybrid",
                        help="run_id 标识 (default 'hybrid')")
    args = parser.parse_args()

    w_list = [float(x.strip()) for x in args.w_grid.split(",") if x.strip()]
    log.info(f"=== Paper Sim hybrid w-grid run ===")
    log.info(f"  config: {args.config_path}")
    log.info(f"  date range: {args.start} → {args.end}")
    log.info(f"  w grid: {w_list}")
    log.info(f"  model_id: {args.model_id}")

    # Codex C5 (a163ca58): 移除 wrong import path 'services.scripts.run_paper_sim_v2',
    # 直接 import scripts.run_paper_sim_v2 (sys.path 已含 backend)
    from importlib import import_module
    runner = import_module("scripts.run_paper_sim_v2")

    summaries = []
    for w in w_list:
        run_id = f"hybrid_w{w:.2f}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        log.info("")
        log.info(f"=== w={w:.2f} run_id={run_id} ===")
        cfg = load_config(
            path=Path(args.config_path),
            override={
                "selection": {
                    "hybrid_model_id": args.model_id,
                    "hybrid_w_ml": w,
                }
            },
        )
        t0 = time.time()
        summary = runner.run_walk_forward(
            f"hybrid_w{w:.2f}", args.start, args.end, cfg,
            sim_run_id=run_id,
        )
        elapsed = time.time() - t0
        summaries.append({"w": w, "run_id": run_id, "summary": summary, "elapsed_s": elapsed})
        log.info(f"  done in {elapsed:.0f}s")

    # Comparison table
    log.info("")
    log.info("=== W-Grid Summary ===")
    log.info(f"{'w':>6} | {'ann_ret':>10} | {'max_dd':>10} | {'excess':>10} | {'monthly_win':>12} | {'sharpe':>8} | {'all_pass':>8}")
    log.info("-" * 80)
    for entry in summaries:
        s = entry["summary"]
        uc = s.get("user_criteria", {})
        log.info(
            f"{entry['w']:>6.2f} | "
            f"{uc.get('annual_return', 0)*100:>8.1f}%  | "
            f"{uc.get('max_dd', 0)*100:>8.1f}%  | "
            f"{uc.get('excess_total_return', 0)*100:>8.1f}%  | "
            f"{uc.get('monthly_win_rate', 0)*100:>10.0f}%   | "
            f"{uc.get('sharpe', 0):>+8.2f} | "
            f"{'PASS' if s.get('all_pass') else 'FAIL':>8}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
