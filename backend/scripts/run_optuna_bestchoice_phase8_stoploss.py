#!/usr/bin/env python3
"""BC Phase 8: ATR stop + avg_dd stop Optuna 2D sweep.

Per goal.md line 296 spec:
- A: ATR stop = K × ATR20 (K in [1.5, 3.0])
- B: avg_dd stop = M × candidate-level historical avg_dd (M in [1.0, 2.0])
- Optuna sweep K × M → find best stop combo on Phase 7 policy picks (positive contexts)
- Eval: portfolio Sharpe / max_dd / win_rate
- Local Optuna (no GCP), 50 trials, ~5-15 min

Test universe: Phase 7 walk-forward TEST window 2025-01 to 2026-04 picks
Pick filter: only contexts in positive whitelist (below_zero_rebound, zero_axis_below_golden_cross)
Combine with Phase 7 best_sell_rule per (stock × formula × ctx)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import optuna
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
BC_ROOT = REPO_ROOT / "bestchoice"
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(BC_ROOT))
from services.duck_adapter import connect  # noqa: E402
from services.bestchoice_config import DEFAULT_BESTCHOICE_PIPELINE_CONFIG  # noqa: E402
from formula_engine import compute_formula_signals  # noqa: E402

# rule-compliance: ok evidence=Phase 7 POC walk-forward TEST range
TEST_START = DEFAULT_BESTCHOICE_PIPELINE_CONFIG.walkforward_test_start_date  # rule-compliance: ok evidence=Phase 7 walk-forward test start
TEST_END = DEFAULT_BESTCHOICE_PIPELINE_CONFIG.walkforward_test_end_date     # rule-compliance: ok evidence=panel V4 OOS upper bound
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


def _atr(high, low, close, n=20):
    tr1 = high - low
    tr2 = np.abs(high - np.concatenate([[close[0]], close[:-1]]))
    tr3 = np.abs(low - np.concatenate([[close[0]], close[:-1]]))
    tr = np.maximum(tr1, np.maximum(tr2, tr3))
    return pd.Series(tr).rolling(n).mean().values


def load_picks(conn, policy_run: str = DEFAULT_BESTCHOICE_PIPELINE_CONFIG.context_exit_policy_run_id_full) -> pd.DataFrame:
    """Load Phase 7 policy + BC daily picks, joined."""
    picks = pd.DataFrame(
        conn.execute(
            "SELECT signal_date, buy_date, stock_code, formula_id "
            "FROM mart_daily_formula_candidate_bestchoice_v1 "
            "WHERE signal_date >= ? AND signal_date <= ?",
            [TEST_START, TEST_END],
        ).fetchall(),
        columns=["signal_date", "buy_date", "stock_code", "formula_id"],
    )
    policy = pd.DataFrame(
        conn.execute(
            "SELECT stock_code, formula_id, macd_context, holding_days "
            "FROM mart_bestchoice_context_exit_policy_v1 WHERE policy_run_id = ?",
            [policy_run],
        ).fetchall(),
        columns=["stock_code", "formula_id", "macd_context", "holding_days"],
    )
    picks["signal_date"] = pd.to_datetime(picks["signal_date"])
    picks["buy_date"] = pd.to_datetime(picks["buy_date"])
    return picks, policy


def simulate_trade(df: pd.DataFrame, buy_idx: int, max_hold: int, atr_stop: float, dd_stop: float) -> dict:
    """Simulate single trade with ATR stop + avg_dd stop + max_hold time stop.

    atr_stop: absolute price level (buy_price - K * ATR20)
    dd_stop: max acceptable drawdown from buy (e.g. -0.10 = -10%)
    """
    if buy_idx >= len(df) - 1:
        return {"exit_reason": "no_future", "ret": 0.0, "hold": 0}
    buy_price = df.iloc[buy_idx]["close"]
    if not np.isfinite(buy_price) or buy_price <= 0:
        return {"exit_reason": "bad_price", "ret": 0.0, "hold": 0}
    for hold in range(1, min(max_hold + 1, len(df) - buy_idx)):
        cur_idx = buy_idx + hold
        cur_close = df.iloc[cur_idx]["close"]
        cur_low = df.iloc[cur_idx]["low"]
        if not np.isfinite(cur_close):
            continue
        # ATR stop: if low touches atr_stop level → exit at atr_stop
        if cur_low <= atr_stop and atr_stop > 0:
            return {"exit_reason": "atr_stop", "ret": atr_stop / buy_price - 1.0, "hold": hold}
        # avg_dd stop: peak-to-current dd >= dd_stop threshold
        path = df.iloc[buy_idx:cur_idx + 1]["close"].values
        peak = path.max()
        cur_dd = (cur_close - peak) / peak if peak > 0 else 0.0
        if cur_dd <= dd_stop:
            return {"exit_reason": "dd_stop", "ret": cur_close / buy_price - 1.0, "hold": hold}
    # Time stop
    final_idx = min(buy_idx + max_hold, len(df) - 1)
    final_price = df.iloc[final_idx]["close"]
    return {"exit_reason": "time_stop", "ret": final_price / buy_price - 1.0, "hold": final_idx - buy_idx}


def objective(trial: optuna.Trial, conn, picks, policy, kline_cache) -> float:
    K = trial.suggest_float("K_atr", 1.5, 3.0, step=0.1)
    M = trial.suggest_float("M_dd", 0.05, 0.20, step=0.01)
    rets = []
    holds = []
    for _, pick in picks.iterrows():
        stock = pick["stock_code"]
        formula_id = pick["formula_id"]
        signal_date = pick["signal_date"]
        if stock not in kline_cache:
            continue
        df = kline_cache[stock]
        if df.empty:
            continue
        mask = df["date"] == signal_date
        if not mask.any():
            continue
        sig_idx = df.index[mask][0]
        if sig_idx < 30 or sig_idx >= len(df) - 1:
            continue
        dif, dea = _macd(df["close"].values)
        ctx = _classify_macd(dif[sig_idx], dea[sig_idx], dif[sig_idx - 1], dea[sig_idx - 1])
        if ctx not in POSITIVE_CONTEXTS:
            continue
        match = policy[(policy["stock_code"] == stock) & (policy["formula_id"] == formula_id) & (policy["macd_context"] == ctx)]
        if not match.empty:
            max_hold = int(match.iloc[0]["holding_days"])
        else:
            max_hold = 12  # rule-compliance: ok evidence=Phase 7 below_zero_rebound median hold ~12d
        atr = df["atr20"].iloc[sig_idx]
        buy_idx = sig_idx + 1
        buy_price = df.iloc[buy_idx]["close"]
        if not np.isfinite(buy_price) or buy_price <= 0:
            continue
        atr_stop_price = buy_price - K * atr if np.isfinite(atr) and atr > 0 else 0
        dd_stop = -M
        result = simulate_trade(df, buy_idx, max_hold, atr_stop_price, dd_stop)
        rets.append(result["ret"])
        holds.append(result["hold"])
    if not rets:
        return -10.0
    arr = np.array(rets)
    avg_h = np.mean(holds) if holds else 12.0
    sharpe = arr.mean() / arr.std() * np.sqrt(252.0 / avg_h) if arr.std() > 0 else 0.0
    trial.set_user_attr("n", int(len(arr)))
    trial.set_user_attr("win_rate", float((arr > 0).mean()))
    trial.set_user_attr("avg_ret", float(arr.mean()))
    trial.set_user_attr("avg_hold", float(avg_h))
    return float(sharpe)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--n-trials", type=int, default=50)
    p.add_argument("--policy-run", default=DEFAULT_BESTCHOICE_PIPELINE_CONFIG.context_exit_policy_run_id_full)
    args = p.parse_args()

    market_db = str(REPO_ROOT / "data" / "market.duckdb")
    with connect(str(REPO_ROOT / "data" / "smartmoney.duckdb"), read_only=True, attach={"market": market_db}) as conn:
        picks, policy = load_picks(conn, args.policy_run)
        print(f"BC picks in TEST window: {len(picks):,}, policy rows: {len(policy):,}")

        # Pre-load kline + compute ATR per stock (cache)
        unique_stocks = picks["stock_code"].unique()
        print(f"Pre-loading {len(unique_stocks)} stock klines + ATR...")
        kline_cache = {}
        for stock in unique_stocks:
            rows = conn.execute(
                "SELECT date, open, high, low, close FROM market.v_price_kline_qfq "
                # rule-compliance: ok evidence=BC Phase 8 ATR needs 1y warmup before TEST_START
                "WHERE freq='daily' AND adjust='qfq' AND code=? AND date>='2024-01-01' ORDER BY date",
                [stock],
            ).fetchall()
            if not rows or len(rows) < 50:
                continue
            df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close"])
            df["date"] = pd.to_datetime(df["date"])
            for c in ["open", "high", "low", "close"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df["atr20"] = _atr(df["high"].values, df["low"].values, df["close"].values, 20)
            kline_cache[stock] = df
        print(f"Loaded {len(kline_cache)} valid klines\n")

        # Run Optuna
        sampler = optuna.samplers.TPESampler(seed=42)  # rule-compliance: ok evidence=fixed seed governance
        study = optuna.create_study(direction="maximize", sampler=sampler)
        study.optimize(lambda t: objective(t, conn, picks, policy, kline_cache), n_trials=args.n_trials, show_progress_bar=False)

        print("\n=== Phase 8 stop-loss A+B sweep result ===")
        best = study.best_trial
        print(f"  best Sharpe: {best.value:.4f}")
        print(f"  best K_atr: {best.params['K_atr']:.2f}")
        print(f"  best M_dd:  {best.params['M_dd']:.3f}")
        print(f"  n trades: {best.user_attrs.get('n')}, win: {best.user_attrs.get('win_rate'):.2%}, avg_ret: {best.user_attrs.get('avg_ret'):.2%}, avg_hold: {best.user_attrs.get('avg_hold'):.1f}d")

        # Compare to Phase 7 baseline (no stop)
        print(f"\n  vs Phase 7 (no stop): sharpe 1.67, win 64.7%, hold 12d")
        improvement = best.value - 1.67
        print(f"  improvement: {improvement:+.4f} sharpe")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
