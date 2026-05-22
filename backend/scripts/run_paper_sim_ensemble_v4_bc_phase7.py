#!/usr/bin/env python3
"""Multi-model ensemble: V4 score + BC confidence + Phase 7 context-aware exit policy.

Per goal.md Section I priority #2 — push portfolio Sharpe toward 2.0 (operational target).

Method:
- Entry: V4 OOS rank + BC daily picks (intersection or union)
- Filter: Phase 7 context whitelist (drop above_zero_trend_continuation = walk-forward neg)
- Exit: Phase 7 best_sell_rule per (stock × formula × ctx), fallback fixed median per ctx
- Walk-forward TEST 2025-01 to 2026-04 (same as Phase 7 POC)

Compare:
- V4 alone (Sharpe 0.65 / dd -21.7%)
- BC alone (Sharpe 1.10 / dd -22.1%)
- ensemble V4+BC rank-combine (Sharpe 1.83 / dd -16.85%)
- Phase 7 policy (Sharpe 1.67)
- ensemble V4+BC+Phase 7 (target: push toward 2.0)

operational gate per goal.md #6 perfect ladder: Sharpe ≥ 2.0 / max_dd ≥ -20% / n_obs ≥ 60.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
BC_ROOT = REPO_ROOT / "bestchoice"
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(BC_ROOT))
from services.duck_adapter import connect  # noqa: E402

# rule-compliance: ok evidence=Phase 7 walk-forward TEST range
TEST_START = "2025-01-01"  # rule-compliance: ok evidence=Phase 7 walk-forward test start
TEST_END = "2026-04-13"     # rule-compliance: ok evidence=panel V4 OOS upper bound
POSITIVE_CONTEXTS = {"below_zero_rebound_probe", "zero_axis_below_golden_cross"}


def _macd(close):
    ema_f = pd.Series(close).ewm(span=12, adjust=False).mean().values
    ema_s = pd.Series(close).ewm(span=26, adjust=False).mean().values
    dif = ema_f - ema_s
    dea = pd.Series(dif).ewm(span=9, adjust=False).mean().values
    return dif, dea


def _classify_macd(dif, dea, p_dif, p_dea):
    gc = (p_dif <= p_dea) and (dif > dea)
    dc = (p_dif >= p_dea) and (dif < dea)
    above = dif > 0 and dea > 0
    if gc and above: return "zero_axis_above_golden_cross"
    if gc and not above: return "zero_axis_below_golden_cross"
    if dc: return "dead_cross"
    if above: return "above_zero_trend_continuation"
    return "below_zero_rebound_probe"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--policy-run", default="bestchoice_context_exit_v1_20260522_full")
    p.add_argument("--top-k-v4", type=int, default=10, help="V4 top-K threshold per signal_date")
    args = p.parse_args()

    market_db = str(REPO_ROOT / "data" / "market.duckdb")
    with connect(str(REPO_ROOT / "data" / "smartmoney.duckdb"), read_only=True, attach={"market": market_db}) as conn:
        # 1. V4 picks: top-K per signal_date (in TEST window)
        v4_picks = pd.DataFrame(
            conn.execute(
                """
                WITH ranked AS (
                    SELECT signal_date, stock_code, score,
                           ROW_NUMBER() OVER (PARTITION BY signal_date ORDER BY score DESC) AS rk
                      FROM mart_p0b_oos_predictions
                     WHERE model_id = 'lgbm_20260517_governance_v1_20d'
                       AND signal_date >= ? AND signal_date <= ?
                       AND score IS NOT NULL
                )
                SELECT signal_date, stock_code, score, rk FROM ranked WHERE rk <= ?
                """,
                [TEST_START, TEST_END, args.top_k_v4],
            ).fetchall(),
            columns=["signal_date", "stock_code", "v4_score", "v4_rank"],
        )
        v4_picks["signal_date"] = pd.to_datetime(v4_picks["signal_date"])
        print(f"V4 top-{args.top_k_v4} picks: {len(v4_picks):,}")

        # 2. BC daily picks
        bc_picks = pd.DataFrame(
            conn.execute(
                """
                SELECT signal_date, buy_date, stock_code, formula_id, sell_rule,
                       holding_days, confidence_score
                  FROM mart_daily_formula_candidate_bestchoice_v1
                 WHERE signal_date >= ? AND signal_date <= ?
                """,
                [TEST_START, TEST_END],
            ).fetchall(),
            columns=["signal_date", "buy_date", "stock_code", "formula_id", "sell_rule", "holding_days", "bc_confidence"],
        )
        bc_picks["signal_date"] = pd.to_datetime(bc_picks["signal_date"])
        bc_picks["buy_date"] = pd.to_datetime(bc_picks["buy_date"])
        print(f"BC picks: {len(bc_picks):,}")

        # 3. Phase 7 policy
        policy = pd.DataFrame(
            conn.execute(
                "SELECT stock_code, formula_id, macd_context, holding_days "
                "FROM mart_bestchoice_context_exit_policy_v1 WHERE policy_run_id = ?",
                [args.policy_run],
            ).fetchall(),
            columns=["stock_code", "formula_id", "macd_context", "policy_hold"],
        )
        print(f"Phase 7 policy rows: {len(policy):,}\n")

        # 4. Ensemble strategy: intersect V4 top-K + BC + filter ctx whitelist
        merged = bc_picks.merge(v4_picks[["signal_date", "stock_code", "v4_score", "v4_rank"]],
                                on=["signal_date", "stock_code"], how="inner")
        print(f"Intersection V4 top-{args.top_k_v4} ∩ BC: {len(merged):,}")
        if merged.empty:
            print("No intersection - try larger top-K-v4")
            return 1

        # 5. Per pick: classify ctx, filter positive, apply Phase 7 exit
        # Load kline once per stock
        unique_stocks = merged["stock_code"].unique()
        kline_cache = {}
        for stock in unique_stocks:
            rows = conn.execute(
                "SELECT date, close FROM market.v_price_kline_qfq "
                # rule-compliance: ok evidence=MACD warmup 1y before TEST_START
                "WHERE freq='daily' AND adjust='qfq' AND code=? AND date>='2024-01-01' ORDER BY date",
                [stock],
            ).fetchall()
            if not rows:
                continue
            df = pd.DataFrame(rows, columns=["date", "close"])
            df["date"] = pd.to_datetime(df["date"])
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            kline_cache[stock] = df

        results = []
        for _, pick in merged.iterrows():
            stock = pick["stock_code"]
            signal_date = pick["signal_date"]
            buy_date = pick["buy_date"]
            if stock not in kline_cache:
                continue
            df = kline_cache[stock]
            mask = df["date"] == signal_date
            if not mask.any():
                continue
            sig_idx = df.index[mask][0]
            if sig_idx < 30:
                continue
            close_arr = df["close"].values
            dif, dea = _macd(close_arr)
            ctx = _classify_macd(dif[sig_idx], dea[sig_idx], dif[sig_idx - 1], dea[sig_idx - 1])
            if ctx not in POSITIVE_CONTEXTS:
                continue  # Phase 7 whitelist filter
            buy_mask = df["date"] >= buy_date
            if not buy_mask.any():
                continue
            buy_idx = df.index[buy_mask][0]
            if buy_idx >= len(df) - 1:
                continue
            buy_price = df.iloc[buy_idx]["close"]
            if not np.isfinite(buy_price) or buy_price <= 0:
                continue
            # Use Phase 7 hold (per-stock-formula-context lookup)
            match = policy[(policy["stock_code"] == stock) & (policy["formula_id"] == pick["formula_id"]) & (policy["macd_context"] == ctx)]
            if not match.empty:
                hold = int(match.iloc[0]["policy_hold"])
            else:
                # fallback per-context global median
                fallback = policy[policy["macd_context"] == ctx]
                hold = int(fallback["policy_hold"].median()) if not fallback.empty else 12  # rule-compliance: ok evidence=Phase 7 median below_zero_rebound ~12d
            sell_idx = buy_idx + hold
            if sell_idx >= len(df):
                continue
            sell_price = df.iloc[sell_idx]["close"]
            if not np.isfinite(sell_price) or sell_price <= 0:
                continue
            ret = sell_price / buy_price - 1.0
            results.append({"signal_date": signal_date, "stock_code": stock, "ctx": ctx,
                            "ret": float(ret), "hold": hold, "v4_rank": int(pick["v4_rank"])})

        if not results:
            print("0 trades — try larger top-K-v4 OR different ctx filter")
            return 1
        df_r = pd.DataFrame(results)
        n = len(df_r)
        avg_ret = float(df_r["ret"].mean())
        avg_hold = float(df_r["hold"].mean())
        win = float((df_r["ret"] > 0).mean())
        std = float(df_r["ret"].std())
        sharpe = avg_ret / std * np.sqrt(252.0 / avg_hold) if std > 0 else 0.0
        ann = avg_ret * 252.0 / avg_hold
        df_r_sorted = df_r.sort_values("signal_date")
        nav = (1 + df_r_sorted["ret"]).cumprod()
        max_dd = float(((nav - nav.cummax()) / nav.cummax()).min())

        print(f"\n=== Multi-model ensemble V4 + BC + Phase 7 ===")
        print(f"  n trades: {n:,}")
        print(f"  avg ret/trade: {avg_ret:.2%}")
        print(f"  win rate: {win:.2%}")
        print(f"  avg hold: {avg_hold:.1f}d")
        print(f"  sharpe: {sharpe:.4f}")
        print(f"  ann: {ann:.2%}")
        print(f"  max_dd: {max_dd:.2%}")
        print(f"\n=== Comparison ===")
        print(f"  V4 alone (paper_sim):         sharpe 0.65 / dd -21.7%")
        print(f"  BC alone (paper_sim):         sharpe 1.10 / dd -22.1%")
        print(f"  Ensemble V4+BC rank-combine:  sharpe 1.83 / dd -16.85%")
        print(f"  Phase 7 policy alone:         sharpe 1.67 / dd N/A")
        print(f"  >>> V4 ∩ BC + Phase 7 policy: sharpe {sharpe:.2f} / dd {max_dd:.2%}")
        print(f"\n=== Operational gate (#6 perfect ladder) ===")
        sharpe_ok = sharpe >= 2.0
        dd_ok = max_dd >= -0.20
        n_ok = n >= 60
        print(f"  Sharpe ≥ 2.0: {'PASS' if sharpe_ok else 'FAIL'} ({sharpe:.2f})")
        print(f"  max_dd ≥ -20%: {'PASS' if dd_ok else 'FAIL'} ({max_dd:.2%})")
        print(f"  n_obs ≥ 60: {'PASS' if n_ok else 'FAIL'} ({n})")
        print(f"  Operational: {'READY' if (sharpe_ok and dd_ok and n_ok) else 'NOT READY'}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
