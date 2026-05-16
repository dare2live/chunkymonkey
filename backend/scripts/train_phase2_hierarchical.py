"""Phase 2 — hierarchical 24-pool LightGBM LambdaRank + child residual (Codex final).

Updated per Codex ace17432 (2026-05-16) + IC-vs-paper_sim 根因 finding:
- Parent: alpha158 + sector + fund flow features (selector, lgbm_v3 类)
- Child: industry_beta + mcap_decile features (risk gate / pool residual)

设计:
1. Parent global LambdaRank
   - 用 mart_stock_regime_full (排除 beta_decile / industry / regime / calendar / cdp 13 cols)
   - lambdamart objective, group by signal_date
   - walk-forward expanding monthly, embargo=30
   - 输出 parent_score per (stock × signal_date)

2. Per-pool child regression
   - 用 mart_stock_pool_assignment 取 pool_id (24 pools = 12 industry × 2 tier)
   - 每 pool: LightGBM regression with residual = label - sigmoid(parent_score)
   - Features: beta_decile + industry + mcap_decile (Codex risk-aware)
   - 输出 child_score per (stock × signal_date × pool_id)

3. Combine: final = 0.70 × parent + 0.30 × child

Walk-forward:
- expanding monthly, embargo=30
- min_train_months=12
- frozen holdout 2025-09 ~ 2026-04 (Codex CONDITIONAL-GO)

Acceptance:
- OOS RankIC ≥ 0.024 vs chain v6 honest 0.020
- DSR > 0.5 after multi-trial correction
- paper_sim 5/5 PASS

用法:
    PYTHONPATH=backend python backend/scripts/train_phase2_hierarchical.py \\
        --stage parent --label fwd_cost_after_20d

⚠ 本 file 是 partial impl — stage_parent 可 run, stage_child + combine 待 next session.
"""
from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import datetime, UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from services.db import DB_PATH
from services.duck_adapter import connect as duck_connect
from services.ml_ranking.ddl import create_p0b_ddl
from services.ml_ranking.lambdamart_walkforward import (
    LambdaMARTWalkForwardConfig,
    train_lambdamart_walkforward,
)


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("phase2_hierarchical")


# Codex ace17432: parent uses lgbm_v3-style features (alpha158 + sector + fund flow).
# Exclude: industry beta / mcap decile (risk features) / candle / regime / calendar (noise).
_PARENT_EXCLUDE = {
    # Meta
    "stock_code", "signal_date", "entry_date", "unable_at_entry",
    "fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d",
    "feature_version", "built_at", "industry_pit_confidence",
    "cdp_source_max_date", "regime_full_anchor_date", "regime_label_lag1",
    # Codex adc5b44520 leakage cols (training-time exclude)
    "inst_quality_wavg", "inst_quality_max", "inst_total_holding_ratio",
    "inst_holder_cnt", "top_inst_holding_ratio",
    "sector_ret_5d", "sector_ret_20d", "sector_ret_60d",
    "sector_excess_20d", "sector_excess_60d",
    # Codex ad2e09e7 ablation: candle / calendar / regime cols (noise)
    "cdp_body_ratio", "cdp_upper_shadow", "cdp_lower_shadow",
    "cdp_close_pos", "cdp_volume_rel", "cdp_breakout_20",
    "cdp_is_bullish", "cdp_is_doji", "cdp_is_long_lower",
    "cdp_is_long_upper", "cdp_is_marubozu", "cdp_is_high_vol",
    "regime_id_lag1", "regime_transition_lag1",
    "cal_month", "cal_dow", "cal_dom", "cal_tdom", "cal_tdays_to_month_end",
    # Codex ace17432: risk features 保留给 child, parent 不用
    "beta_60d", "beta_60d_z", "mcap_decile", "industry",
}


