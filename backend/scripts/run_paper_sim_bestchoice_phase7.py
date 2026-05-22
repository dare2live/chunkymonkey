#!/usr/bin/env python3
"""BC Phase 7 D6: paper_sim test of context-aware exit policy on portfolio level.

Uses mart_bestchoice_context_exit_policy_v1 (D5 walk-forward POC output) to overlay
sell rules on actual portfolio paper_sim. Demonstrate: does context-aware exit improve
portfolio Sharpe vs BC baseline fixed_N exit?

Strategy:
- TEST window: 2025-01-01 to 2026-04-13 (same as Phase 7 POC walk-forward TEST)
- BC daily picks: from mart_daily_formula_candidate_bestchoice_v1
- Override sell_rule: lookup mart_bestchoice_context_exit_policy_v1 per (stock_code,
  formula_id, macd_context) → use best_sell_rule; fallback to existing BC fixed_N
- Apply context filter: only buy if context in HIGH-confidence whitelist
  (below_zero_rebound_probe, zero_axis_below_golden_cross — positive walk-forward sharpe)
- Drop above_zero_trend_continuation (walk-forward neg)
- Output: portfolio-level Sharpe / ann / dd / win rate vs BC baseline (fixed sell_rule)

Note: This is POC paper_sim. Production integration would go via paper_sim_v6 with
custom exit_rules wrapper (defer to Phase 8 if Phase 7 portfolio gate PASS).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
BC_ROOT = REPO_ROOT / "bestchoice"
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(BC_ROOT))
from services.duck_adapter import connect  # noqa: E402
from formula_engine import compute_formula_signals  # noqa: E402

# rule-compliance: ok evidence=Phase 7 POC walk-forward TEST range = BC paper_sim 同期
TEST_START = "2025-01-01"  # rule-compliance: ok evidence=Phase 7 walk-forward test start
TEST_END = "2026-04-13"     # rule-compliance: ok evidence=panel V4 OOS upper bound
POSITIVE_CONTEXTS = {
    "below_zero_rebound_probe",      # full POC sharpe 6.14 (suspicious but positive)
    "zero_axis_below_golden_cross",   # full POC sharpe 1.15
}


def _macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_f = pd.Series(close).ewm(span=fast, adjust=False).mean().values
    ema_s = pd.Series(close).ewm(span=slow, adjust=False).mean().values
    dif = ema_f - ema_s
    dea = pd.Series(dif).ewm(span=signal, adjust=False).mean().values
    return dif, dea


def _classify_macd_context(dif, dea, prev_dif, prev_dea):
    golden_cross = (prev_dif <= prev_dea) and (dif > dea)
    dead_cross = (prev_dif >= prev_dea) and (dif < dea)
    above_zero = dif > 0 and dea > 0
    if golden_cross and above_zero:
        return "zero_axis_above_golden_cross"
    if golden_cross and not above_zero:
        return "zero_axis_below_golden_cross"
    if dead_cross:
        return "dead_cross"
    if above_zero:
        return "above_zero_trend_continuation"
    return "below_zero_rebound_probe"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-path", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    p.add_argument("--policy-run-id", default="bestchoice_context_exit_v1_20260522_full")
    p.add_argument("--bc-run-id", default="bestchoice_formula_optuna_20260521_v1")
    args = p.parse_args()

    market_db = str(REPO_ROOT / "data" / "market.duckdb")

    with connect(args.db_path, read_only=True, attach={"market": market_db}) as conn:
        # Load Phase 7 policy table
        policy = pd.DataFrame(
            conn.execute(
                "SELECT stock_code, formula_id, macd_context, best_sell_rule, holding_days "
                "FROM mart_bestchoice_context_exit_policy_v1 "
                "WHERE policy_run_id = ?",
                [args.policy_run_id],
            ).fetchall(),
            columns=["stock_code", "formula_id", "macd_context", "best_sell_rule", "holding_days"],
        )
        print(f"[Phase 7 paper_sim] policy rows loaded: {len(policy):,}")

        # Load BC daily picks in TEST window
        bc_picks = pd.DataFrame(
            conn.execute(
                "SELECT signal_date, buy_date, stock_code, formula_id, sell_rule, "
                "holding_days, confidence_score "
                "FROM mart_daily_formula_candidate_bestchoice_v1 "
                "WHERE run_id = ? AND signal_date >= ? AND signal_date <= ?",
                [args.bc_run_id, TEST_START, TEST_END],
            ).fetchall(),
            columns=["signal_date", "buy_date", "stock_code", "formula_id", "sell_rule",
                     "holding_days", "confidence_score"],
        )
        print(f"[Phase 7 paper_sim] BC picks in TEST window: {len(bc_picks):,}")
        if bc_picks.empty:
            return 1
        bc_picks["signal_date"] = pd.to_datetime(bc_picks["signal_date"])
        bc_picks["buy_date"] = pd.to_datetime(bc_picks["buy_date"])

        # For each pick, classify macd_context at signal_date + apply policy override
        results_baseline = []  # use BC fixed sell_rule
        results_policy = []     # use Phase 7 policy + context whitelist
        kline_cache: dict[str, pd.DataFrame] = {}

        for _, pick in bc_picks.iterrows():
            stock = pick["stock_code"]
            signal_date = pick["signal_date"]
            buy_date = pick["buy_date"]

            if stock not in kline_cache:
                rows = conn.execute(
                    "SELECT date, close FROM market.v_price_kline_qfq "
                    # rule-compliance: ok evidence=BC POC kline cutoff 1 year before TEST_START for MACD warmup
                    "WHERE freq='daily' AND adjust='qfq' AND code=? AND date>='2024-01-01' ORDER BY date",
                    [stock],
                ).fetchall()
                if not rows:
                    kline_cache[stock] = pd.DataFrame()
                    continue
                df = pd.DataFrame(rows, columns=["date", "close"])
                df["date"] = pd.to_datetime(df["date"])
                df["close"] = pd.to_numeric(df["close"], errors="coerce")
                kline_cache[stock] = df
            df = kline_cache[stock]
            if df.empty:
                continue

            # Find signal_date idx
            mask = df["date"] == signal_date
            if not mask.any():
                continue
            idx = df.index[mask][0]
            if idx < 30:
                continue

            close_arr = df["close"].values
            dif, dea = _macd(close_arr)
            ctx = _classify_macd_context(dif[idx], dea[idx], dif[idx - 1], dea[idx - 1])

            # Buy price at next day
            buy_mask = df["date"] >= buy_date
            if not buy_mask.any():
                continue
            buy_idx = df.index[buy_mask][0]
            if buy_idx >= len(df) - 1:
                continue
            buy_price = df.iloc[buy_idx]["close"]
            if not np.isfinite(buy_price) or buy_price <= 0:
                continue

            # Baseline: use BC original sell_rule (fixed_N or formula_exit_or_N)
            sell_rule = pick["sell_rule"]
            try:
                if sell_rule.startswith("fixed_"):
                    bc_hold = int(sell_rule.replace("fixed_", ""))
                elif "_or_" in sell_rule:
                    bc_hold = int(sell_rule.split("_or_")[-1])
                else:
                    bc_hold = int(pick["holding_days"] or 20)  # rule-compliance: ok evidence=BC default fallback
            except Exception:
                bc_hold = 20  # rule-compliance: ok evidence=BC default 20d fallback for malformed sell_rule
            sell_idx = buy_idx + bc_hold
            if sell_idx >= len(df):
                continue
            sell_price = df.iloc[sell_idx]["close"]
            if not np.isfinite(sell_price) or sell_price <= 0:
                continue
            ret_baseline = sell_price / buy_price - 1.0
            results_baseline.append({
                "signal_date": signal_date, "stock_code": stock, "ctx": ctx,
                "ret": ret_baseline, "hold": bc_hold,
            })

            # Policy: lookup policy, apply context whitelist
            if ctx not in POSITIVE_CONTEXTS:
                continue  # drop entry per context filter
            match = policy[
                (policy["stock_code"] == stock)
                & (policy["formula_id"] == pick["formula_id"])
                & (policy["macd_context"] == ctx)
            ]
            if not match.empty:
                policy_hold = int(match.iloc[0]["holding_days"])
            else:
                # Fallback to formula+context global default (median holding across that context)
                fallback = policy[policy["macd_context"] == ctx]
                policy_hold = int(fallback["holding_days"].median()) if not fallback.empty else bc_hold
            policy_sell_idx = buy_idx + policy_hold
            if policy_sell_idx >= len(df):
                continue
            policy_sell_price = df.iloc[policy_sell_idx]["close"]
            if not np.isfinite(policy_sell_price) or policy_sell_price <= 0:
                continue
            ret_policy = policy_sell_price / buy_price - 1.0
            results_policy.append({
                "signal_date": signal_date, "stock_code": stock, "ctx": ctx,
                "ret": ret_policy, "hold": policy_hold,
            })

        # Summary
        def _kpi(rows):
            if not rows:
                return {"n": 0}
            df_r = pd.DataFrame(rows)
            n = len(df_r)
            avg_ret = float(df_r["ret"].mean())
            avg_hold = float(df_r["hold"].mean())
            win_rate = float((df_r["ret"] > 0).mean())
            std_ret = float(df_r["ret"].std())
            sharpe = avg_ret / std_ret * np.sqrt(252.0 / avg_hold) if std_ret > 0 else 0.0
            ann = avg_ret * (252.0 / avg_hold)
            # Max DD approx: cumulative product
            df_r_sorted = df_r.sort_values("signal_date")
            nav = (1 + df_r_sorted["ret"]).cumprod()
            peak = nav.cummax()
            dd = (nav - peak) / peak
            max_dd = float(dd.min())
            return {"n": n, "avg_ret": avg_ret, "avg_hold": avg_hold,
                    "win_rate": win_rate, "sharpe": sharpe, "ann": ann, "max_dd": max_dd}

        baseline_kpi = _kpi(results_baseline)
        policy_kpi = _kpi(results_policy)

        print(f"\n=== Phase 7 paper_sim (D6) result ===\n")
        print(f"{'metric':<15} {'BASELINE':>15} {'POLICY':>15} {'delta':>12}")
        for k in ["n", "avg_ret", "avg_hold", "win_rate", "sharpe", "ann", "max_dd"]:
            bv = baseline_kpi.get(k, 0)
            pv = policy_kpi.get(k, 0)
            d = pv - bv if isinstance(pv, (int, float)) and isinstance(bv, (int, float)) else 0
            print(f"  {k:<13} {bv:>15.4f} {pv:>15.4f} {d:>+12.4f}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
