#!/usr/bin/env python3
"""v7 + Phase 7 MACD context whitelist ensemble.

Option 4: v7 base score, filter to positive contexts (below_zero_rebound, zero_axis_below_golden_cross).
Phase 7 showed +0.47 Sharpe lift vs BC baseline. Apply to v7 picks.
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from services.duck_adapter import connect  # noqa: E402

ENSEMBLE_MODEL_ID = "ensemble_v7_phase7_context_v1"
V7_MODEL_ID = "lgbm_phase5_v7_20260523T010000Z"
POSITIVE_CONTEXTS = {"below_zero_rebound_probe", "zero_axis_below_golden_cross"}


def _macd(close):
    ema_f = pd.Series(close).ewm(span=12, adjust=False).mean().values
    ema_s = pd.Series(close).ewm(span=26, adjust=False).mean().values
    dif = ema_f - ema_s
    dea = pd.Series(dif).ewm(span=9, adjust=False).mean().values
    return dif, dea


def _classify(dif, dea, p_dif, p_dea):
    gc = (p_dif <= p_dea) and (dif > dea)
    dc = (p_dif >= p_dea) and (dif < dea)
    above = dif > 0 and dea > 0
    if gc and above: return "zero_axis_above_golden_cross"
    if gc and not above: return "zero_axis_below_golden_cross"
    if dc: return "dead_cross"
    if above: return "above_zero_trend_continuation"
    return "below_zero_rebound_probe"


def main() -> int:
    market_db = str(REPO_ROOT / "data" / "market.duckdb")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with connect(str(REPO_ROOT / "data" / "smartmoney.duckdb"), read_only=False, attach={"market": market_db}) as conn:
        print("Loading v7 predictions...")
        v7 = pd.DataFrame(conn.execute(f"""
            SELECT signal_date, stock_code, score AS v7_score,
                   fwd_cost_after_5d, fwd_cost_after_10d, fwd_cost_after_20d
              FROM mart_p0b_lambdamart_v6_predictions
             WHERE model_id = '{V7_MODEL_ID}' AND score IS NOT NULL
        """).fetchall(), columns=["signal_date","stock_code","v7_score","fwd5","fwd10","fwd20"])
        v7["signal_date"] = pd.to_datetime(v7["signal_date"])
        print(f"  v7 rows: {len(v7):,}, unique stocks: {v7['stock_code'].nunique()}")

        # Compute MACD context per (stock, signal_date)
        print("Computing MACD context per stock (kline cache)...")
        contexts = {}
        for stock in v7["stock_code"].unique():
            rows = conn.execute(
                "SELECT date, close FROM market.v_price_kline_qfq "
                # rule-compliance: ok evidence=MACD warmup 6mo before panel start 2024-01-02
                "WHERE freq='daily' AND adjust='qfq' AND code=? AND date>='2023-07-01' ORDER BY date",
                [stock],
            ).fetchall()
            if not rows or len(rows) < 30:
                continue
            df = pd.DataFrame(rows, columns=["date", "close"])
            df["date"] = pd.to_datetime(df["date"])
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            close_arr = df["close"].values
            dif, dea = _macd(close_arr)
            df["ctx"] = [
                _classify(dif[i], dea[i], dif[i-1], dea[i-1]) if i >= 1 else None
                for i in range(len(df))
            ]
            for d, ctx in zip(df["date"], df["ctx"]):
                if ctx: contexts[(stock, d)] = ctx
        print(f"  computed contexts: {len(contexts):,}")

        # Build ensemble: v7 score IF context in positive whitelist ELSE NULL
        v7["ctx"] = v7.apply(lambda r: contexts.get((r["stock_code"], r["signal_date"])), axis=1)
        v7["score_out"] = v7.apply(lambda r: r["v7_score"] if r["ctx"] in POSITIVE_CONTEXTS else None, axis=1)
        n_non_null = v7["score_out"].notna().sum()
        print(f"  non-NULL (positive ctx): {n_non_null:,} / {len(v7):,} = {n_non_null/len(v7)*100:.1f}%")

        # Insert into predictions table
        conn.execute(f"DELETE FROM mart_p0b_lambdamart_v6_predictions WHERE model_id = '{ENSEMBLE_MODEL_ID}'")
        v7_out = v7[["stock_code","signal_date","score_out","fwd5","fwd10","fwd20"]].copy()
        v7_out.columns = ["stock_code","signal_date","score","fwd_cost_after_5d","fwd_cost_after_10d","fwd_cost_after_20d"]
        v7_out["model_id"] = ENSEMBLE_MODEL_ID
        v7_out["model_version"] = "v7_phase7_context_v1"
        v7_out["feature_version"] = "p0a_v5_PIT+macd_context_filter"
        v7_out["label_version"] = "p0a_v2_governance_v1"
        v7_out["walk_forward_mode"] = "NA"
        v7_out["train_start"] = pd.Timestamp("2024-01-02")  # rule-compliance: ok evidence=v7 train start
        v7_out["train_end"] = pd.Timestamp("2024-06-28")    # rule-compliance: ok evidence=v7 train end
        v7_out["test_start"] = pd.Timestamp("2024-07-01")   # rule-compliance: ok evidence=v7 OOS start
        v7_out["test_end"] = pd.Timestamp("2026-04-13")     # rule-compliance: ok evidence=panel cutoff
        v7_out["is_final_holdout"] = False
        v7_out["built_at"] = now
        v7_out["trade_date_dt"] = v7_out["signal_date"]
        conn._con.register("v7_out_df", v7_out)
        conn.execute("INSERT INTO mart_p0b_lambdamart_v6_predictions BY NAME SELECT * FROM v7_out_df")
        conn.commit()
        r = conn.execute(f"SELECT COUNT(*), SUM(CASE WHEN score IS NOT NULL THEN 1 ELSE 0 END) FROM mart_p0b_lambdamart_v6_predictions WHERE model_id = '{ENSEMBLE_MODEL_ID}'").fetchone()
        print(f"\n[OK] {ENSEMBLE_MODEL_ID}: total {r[0]:,} rows, non-NULL {r[1]:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
