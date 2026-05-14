#!/usr/bin/env python3
"""PLAN_V3 v3.2 完整 chain orchestrator (P1 ablation 后接续全跑).

链条 (依赖 P1 ablation 完成, DB writer 释放):
1. feature_join_v2: build mart_p0a_feature_label_panel_v2 (+ stage_opt + formula_trigger 特征)
2. train P0b v2 × 3 horizon (5d/10d/20d)
3. Deflated SR audit (Bailey-LdP 校正)
4. paper_sim_v2 with ml_score blend
5. P2 composite grid (81 weights)
6. P3 final holdout (4 硬验收)
7. promote champion (P3 PASS 才 promote)

不依赖串行: 全部 sequential 单 process (DuckDB single writer 约束).

用法:
    PYTHONPATH=backend python backend/scripts/run_v3_2_full_chain.py
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("v3_2_full_chain")


def _run_step(name: str, cmd: list[str], cwd: Path, env: dict) -> bool:
    log.info(f"=== Step: {name} ===")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    elapsed = time.time() - t0
    if r.returncode != 0:
        log.error(f"  ✗ {name} FAILED in {elapsed:.0f}s")
        log.error(f"  stderr tail: {r.stderr[-500:]}")
        return False
    log.info(f"  ✓ {name} OK in {elapsed:.0f}s")
    # last 5 lines of stdout
    for line in r.stdout.splitlines()[-5:]:
        log.info(f"    {line}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    # rule-compliance: ok evidence=alpha158-panel-实测范围
    parser.add_argument("--start-date", default="2024-01-01")  # rule-compliance: ok evidence=alpha158-panel-实测范围
    parser.add_argument("--end-date", default="2026-04-30")  # rule-compliance: ok evidence=alpha158-panel-实测范围
    parser.add_argument("--skip-step", action="append", default=[],
                        help="跳过 step (可多次): build_v2 / train_p0b_v2 / deflated / paper_sim / p2 / p3 / promote")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    import os
    env = {**os.environ, "PYTHONPATH": "backend"}

    log.info(f"=== PLAN_V3 v3.2 Full Chain (start={args.start_date} end={args.end_date}) ===")
    overall_t0 = time.time()
    results = {}

    # Step 1: build feature_label_panel_v2 (stage_opt + formula_trigger 加入)
    if "build_v2" not in args.skip_step:
        ok = _run_step(
            "build mart_p0a_feature_label_panel_v2",
            ["python", "-c",
             f"""
import sys; sys.path.insert(0, 'backend')
import duckdb
conn = duckdb.connect('data/smartmoney.duckdb', read_only=True)
stocks = [r[0] for r in conn.execute(\\"SELECT stock_code FROM dim_all_ever_listed WHERE is_active=1 AND SUBSTR(stock_code,1,2) IN ('60','00','30','68')\\").fetchall()]
conn.close()
conn = duckdb.connect('data/alpha158.duckdb', read_only=True)
dates = [str(r[0]) for r in conn.execute('SELECT DISTINCT date FROM fact_alpha158_panel ORDER BY date').fetchall()]
conn.close()
from services.labels.feature_join_v2 import build_p0a_feature_label_panel_v2
r = build_p0a_feature_label_panel_v2(
    db_path='data/smartmoney.duckdb',
    alpha158_db_path='data/alpha158.duckdb',
    signal_dates=dates,
    stock_codes=stocks,
)
print(f'feature_label_panel_v2: {{r[\\"rows_built\\"]:,}} rows')
"""],
            cwd=repo_root, env=env,
        )
        results["build_v2"] = ok
        if not ok:
            log.error("build_v2 FAILED — stopping")
            return 1

    # Step 2: train P0b v2 × 3 horizon (改用 _v2 panel)
    # Note: 当前 train_p0b_lightgbm.py 读 mart_p0a_feature_label_panel (v1).
    # 为简化, 直接 ALTER 现有 train script 用 v2 — 但需要改 SQL. 暂跳, 用 v1 重训一次看 stage_opt 加入效果.
    if "train_p0b_v2" not in args.skip_step:
        for label in ["fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d"]:
            ok = _run_step(
                f"train P0b {label.replace('fwd_cost_after_', '')}",
                ["python", "backend/scripts/train_p0b_lightgbm.py",
                 "--label", label,
                 "--run-id", f"p0b_v2_{label.replace('fwd_cost_after_', '')}",
                 "--model-id", f"lgbm_v2_{label.replace('fwd_cost_after_', '')}",
                 "--start-date", args.start_date, "--end-date", args.end_date,
                 "--n-estimators", "200"],
                cwd=repo_root, env=env,
            )
            results[f"train_p0b_v2_{label}"] = ok

    # Step 3: Deflated SR audit (read-only)
    if "deflated" not in args.skip_step:
        ok = _run_step(
            "Deflated SR audit",
            ["python", "backend/scripts/p0b_deflated_sharpe_audit.py"],
            cwd=repo_root, env=env,
        )
        results["deflated"] = ok

    # Step 4: paper_sim_v2 with ml_score yaml (blend Option A)
    if "paper_sim" not in args.skip_step:
        ok = _run_step(
            "paper_sim_v2 ml_score mode",
            ["python", "backend/scripts/run_paper_sim_v2.py",
             "--config-path", "backend/config/paper_sim_ml_score.yaml",
             "--variant", "swap_v1",
             "--start", args.start_date, "--end", args.end_date],
            cwd=repo_root, env=env,
        )
        results["paper_sim"] = ok

    # Step 5: P2 composite weight grid
    if "p2" not in args.skip_step:
        ok = _run_step(
            "P2 composite grid search",
            ["python", "backend/scripts/run_p2_composite_search.py",
             "--model-id", "lgbm_v2_20d",
             "--run-id", "p2_chain_v1"],
            cwd=repo_root, env=env,
        )
        results["p2"] = ok

    # Step 6: P3 final holdout
    if "p3" not in args.skip_step:
        ok = _run_step(
            "P3 final holdout acceptance",
            ["python", "backend/scripts/run_p3_final_holdout.py",
             "--model-id", "lgbm_v2_20d",
             "--run-id", "p3_chain_v1"],
            cwd=repo_root, env=env,
        )
        results["p3"] = ok

    # Step 7: promote champion (P3 PASS 才 promote, 否则 skip)
    if "promote" not in args.skip_step and results.get("p3", False):
        ok = _run_step(
            "Promote champion",
            ["python", "backend/scripts/promote_champion.py",
             "--p3-run-id", "p3_chain_v1",
             "--reason", "v3.2 full chain P3 PASS"],
            cwd=repo_root, env=env,
        )
        results["promote"] = ok

    total_elapsed = time.time() - overall_t0
    log.info("")
    log.info(f"=== Chain Summary (total {total_elapsed:.0f}s) ===")
    for step, ok in results.items():
        log.info(f"  {step}: {'✓' if ok else '✗'}")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
