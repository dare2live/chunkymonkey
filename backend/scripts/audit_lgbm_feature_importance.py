#!/usr/bin/env python3
"""Phase 4 alpha 根因 audit: governance v1 lgbm feature importance ranking.

按 Codex Q1 alpha 根因路径 #2 (feature engineering 准备):
- 跑 train_p0b_lightgbm.py governance v1 (lgbm_20260517_governance_v1_20d) 同 hyperparams
- 输出 feature importance gain ranking, 标识 top-N 重要 + bottom-N 噪音 features
- 帮决定: 哪些 features 真带 alpha, 哪些可删, 哪些应补充

执行:
    PYTHONPATH=backend python backend/scripts/audit_lgbm_feature_importance.py \
        --top-n 20 --bottom-n 20

输出:
- top-N 高 importance features (governance v1 真实 alpha 信号)
- bottom-N 低 importance features (噪音, 候选删除)
- importance distribution 直方图 (concentration vs broad)

注意: 此 audit read-only DuckDB, 不写 mart. 跟 train_p0b 并存兼容.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import duckdb

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("audit_lgbm_importance")

REPO_ROOT = Path(__file__).resolve().parents[2]
SMART_DB = REPO_ROOT / "data" / "smartmoney.duckdb"


def main() -> int:
    parser = argparse.ArgumentParser(description="LightGBM feature importance audit (governance v1)")
    parser.add_argument("--feature-panel", default="mart_p0a_feature_label_panel_v3",
                        help="读哪张 panel")
    parser.add_argument("--label", default="fwd_cost_after_20d",
                        choices=["fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d"])
    parser.add_argument("--top-n", type=int, default=20,
                        help="show top N 重要 features")
    parser.add_argument("--bottom-n", type=int, default=20,
                        help="show bottom N 噪音 features")
    parser.add_argument("--n-estimators", type=int, default=300,
                        help="quick fit n_estimators (audit 用 smoke 数值)")
    args = parser.parse_args()

    import lightgbm as lgb
    import pandas as pd
    import numpy as np

    log.info(f"=== LightGBM feature importance audit ({args.label}, n_est={args.n_estimators}) ===")
    log.info(f"  panel: {args.feature_panel}")

    # Read panel (governance v1, 必含 label != NULL)
    conn = duckdb.connect(str(SMART_DB), read_only=True)
    df = conn.execute(
        f"SELECT * FROM {args.feature_panel} WHERE {args.label} IS NOT NULL"
    ).fetchdf()
    conn.close()
    log.info(f"  rows: {len(df):,} × cols: {len(df.columns)}")

    # Feature columns (exclude label / metadata)
    META = {"stock_code", "signal_date", "built_at", "feature_version", "label_version",
            "fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d",
            "industry_pit_confidence", "industry_pit_l1_name", "industry_pit_l2_name"}
    feature_cols = [c for c in df.columns if c not in META and pd.api.types.is_numeric_dtype(df[c])]
    log.info(f"  feature_cols: {len(feature_cols)}")
    # Sample: print first 5
    log.info(f"  samples: {feature_cols[:5]}...")

    X = df[feature_cols].fillna(0).values
    y = df[args.label].values

    # Quick fit (single LightGBM, governance v1 default params, no walk-forward)
    log.info(f"Fitting LightGBM (no walk-forward, importance only)...")
    model = lgb.LGBMRegressor(
        n_estimators=args.n_estimators,
        learning_rate=0.05,
        num_leaves=31,
        random_state=42,
        verbose=-1,
    )
    model.fit(X, y, feature_name=feature_cols)

    # Importance ranking
    importance = pd.DataFrame({
        "feature": feature_cols,
        "gain": model.booster_.feature_importance(importance_type="gain"),
        "split": model.booster_.feature_importance(importance_type="split"),
    }).sort_values("gain", ascending=False).reset_index(drop=True)

    total_gain = importance["gain"].sum()
    importance["gain_pct"] = importance["gain"] / total_gain * 100

    log.info(f"\n=== TOP {args.top_n} features by gain ===")
    log.info(f"{'#':>3} {'feature':40s} {'gain':>12s} {'gain_pct':>8s} {'split':>8s}")
    for i, row in importance.head(args.top_n).iterrows():
        log.info(f"{i+1:>3} {row['feature']:40s} {row['gain']:>12.0f} {row['gain_pct']:>7.2f}% {row['split']:>8.0f}")

    log.info(f"\n=== BOTTOM {args.bottom_n} features by gain (noise 候选) ===")
    for i, row in importance.tail(args.bottom_n).iloc[::-1].iterrows():
        rank = len(importance) - importance.tail(args.bottom_n).index.get_loc(i)
        log.info(f"{rank:>3} {row['feature']:40s} {row['gain']:>12.0f} {row['gain_pct']:>7.4f}% {row['split']:>8.0f}")

    # Cumulative gain stats
    cum = importance["gain_pct"].cumsum()
    log.info(f"\n=== Importance concentration ===")
    for pct in [50, 75, 90, 95, 99]:
        n_features = (cum < pct).sum() + 1
        log.info(f"  top {n_features} features account for {pct}% gain")

    log.info(f"\n=== Phase 4 alpha 根因建议 ===")
    log.info(f"  - 0 gain features (完全无用): {(importance['gain'] == 0).sum()}")
    log.info(f"  - gain < 0.1% features (噪音候选): {(importance['gain_pct'] < 0.1).sum()}")
    log.info(f"  - 集中度 top 10 features 占: {importance.head(10)['gain_pct'].sum():.1f}%")
    log.info(f"  - 如 top 10 占 > 80% → 大部分 features 噪音, 建议精简")
    log.info(f"  - 如 top 10 占 < 30% → broad alpha 但每 feature 信号弱, 建议加新 alpha (industry beta / time-of-month / sector)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
