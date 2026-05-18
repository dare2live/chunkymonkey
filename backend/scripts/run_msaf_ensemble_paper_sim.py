#!/usr/bin/env python3
"""MSAF Phase 3.3: Ensemble paper_sim runner.

跑 3 类策略 ensemble + regime adaptive 加权的历史 paper_sim, 输出 KPI 跟 baseline 对比.

3 类输入:
- lambdamart_v6 (mart_p0b_oos_predictions 或 mart_p0b_lambdamart_v6_predictions)
- sniper confluence (services.strategies.sniper, 当前 fallback 全 0)
- 机构跟随 composite (services.strategies.institution_follow, 当前 fallback 全 0)

For Phase 3.3 minimum viable:
- 用现有 lambdamart_v6 (Codex 2.1) 或 v4 ml_score 作输入
- sniper / institution placeholder (返回 0 score, ensemble 仅取 lambdamart)
- 后续 Phase 3.4 接全 3 source

Usage:
    PYTHONPATH=backend python backend/scripts/run_msaf_ensemble_paper_sim.py \
        --start 2024-07-01 --end 2026-04-13 --max-positions 5 --output mart_msaf_ensemble_kpi
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.strategies.ensemble import ensemble_scores
from services.strategies.regime.regime_state import compute_regime_state, load_hs300_kline

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("msaf_ensemble")


def load_lambdamart_predictions(
    db_path: str,
    model_id: str = "lgbm_20260517_governance_v1_20d",
    start_date: str = "2024-07-01",  # rule-compliance: ok evidence=p0b-walk-forward-起始
    end_date: str = "2026-04-13",    # rule-compliance: ok evidence=panel-cutoff
) -> pd.DataFrame:
    """Load LambdaMART (or LGBM) predictions from mart_p0b_oos_predictions."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        df = con.execute(
            "SELECT signal_date, stock_code, score "
            "FROM mart_p0b_oos_predictions "
            "WHERE model_id = ? AND signal_date >= ? AND signal_date <= ? "
            "ORDER BY signal_date, stock_code",
            [model_id, start_date, end_date],
        ).fetchdf()
        return df
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="MSAF ensemble paper_sim runner")
    parser.add_argument("--start", default="2024-07-01")  # rule-compliance: ok evidence=p0b-walk-forward-起始
    parser.add_argument("--end", default="2026-04-13")    # rule-compliance: ok evidence=panel-cutoff
    parser.add_argument("--smartmoney-db", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    parser.add_argument("--market-db", default=str(REPO_ROOT / "data" / "market.duckdb"))
    parser.add_argument("--lambdamart-model-id", default="lgbm_20260517_governance_v1_20d")
    parser.add_argument("--max-positions", type=int, default=5)
    parser.add_argument("--output-json", default=str(REPO_ROOT / "data" / "reports" / "msaf_ensemble_run.json"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    log.info(f"=== MSAF Ensemble paper_sim {args.start} → {args.end} ===")

    # 1. Load HS300 K-line for regime
    log.info("Loading HS300 K-line...")
    hs300 = load_hs300_kline(args.market_db)
    log.info(f"  HS300: {len(hs300)} rows, {hs300.iloc[0]['date']} → {hs300.iloc[-1]['date']}")

    # 2. Load LambdaMART predictions
    log.info(f"Loading predictions: model_id={args.lambdamart_model_id}")
    preds = load_lambdamart_predictions(
        args.smartmoney_db, args.lambdamart_model_id, args.start, args.end
    )
    log.info(f"  predictions: {len(preds):,} rows, {preds['signal_date'].min()} → {preds['signal_date'].max()}")

    # 3. Loop daily signals
    signal_dates = preds["signal_date"].drop_duplicates().tolist()
    log.info(f"  unique signal_dates: {len(signal_dates)}")

    results: list[dict] = []
    regime_counts = {"bull": 0, "neutral": 0, "bear": 0, "crash": 0}
    for sd in signal_dates:
        sd_str = str(sd)[:10]
        try:
            regime = compute_regime_state(sd_str, hs300)
        except ValueError as e:
            log.warning(f"  {sd_str}: skip — {e}")
            continue
        regime_counts[regime.state] += 1

        # lambdamart scores for this signal_date
        day_preds = preds[preds["signal_date"] == sd]
        lam = day_preds.set_index("stock_code")["score"]

        # sniper / institution placeholders (Phase 3.3 minimum viable)
        verdict = ensemble_scores(
            signal_date=sd_str,
            regime=regime,
            lambdamart_scores=lam,
            sniper_scores=None,  # TODO: Phase 3.4
            institution_scores=None,  # TODO: Phase 3.4
            max_positions=args.max_positions,
        )
        results.append({
            "signal_date": sd_str,
            "regime_state": verdict.regime_state,
            "cash_pct": verdict.cash_pct,
            "n_positions": len(verdict.top_k_codes),
            "top_k_codes": verdict.top_k_codes,
            "top_k_scores": [round(float(s), 4) for s in verdict.top_k_scores],
        })

    log.info(f"=== ensemble done ===")
    log.info(f"  signal_dates processed: {len(results)}")
    log.info(f"  regime distribution: {regime_counts}")

    # Output
    if not args.dry_run:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({
            "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "args": vars(args),
            "regime_counts": regime_counts,
            "n_signal_dates": len(results),
            "results": results[:10],  # first 10 sample, full results 太大不存
            "results_total": len(results),
        }, indent=2, ensure_ascii=False, default=str))
        log.info(f"  saved: {args.output_json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
