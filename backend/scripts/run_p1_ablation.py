#!/usr/bin/env python3
"""P1 ablation suite CLI — alpha158/risk/financial/events drop-one + only-one.

读 mart_p0a_feature_label_panel → run_ablation_suite → 入库
mart_p1_ablation_result + stdout summary.

DataFrame-based (跟 train_p0b v3 一致): conn._con.execute().fetchdf() 跳 list[dict].

用法:
    PYTHONPATH=backend python backend/scripts/run_p1_ablation.py \
        --label fwd_cost_after_10d \
        --n-estimators 50 \
        --run-id p1_ablation_baseline \
        --start-date 2024-01-01 --end-date 2026-04-30
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import DB_PATH
from services.duck_adapter import connect as duck_connect
from services.ml_ranking.ablation import DEFAULT_GROUPS, run_ablation_suite
from services.ml_ranking.lightgbm_walkforward import LightGBMWalkForwardConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("p1_ablation")


ABLATION_RESULT_DDL = """
CREATE TABLE IF NOT EXISTS mart_p1_ablation_result (
    run_id TEXT NOT NULL,
    experiment_name TEXT NOT NULL,
    feature_groups_used TEXT NOT NULL,
    n_features INTEGER,
    n_windows INTEGER,
    rank_ic DOUBLE,
    rank_ic_ir DOUBLE,
    delta_vs_baseline DOUBLE,
    n_oos_dates INTEGER,
    label_field TEXT,
    n_estimators INTEGER,
    learning_rate DOUBLE,
    num_leaves INTEGER,
    built_at TEXT,
    PRIMARY KEY (run_id, experiment_name)
);
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="P1 ablation suite CLI")
    parser.add_argument("--label", default="fwd_cost_after_10d",
                        choices=["fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d"])
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--n-estimators", type=int, default=50)
    parser.add_argument("--learning-rate", type=float, default=0.05)
    parser.add_argument("--num-leaves", type=int, default=31)
    parser.add_argument("--min-train-months", type=int, default=None)
    parser.add_argument("--forward-months", type=int, default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    args = parser.parse_args()

    run_id = args.run_id or f"p1_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"
    log.info(f"run_id={run_id}, label={args.label}")

    conn = duck_connect(str(DB_PATH))
    try:
        conn.execute(ABLATION_RESULT_DDL)
        log.info("Loading DataFrame from mart_p0a_feature_label_panel...")
        # DataFrame fast path (跟 train_p0b v3 一致): _con.execute().fetchdf() 跳 dict
        import pandas as pd
        df = conn._con.execute(
            "SELECT * FROM mart_p0a_feature_label_panel ORDER BY signal_date, stock_code"
        ).fetchdf()
        log.info(f"Loaded {len(df):,} rows × {len(df.columns)} cols")
        if args.start_date:
            df = df[df["signal_date"] >= pd.to_datetime(args.start_date)]
        if args.end_date:
            df = df[df["signal_date"] <= pd.to_datetime(args.end_date)]
        df = df[df[args.label].notna()].copy()
        log.info(f"After filters: {len(df):,} rows")

        # Convert DataFrame to list[dict] via to_dict('records') — 仍要给 ablation API
        # (TODO 后续 ablation 也改成 DataFrame-based; 当前 to_dict 用 numpy 后端, 比 cursor.fetchall() 快约 3-5×)
        rows = df.to_dict("records")
        log.info(f"Converted to {len(rows):,} dicts")

        cfg = LightGBMWalkForwardConfig(
            label_field=args.label,
            n_estimators=args.n_estimators,
            learning_rate=args.learning_rate,
            num_leaves=args.num_leaves,
            min_train_months=args.min_train_months,
            forward_months=args.forward_months,
        )
        log.info(f"Running ablation suite with {len(DEFAULT_GROUPS)} groups...")
        suite = run_ablation_suite(rows, base_cfg=cfg)
        summary = suite.summary()

        # Write results
        built_at = datetime.now(UTC).isoformat(timespec="seconds")
        for name, s in summary.items():
            # find AblationResult for this name (baseline + drop_one + add_one)
            target = None
            if name == suite.baseline.experiment_name:
                target = suite.baseline
            else:
                for r in suite.drop_one + suite.add_one:
                    if r.experiment_name == name:
                        target = r
                        break
            if not target:
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO mart_p1_ablation_result
                (run_id, experiment_name, feature_groups_used, n_features, n_windows,
                 rank_ic, rank_ic_ir, delta_vs_baseline, n_oos_dates, label_field,
                 n_estimators, learning_rate, num_leaves, built_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run_id, name, json.dumps(target.feature_groups_used),
                    s["n_features"], s["n_windows"],
                    None if s["rank_ic"] != s["rank_ic"] else s["rank_ic"],
                    None if s["ic_ir"] != s["ic_ir"] else s["ic_ir"],
                    s["delta_vs_baseline"],
                    target.walk_forward_result.overall_rank_ic.n_dates,
                    args.label, args.n_estimators, args.learning_rate, args.num_leaves,
                    built_at,
                ]
            )

        log.info("")
        log.info("=== Ablation Summary (vs baseline) ===")
        log.info(f"{'experiment':35s} {'n_feat':>7s} {'RankIC':>8s} {'IC IR':>8s} {'Δbase':>8s}")
        for name, s in summary.items():
            delta = s["delta_vs_baseline"]
            sign = "+" if delta >= 0 else ""
            log.info(
                f"{name:35s} {s['n_features']:>7d} {s['rank_ic']:>8.4f} {s['ic_ir']:>8.4f} {sign}{delta:.4f}"
            )
        log.info("")
        log.info(f"All results written to mart_p1_ablation_result (run_id={run_id})")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
