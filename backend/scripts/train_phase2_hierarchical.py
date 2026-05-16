"""Phase 2 — hierarchical 24-pool LightGBM LambdaRank + child residual (Codex final).

设计 (Codex 2026-05-15 final 方案):
1. Parent global LightGBM LambdaRank
   - 用 mart_stock_regime_full (135 cols, all stocks)
   - objective='lambdarank' pairwise NDCG
   - group by signal_date (cross-section ranking)
   - walk-forward expanding monthly, embargo=30
   - 输出 parent_score per (stock × signal_date)

2. Per-pool child residual model (24 pools)
   - 用 mart_stock_pool_assignment 取 pool_id
   - 每 pool 独立 LightGBM regression on (parent_score 残差, 同 features)
   - 输出 child_score per (stock × signal_date × pool_id)

3. Final score = 0.70 × parent_score + 0.30 × child_score
   - 写 mart_p0b_oos_predictions WITH model_id='phase2_hierarchical_*'

Phase 2 acceptance:
- OOS RankIC ≥ 0.024 (vs chain v6 honest 0.020 = +0.004 真增益)
- Net ann_ret ≥ 18%
- Max_dd ≥ -25%
- Deflated Sharpe > 0.50
- Per-fold top-feature overlap > 55%

Walk-forward:
- expanding monthly
- embargo=30
- min_train_months=12
- 4 cutoffs (or更多): 2025-Q1/Q2/Q3/Q4

用法:
    # Parent train
    PYTHONPATH=backend python backend/scripts/train_phase2_hierarchical.py \\
        --stage parent --label fwd_cost_after_20d

    # Child residual (per pool)
    PYTHONPATH=backend python backend/scripts/train_phase2_hierarchical.py \\
        --stage child --pool-id "装备制造_high"

    # Final score combine
    PYTHONPATH=backend python backend/scripts/train_phase2_hierarchical.py \\
        --stage combine --w-parent 0.70

⚠ 本 file 是 skeleton, Phase 2 实施待 next session (multi-day work).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("phase2_hierarchical")


def stage_parent(args) -> int:
    """Parent global LambdaRank train (Codex final step 1)."""
    log.info("=== Phase 2 stage: parent LambdaRank ===")
    log.info("  panel: mart_stock_regime_full (135 cols)")
    log.info("  objective: lambdarank (pairwise NDCG)")
    log.info("  group: signal_date (cross-sectional)")
    log.info("  walk-forward: expanding_monthly, embargo=30")
    # TODO (next session):
    # 1. Load mart_stock_regime_full
    # 2. Define group=signal_date for LambdaRank
    # 3. Call services.ml_ranking.lambdamart_walkforward.train_lambdamart_walkforward
    # 4. Write predictions to mart_p0b_oos_predictions(model_id='phase2_parent_*')
    log.warning("  SKELETON — Phase 2 parent implementation pending")
    return 0


def stage_child(args) -> int:
    """Per-pool child residual regression (Codex final step 2)."""
    log.info(f"=== Phase 2 stage: child residual pool={args.pool_id} ===")
    log.info("  prereq: parent_score from mart_p0b_oos_predictions WHERE model_id='phase2_parent_*'")
    log.info("  pool definition: mart_stock_pool_assignment")
    log.info("  objective: regression on residual = label - parent_score_normalized")
    # TODO (next session):
    # 1. SELECT signal_date, stock_code, parent_score, label, features
    #    FROM mart_stock_regime_full r
    #    JOIN mart_p0b_oos_predictions p ON ... WHERE p.model_id='phase2_parent_*'
    #    JOIN mart_stock_pool_assignment pa ON pa.stock_code = r.stock_code
    #      AND pa.as_of_month = DATE_TRUNC('month', r.signal_date)
    #    WHERE pa.pool_id = ?
    # 2. Compute residual = label - sigmoid_normalize(parent_score)
    # 3. Train LightGBM regression per pool
    # 4. Walk-forward expanding monthly
    # 5. Write predictions to mart_p0b_oos_predictions(model_id='phase2_child_<pool>_*')
    log.warning("  SKELETON — Phase 2 child implementation pending")
    return 0


def stage_combine(args) -> int:
    """Combine parent + child → final score (Codex final step 3)."""
    log.info(f"=== Phase 2 stage: combine w_parent={args.w_parent} ===")
    log.info(f"  final_score = {args.w_parent} × parent_score + {1 - args.w_parent} × child_score")
    log.info("  output: mart_p0b_oos_predictions(model_id='phase2_combined_*')")
    # TODO (next session):
    # 1. JOIN parent + child by (signal_date, stock_code)
    # 2. Compute final_score
    # 3. Write to mart_p0b_oos_predictions
    # 4. Acceptance audit:
    #    - OOS RankIC ≥ 0.024
    #    - Per-fold feature overlap > 55%
    log.warning("  SKELETON — Phase 2 combine implementation pending")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=["parent", "child", "combine"])
    parser.add_argument("--label", default="fwd_cost_after_20d")
    parser.add_argument("--pool-id", default=None)
    parser.add_argument("--w-parent", type=float, default=0.70)
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    if args.stage == "parent":
        return stage_parent(args)
    elif args.stage == "child":
        if not args.pool_id:
            log.error("--pool-id required for child stage")
            return 1
        return stage_child(args)
    elif args.stage == "combine":
        return stage_combine(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