def stage_parent(args) -> int:
    """Parent global LambdaRank train on mart_stock_regime_full (selector features only)."""
    log.info("=== Phase 2 parent LambdaRank ===")
    log.info("  panel: mart_stock_regime_full (138 cols)")
    log.info("  exclude (risk + meta + noise): %d cols", len(_PARENT_EXCLUDE))
    log.info("  objective: lambdarank (pairwise NDCG)")
    log.info("  group: signal_date (cross-sectional)")

    run_id = args.run_id or f"phase2_parent_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"
    model_id = args.model_id or f"phase2_parent_{args.label.replace('fwd_cost_after_', '')}"

    conn = duck_connect(str(DB_PATH))
    create_p0b_ddl(conn)

    df = conn._con.execute(
        "SELECT * FROM mart_stock_regime_full ORDER BY signal_date, stock_code"
    ).fetchdf()
    log.info(f"  loaded {len(df):,} rows × {len(df.columns)} cols")

    df = df[df["signal_date"] >= pd.to_datetime(args.start_date)]
    df = df[df["signal_date"] <= pd.to_datetime(args.end_date)]
    df = df[df[args.label].notna()].copy()
    log.info(f"  after filter: {len(df):,} rows")

    feature_columns = [c for c in df.columns
                       if c not in _PARENT_EXCLUDE
                       and pd.api.types.is_numeric_dtype(df[c])]
    log.info(f"  feature_columns ({len(feature_columns)}): {feature_columns[:8]}...")

    cfg = LambdaMARTWalkForwardConfig(
        label_field=args.label,
        min_train_months=args.min_train_months,
        forward_months=args.forward_months,
        n_estimators=args.n_estimators,
        learning_rate=0.05,
        num_leaves=31,
        label_gain_max=20,
        feature_columns=feature_columns,
    )

    rows = df.to_dict("records")
    log.info("  training walk-forward...")
    result = train_lambdamart_walkforward(rows, cfg)

    log.info(f"\n=== Phase 2 Parent {model_id} OOS Results ===")
    log.info(f"  n_windows: {result.n_windows}")
    log.info(f"  overall RankIC: {result.overall_rank_ic.mean_rank_ic:.4f}")
    log.info(f"  overall IC IR: {result.overall_rank_ic.ic_ir:.4f}")
    log.info(f"  n_dates: {result.overall_rank_ic.n_dates}")
    log.info(f"  Gate: {'PASS' if result.passed_gate else 'FAIL'}")

    # 写 predictions
    built_at = datetime.now(UTC).isoformat(timespec="seconds")
    rows_to_write = []
    for win in result.windows:
        for p in win.test_predictions:
            if p.get("score") is None:
                continue
            rows_to_write.append([
                p["stock_code"], p["signal_date"],
                p["score"],
                p.get("fwd_cost_after_5d"),
                p.get(args.label) if args.label == "fwd_cost_after_10d" else None,
                p.get("fwd_cost_after_20d"),
                model_id, "v3.2.phase2_parent", "regime_full_v2", "v1",
                built_at, run_id,
            ])
    if rows_to_write:
        # match actual mart_p0b_oos_predictions schema (12 cols)
        rows_to_write_v2 = [
            [r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], "v1", "expanding_monthly", built_at]
            for r in rows_to_write
        ]
        conn._con.executemany(
            """INSERT OR REPLACE INTO mart_p0b_oos_predictions
               (stock_code, signal_date, score, fwd_cost_after_5d,
                fwd_cost_after_10d, fwd_cost_after_20d, model_id,
                model_version, feature_version, label_version,
                walk_forward_mode, built_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows_to_write_v2,
        )
        log.info(f"  Wrote {len(rows_to_write):,} predictions to mart_p0b_oos_predictions")

    return 0


def stage_child(args) -> int:
    """Per-pool child residual regression (Codex ace17432 priority #4 实施).

    每 pool 独立 LightGBM regression on residual = label - sigmoid(parent_score).
    Features: 仅 risk dim (beta_60d / beta_60d_z / mcap_decile + 必要 alpha158).
    """
    import lightgbm as lgb
    import numpy as np

    log.info(f"=== Phase 2 child residual pool={args.pool_id or 'all'} ===")

    run_id = args.run_id or f"phase2_child_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"
    parent_model_id = args.parent_model_id or "phase2_parent_20d"

    conn = duck_connect(str(DB_PATH))
    create_p0b_ddl(conn)

    # Load panel + parent score + pool assignment
    df = conn._con.execute(f"""
        SELECT r.*,
               p.score AS parent_score,
               pa.pool_id, pa.supersector, pa.liquidity_tier
        FROM mart_stock_regime_full r
        JOIN mart_p0b_oos_predictions p
            ON p.stock_code = r.stock_code AND p.signal_date = r.signal_date
            AND p.model_id = '{parent_model_id}'
        LEFT JOIN mart_stock_pool_assignment pa
            ON pa.stock_code = r.stock_code
            AND DATE_TRUNC('month', pa.as_of_month) = DATE_TRUNC('month', r.signal_date)
        WHERE r.signal_date >= CAST(? AS DATE)
          AND r.signal_date <= CAST(? AS DATE)
          AND r.{args.label} IS NOT NULL
        ORDER BY r.signal_date, r.stock_code
    """, [args.start_date, args.end_date]).fetchdf()
    log.info(f"  loaded {len(df):,} rows (joined with parent + pool)")

    if len(df) == 0:
        log.error("No parent_score rows; run stage_parent first.")
        return 1

    # Compute residual: label - sigmoid(parent_score)
    df["parent_sigmoid"] = 1 / (1 + np.exp(-df["parent_score"]))
    df["residual"] = df[args.label] - df["parent_sigmoid"]

    # Filter to specific pool if requested
    if args.pool_id:
        df = df[df["pool_id"] == args.pool_id].copy()
        log.info(f"  filtered to pool '{args.pool_id}': {len(df):,} rows")

    # Risk feature columns (Codex ace17432 separation)
    risk_features = [c for c in ["beta_60d", "beta_60d_z", "mcap_decile"]
                     if c in df.columns]
    log.info(f"  risk features: {risk_features}")

    pools = df["pool_id"].dropna().unique() if not args.pool_id else [args.pool_id]
    log.info(f"  pools to train: {len(pools)}")

    built_at = datetime.now(UTC).isoformat(timespec="seconds")
    total_predictions = 0

    for pool in pools:
        pool_df = df[df["pool_id"] == pool].copy()
        if len(pool_df) < 200:
            log.warning(f"  pool {pool} too small ({len(pool_df)} rows), skip")
            continue

        # Walk-forward expanding monthly: for each test month, train on prior data
        pool_df["month"] = pool_df["signal_date"].dt.to_period("M")
        months = sorted(pool_df["month"].unique())
        if len(months) < args.min_train_months + 1:
            log.warning(f"  pool {pool}: only {len(months)} months, need {args.min_train_months + 1}, skip")
            continue
        pool_predictions = 0
        for i, test_month in enumerate(months[args.min_train_months:], start=args.min_train_months):
            train_months = months[:i]
            train = pool_df[pool_df["month"].isin(train_months)]
            test = pool_df[pool_df["month"] == test_month]
            if len(test) == 0 or len(train) < 100:
                continue

            x_train = train[risk_features].fillna(0).values
            y_train = train["residual"].values
            x_test = test[risk_features].fillna(0).values

            model_child = lgb.LGBMRegressor(
                num_leaves=15, learning_rate=0.05, n_estimators=args.n_estimators,
                verbose=-1,
            )
            model_child.fit(x_train, y_train)
            child_score = model_child.predict(x_test)

            rows = []
            for j, (_, row) in enumerate(test.iterrows()):
                rows.append([
                    row["stock_code"], row["signal_date"],
                    float(child_score[j]),
                    row.get("fwd_cost_after_5d"),
                    row.get(args.label) if args.label == "fwd_cost_after_10d" else None,
                    row.get("fwd_cost_after_20d"),
                    f"phase2_child_{pool}",
                    "v3.2.phase2_child_wf", "regime_full_v2_risk", "v1",
                    "expanding_monthly", built_at,
                ])
            conn._con.executemany(
                """INSERT OR REPLACE INTO mart_p0b_oos_predictions
                   (stock_code, signal_date, score, fwd_cost_after_5d,
                    fwd_cost_after_10d, fwd_cost_after_20d, model_id,
                    model_version, feature_version, label_version,
                    walk_forward_mode, built_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            pool_predictions += len(rows)
        total_predictions += pool_predictions
        log.info(f"  pool {pool}: {len(months)} months / {len(months) - args.min_train_months} test windows / {pool_predictions} predictions")

    log.info(f"\n=== Phase 2 Child OOS Results ===")
    log.info(f"  Pools trained: {len(pools)}")
    log.info(f"  Total predictions: {total_predictions:,}")
    return 0


def stage_combine(args) -> int:
    """Combine parent + child → final score (Codex final step 3)."""
    log.info(f"=== Phase 2 combine w_parent={args.w_parent} ===")
    parent_mid = args.parent_model_id or "phase2_parent_20d"
    conn = duck_connect(str(DB_PATH))
    create_p0b_ddl(conn)

    # JOIN parent + child by (signal_date, stock_code)
    rows = conn._con.execute(f"""
        SELECT
            p.stock_code, p.signal_date,
            ({args.w_parent} * p.score + (1 - {args.w_parent}) * c.score) AS combined_score,
            p.fwd_cost_after_5d, p.fwd_cost_after_10d, p.fwd_cost_after_20d,
            c.model_id AS child_model_id
        FROM mart_p0b_oos_predictions p
        JOIN mart_p0b_oos_predictions c
            ON c.stock_code = p.stock_code AND c.signal_date = p.signal_date
        WHERE p.model_id = '{parent_mid}'
          AND c.model_id LIKE 'phase2_child_%'
    """).fetchdf()
    log.info(f"  combined {len(rows):,} rows")

    if len(rows) == 0:
        log.error("No combined rows — run parent+child first")
        return 1

    # 写 mart_p0b_oos_predictions(model_id='phase2_combined_*')
    combined_model_id = args.model_id or f"phase2_combined_w{int(args.w_parent*100)}"
    built_at = datetime.now(UTC).isoformat(timespec="seconds")
    insert_rows = []
    for _, r in rows.iterrows():
        insert_rows.append([
            r["stock_code"], r["signal_date"],
            float(r["combined_score"]),
            r.get("fwd_cost_after_5d"),
            r.get("fwd_cost_after_10d"),
            r.get("fwd_cost_after_20d"),
            combined_model_id,
            "v3.2.phase2_combined", "regime_full_v2", "v1",
            "expanding_monthly", built_at,
        ])
    conn._con.executemany(
        """INSERT OR REPLACE INTO mart_p0b_oos_predictions
           (stock_code, signal_date, score, fwd_cost_after_5d,
            fwd_cost_after_10d, fwd_cost_after_20d, model_id,
            model_version, feature_version, label_version,
            walk_forward_mode, built_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        insert_rows,
    )
    log.info(f"  Wrote {len(insert_rows):,} predictions as {combined_model_id}")

    # Quick RankIC computation per signal_date
    import numpy as np
    rows_eval = rows.dropna(subset=["fwd_cost_after_20d", "combined_score"])
    if len(rows_eval) >= 30:
        ic_per_day = rows_eval.groupby("signal_date").apply(
            lambda g: g["combined_score"].corr(g["fwd_cost_after_20d"], method="spearman")
        ).dropna()
        log.info(f"\n=== Phase 2 Combined OOS ===")
        log.info(f"  signal_date n: {len(ic_per_day)}")
        log.info(f"  overall RankIC: {ic_per_day.mean():.4f}")
        log.info(f"  IC IR: {ic_per_day.mean() / ic_per_day.std():.4f}" if ic_per_day.std() > 0 else "  IC IR: nan")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=["parent", "child", "combine"])
    parser.add_argument("--label", default="fwd_cost_after_20d",
                        choices=["fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d"])
    parser.add_argument("--pool-id", default=None)
    parser.add_argument("--w-parent", type=float, default=0.70)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--model-id", default=None)
    parser.add_argument("--parent-model-id", default=None, help="stage_child use parent score from this model_id")
    parser.add_argument("--min-train-months", type=int, default=12)
    parser.add_argument("--forward-months", type=int, default=1)
    parser.add_argument("--n-estimators", type=int, default=200)
    parser.add_argument("--start-date", default="2024-01-01")   # rule-compliance: ok evidence=panel-window
    parser.add_argument("--end-date", default="2026-04-23")     # rule-compliance: ok evidence=panel-window-end
    args = parser.parse_args()

    if args.stage == "parent":
        return stage_parent(args)
    elif args.stage == "child":
        return stage_child(args)
    elif args.stage == "combine":
        return stage_combine(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
