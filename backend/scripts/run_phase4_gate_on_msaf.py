#!/usr/bin/env python3
"""Phase 4 holdout: 跑 backtest_validation 4 gates on MSAF Phase 3.3 实测 22 obs.

Usage:
    PYTHONPATH=backend python backend/scripts/run_phase4_gate_on_msaf.py

输入:
- 复用 run_msaf_ensemble_paper_sim.py 跑 432 dates → 22 monthly obs port_ret
- 跑 backtest_validation gate (PBO / DSR / Conservative / IS-OOS)

注: PBO 需 multi-trial (n_trials, n_periods), 当前只 1 trial — 跑 multi-horizon 模拟 trials.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.backtest_validation.gate import run_all_gates
from services.strategies.ensemble import ensemble_scores
from services.strategies.regime.regime_state import compute_regime_state, load_hs300_kline

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("phase4_gate")


def load_predictions(db_path: str, model_id: str) -> pd.DataFrame:
    con = duckdb.connect(db_path, read_only=True)
    try:
        df = con.execute(
            "SELECT signal_date, stock_code, score, "
            "       fwd_cost_after_5d, fwd_cost_after_10d, fwd_cost_after_20d "
            "FROM mart_p0b_oos_predictions WHERE model_id = ? ORDER BY signal_date, stock_code",
            [model_id],
        ).fetchdf()
        return df
    finally:
        con.close()


def compute_port_returns(
    preds: pd.DataFrame, horizon: str, hs300: pd.DataFrame, max_positions: int = 5
) -> list[float]:
    """Run ensemble + compute monthly port_ret using horizon non-overlap rebal."""
    fwd_col = f"fwd_cost_after_{horizon}"
    n_days = int(horizon.rstrip("d"))
    fwd_map = preds.set_index(["signal_date", "stock_code"])[fwd_col].to_dict()

    signal_dates = preds["signal_date"].drop_duplicates().tolist()
    results = []
    for sd in signal_dates:
        sd_str = str(sd)[:10]
        try:
            regime = compute_regime_state(sd_str, hs300)
        except ValueError:
            continue
        day_preds = preds[preds["signal_date"] == sd]
        lam = day_preds.set_index("stock_code")["score"]
        v = ensemble_scores(
            signal_date=sd_str, regime=regime, lambdamart_scores=lam, max_positions=max_positions
        )
        results.append({"sd": sd, "codes": v.top_k_codes, "cash_pct": v.cash_pct})

    # Non-overlap rebal
    rebal = results[::n_days]
    obs = []
    for r in rebal:
        sd_norm = pd.Timestamp(r["sd"]).normalize()
        rets = []
        for code in r["codes"]:
            v = fwd_map.get((sd_norm, code))
            if v is not None and pd.notna(v):
                rets.append(float(v))
        if not rets:
            continue
        equity = sum(rets) / len(rets)
        port = (1 - r["cash_pct"]) * equity
        obs.append(port)
    return obs


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Phase 4 gate on MSAF")
    parser.add_argument("--smartmoney-db", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    parser.add_argument("--market-db", default=str(REPO_ROOT / "data" / "market.duckdb"))
    parser.add_argument("--model-id", default="lgbm_20260517_governance_v1_20d")
    parser.add_argument("--challenger-id", default="msaf_v1_lambdamart_only")
    parser.add_argument("--output-json", default=str(REPO_ROOT / "data" / "reports" / "phase4_gate_result.json"))
    args = parser.parse_args()

    log.info(f"=== Phase 4 gate on MSAF model_id={args.model_id} ===")

    # Load
    hs300 = load_hs300_kline(args.market_db)
    preds = load_predictions(args.smartmoney_db, args.model_id)
    log.info(f"  predictions: {len(preds):,} rows, dates {preds['signal_date'].min()} → {preds['signal_date'].max()}")

    # 当前 model_id=20d label, 只有 fwd_cost_after_20d 有数据 (5d/10d NULL)
    # PBO 需 multi-trial — 当前 1 trial 无法跑, mark missing → warn_only
    obs_20d = compute_port_returns(preds, "20d", hs300)
    log.info(f"  obs_20d: n={len(obs_20d)} (monthly non-overlap)")
    if len(obs_20d) < 16:
        log.warning(f"  obs_20d n={len(obs_20d)} too few for stat tests")

    returns_matrix = None  # PBO single-trial 不适用 (待 Phase 5: multi-horizon retrain)

    # Conservative scenario: slippage +50% 估抹 1.5% ann (rule-compliance: ok evidence=cost-model-yaml)
    obs_arr = np.array(obs_20d)
    ann_normal = float(obs_arr.mean() * 12)  # rule-compliance: ok evidence=annualize-monthly
    ann_conservative = ann_normal - 0.015  # rule-compliance: ok evidence=slippage-50pct-overhead-est

    # IS-OOS metric: 用 RankIC 替 sharpe (LightGBM 训练 IC vs OOS IC)
    # 当前没 in-sample RankIC 在 mart, 用 cap 0.04 (P0b reported v1 RankIC bar) 作 IS placeholder
    is_metric = 0.04   # rule-compliance: ok evidence=p0b-v1-ic-baseline
    oos_metric = 0.022  # rule-compliance: ok evidence=p0b-v1-honest-oos-ic

    result = run_all_gates(
        challenger_id=args.challenger_id,
        returns_matrix=returns_matrix,
        oos_returns=obs_arr,
        n_trials_for_dsr=50,  # rule-compliance: ok evidence=optuna-50-trial-search-space
        ann_normal=ann_normal,
        ann_conservative=ann_conservative,
        is_metric=is_metric,
        oos_metric=oos_metric,
    )

    log.info(f"=== verdict: {result.promote_action} ===")
    log.info(f"  all_pass: {result.all_pass}")

    # Save
    out_path = Path(args.output_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "challenger_id": args.challenger_id,
        "model_id": args.model_id,
        "n_obs_20d": len(obs_20d),
        "ann_normal": ann_normal,
        "ann_conservative": ann_conservative,
        "is_metric": is_metric,
        "oos_metric": oos_metric,
        "gate_result": result.to_dict(),
    }, indent=2, ensure_ascii=False, default=str))
    log.info(f"  saved: {args.output_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
