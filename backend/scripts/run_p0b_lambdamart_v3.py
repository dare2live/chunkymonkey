#!/usr/bin/env python3
"""P0b LambdaMART walk-forward CLI — v3 panel (Codex 7-day plan Day 7 架构对照).

读 mart_p0a_feature_label_panel_v3 → train_lambdamart_walkforward → 写
mart_p0b_oos_predictions (model_id='lambdamart_v3_*') + mart_p0b_walkforward_eval.

跟 train_p0b_lightgbm.py 同结构, 区别在 objective='lambdarank' (pairwise NDCG).

用法:
    PYTHONPATH=backend python backend/scripts/run_p0b_lambdamart_v3.py \\
        --label fwd_cost_after_20d \\
        --run-id p0b_lambdamart_v3_20d
"""
from __future__ import annotations

import argparse
import logging
import math
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from services.db import DB_PATH
from services.duck_adapter import connect as duck_connect
from services.ml_ranking.ddl import create_p0b_ddl
from services.ml_ranking.lambdamart_walkforward import (
    LambdaMARTWalkForwardConfig,
    train_lambdamart_walkforward,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("p0b_lambdamart")


_META_FIELDS = {
    "stock_code", "signal_date", "entry_date", "unable_at_entry",
    "fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d",
    "feature_version", "built_at", "industry_pit_confidence",
    # Codex adc5b44520 leakage cols
    "inst_quality_wavg", "inst_quality_max", "inst_total_holding_ratio",
    "inst_holder_cnt", "top_inst_holding_ratio",
    "sector_ret_5d", "sector_ret_20d", "sector_ret_60d",
    "sector_excess_20d", "sector_excess_60d",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="P0b LambdaMART pairwise walk-forward (Day 7)")
    parser.add_argument("--label", default="fwd_cost_after_20d",
                        choices=["fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d"])
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--model-id", default=None)
    parser.add_argument("--min-train-months", type=int, default=12)
    parser.add_argument("--forward-months", type=int, default=1)
    parser.add_argument("--n-estimators", type=int, default=200)
    parser.add_argument("--learning-rate", type=float, default=0.05)
    parser.add_argument("--num-leaves", type=int, default=31)
    parser.add_argument("--label-gain-max", type=int, default=20)
    parser.add_argument("--feature-panel", default="mart_p0a_feature_label_panel_v3")
    parser.add_argument("--start-date", default="2024-01-01")  # rule-compliance: ok evidence=alpha158-panel-实测范围
    parser.add_argument("--end-date", default="2026-04-30")    # rule-compliance: ok evidence=alpha158-panel-实测范围
    args = parser.parse_args()

    run_id = args.run_id or f"p0b_lambdamart_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"
    model_id = args.model_id or f"lambdamart_v3_{args.label.replace('fwd_cost_after_', '')}"
    log.info(f"run_id={run_id}, model_id={model_id}, label={args.label}")

    conn = duck_connect(str(DB_PATH))
    try:
        create_p0b_ddl(conn)

        log.info(f"Loading DataFrame from {args.feature_panel} ...")
        df = conn._con.execute(
            f"SELECT * FROM {args.feature_panel} ORDER BY signal_date, stock_code"
        ).fetchdf()
        log.info(f"Loaded {len(df):,} rows × {len(df.columns)} cols")

        df = df[df["signal_date"] >= pd.to_datetime(args.start_date)]
        df = df[df["signal_date"] <= pd.to_datetime(args.end_date)]
        df = df[df[args.label].notna()].copy()
        log.info(f"After filter: {len(df):,} rows")

        feature_columns = [c for c in df.columns if c not in _META_FIELDS
                           and pd.api.types.is_numeric_dtype(df[c])]
        log.info(f"feature_columns ({len(feature_columns)}): {feature_columns[:10]}...")

        rows = df.to_dict("records")

        cfg = LambdaMARTWalkForwardConfig(
            label_field=args.label,
            min_train_months=args.min_train_months,
            forward_months=args.forward_months,
            n_estimators=args.n_estimators,
            learning_rate=args.learning_rate,
            num_leaves=args.num_leaves,
            label_gain_max=args.label_gain_max,
            feature_columns=feature_columns,
        )
        log.info("Running LambdaMART walk-forward ...")
        result = train_lambdamart_walkforward(rows, cfg)

        if not result.windows:
            log.error("No windows produced (insufficient data)")
            return 1

        log.info("")
        log.info(f"=== LambdaMART {model_id} OOS Results ===")
        log.info(f"  n_windows: {result.n_windows}")
        log.info(f"  overall RankIC: {result.overall_rank_ic.mean_rank_ic:.4f}")
        log.info(f"  overall IC IR: {result.overall_rank_ic.ic_ir:.4f}")
        log.info(f"  n_dates: {result.overall_rank_ic.n_dates}")
        log.info(f"  Gate (RankIC ≥ 0.03 + n_dates ≥ 30): "
                 f"{'PASS' if result.passed_gate else 'FAIL'}")

        # Write predictions to mart_p0b_oos_predictions
        built_at = datetime.now(UTC).isoformat(timespec="seconds")
        ranger = []
        for win in result.windows:
            for p in win.test_predictions:
                if p.get("score") is None:
                    continue
                ranger.append([
                    p["stock_code"], p["signal_date"],
                    p["score"],
                    p.get("fwd_cost_after_5d"),
                    p.get(args.label) if args.label == "fwd_cost_after_10d" else None,
                    p.get("fwd_cost_after_20d"),
                    model_id, "v3.lambdamart", "p0a_v3", "v1",
                    "expanding_monthly",
                    win.train_start, win.train_end, win.test_start, win.test_end,
                    False, built_at,
                ])
        # Use register DataFrame approach (faster than executemany)
        # (module-level `import pandas as pd` line 27, 不要 shadow)
        if ranger:
            df_pred = pd.DataFrame(ranger, columns=[
                "stock_code", "signal_date", "score",
                "fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d",
                "model_id", "model_version", "feature_version", "label_version",
                "walk_forward_mode",
                "train_start", "train_end", "test_start", "test_end",
                "is_final_holdout", "built_at",
            ])
            conn._con.register("df_pred_lm", df_pred)
            conn.execute(f"DELETE FROM mart_p0b_oos_predictions WHERE model_id = '{model_id}'")
            conn.execute("""
                INSERT INTO mart_p0b_oos_predictions
                (stock_code, signal_date, score,
                 fwd_cost_after_5d, fwd_cost_after_10d, fwd_cost_after_20d,
                 model_id, model_version, feature_version, label_version,
                 walk_forward_mode,
                 train_start, train_end, test_start, test_end,
                 is_final_holdout, built_at)
                SELECT * FROM df_pred_lm
            """)
            conn._con.unregister("df_pred_lm")
        log.info(f"Wrote {len(ranger):,} predictions to mart_p0b_oos_predictions")

        # Walk-forward eval rows
        for i, win in enumerate(result.windows):
            conn.execute(
                """INSERT OR REPLACE INTO mart_p0b_walkforward_eval
                   (run_id, window_idx, model_id, model_version, feature_version, label_version,
                    walk_forward_mode, train_start, train_end, test_start, test_end,
                    n_train, n_test, rank_ic, rank_ic_ir, built_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    run_id, i, model_id, "v3.lambdamart", "p0a_v3", "v1",
                    "expanding_monthly", win.train_start, win.train_end,
                    win.test_start, win.test_end,
                    win.n_train, win.n_test,
                    None if math.isnan(win.rank_ic) else win.rank_ic,
                    None if math.isnan(win.rank_ic_ir) else win.rank_ic_ir,
                    built_at,
                ]
            )

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
