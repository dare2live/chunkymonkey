#!/usr/bin/env python3
"""BC Phase 7 POC: 条件化持有/退出 策略 (context-aware sell policy).

Per goal.md plan §5 Phase 7 + line 374:
Context buckets: stock_code + formula_id + variant_id + stage + macd_context +
  market/industry_regime + volatility_bucket + kline_pattern
MACD context: zero_axis_above_golden_cross / zero_axis_below_golden_cross /
  dead_cross / above_zero_trend_continuation / below_zero_rebound_probe
Action space: fixed_5/10/15/20/30, formula_exit_or_N, 死叉退出, 阶段恶化退出,
  trailing_stop, profit_target + time_stop, max_holding + early_exit, regime_risk_off_exit

POC scope (本地 walk-forward 小样本, 不 GCP):
- 取 BC top 100 candidates (highest score)
- 每 candidate: 跑 formula on K-line → entries
- per entry: 算 macd_context bucket
- per bucket: 尝试 5 sell rules, paper-fwd 算 metrics
- 选 best sell_rule per bucket → write to mart_bestchoice_context_exit_policy_v1
- Fallback level: stock+formula+context → formula+context → formula default
- PIT-safe: walk-forward T+1 buy, no future leak

Output: mart_bestchoice_context_exit_policy_v1 + paper_sim metrics
Threshold gate (per goal.md): Sharpe>=1.3 OR ann>=50%/dd>=-25% → 才 Phase 8 GCP
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

# rule-compliance: ok evidence=BC plan §5 Phase 7 POC config
DEFAULT_RUN_ID = "bestchoice_formula_optuna_20260521_v1"
POLICY_RUN_ID = "bestchoice_context_exit_v1_20260522"
DEFAULT_TOP_N = 100


def _macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_f = pd.Series(close).ewm(span=fast, adjust=False).mean().values
    ema_s = pd.Series(close).ewm(span=slow, adjust=False).mean().values
    dif = ema_f - ema_s
    dea = pd.Series(dif).ewm(span=signal, adjust=False).mean().values
    macd = (dif - dea) * 2
    return dif, dea, macd


def _classify_macd_context(dif: float, dea: float, prev_dif: float, prev_dea: float) -> str:
    """5 categories from goal.md plan §5 Phase 7 spec."""
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


def _compute_sell_metrics(buy_price: float, future_closes: np.ndarray, hold_days: int) -> dict:
    if hold_days >= len(future_closes) or buy_price <= 0:
        return {"ret": None, "max_dd": None}
    sell_price = future_closes[hold_days]
    if not np.isfinite(sell_price) or sell_price <= 0:
        return {"ret": None, "max_dd": None}
    ret = sell_price / buy_price - 1.0
    path = future_closes[:hold_days + 1]
    if len(path) > 0:
        peak = np.maximum.accumulate(path)
        dd = (path - peak) / np.where(peak > 0, peak, 1.0)
        max_dd = float(dd.min())
    else:
        max_dd = 0.0
    return {"ret": float(ret), "max_dd": max_dd}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-path", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    p.add_argument("--run-id", default=DEFAULT_RUN_ID)
    p.add_argument("--policy-run-id", default=POLICY_RUN_ID)
    p.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    args = p.parse_args()

    market_db = str(REPO_ROOT / "data" / "market.duckdb")
    sell_rules_to_test = [5, 10, 15, 20, 30]
    # rule-compliance: ok evidence=walk-forward split prevents in-sample fit (CLAUDE.md §1.4 真金白银)
    # Train: 2023-01-03 to 2024-12-31 (~2 years). Test: 2025-01-01 to 2026-04-13 (~16 months).
    TRAIN_END = "2024-12-31"  # rule-compliance: ok evidence=BC POC walk-forward train_end (panel coverage to 2026-04)
    TEST_END = "2026-04-13"    # rule-compliance: ok evidence=panel max signal_date (V4 OOS upper bound)

    with connect(args.db_path, read_only=False, attach={"market": market_db}) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mart_bestchoice_context_exit_policy_v1 (
                policy_run_id VARCHAR,
                stock_code VARCHAR,
                formula_id VARCHAR,
                macd_context VARCHAR,
                best_sell_rule VARCHAR,
                holding_days INTEGER,
                n_train_signals INTEGER,
                avg_ret DOUBLE,
                win_rate DOUBLE,
                avg_max_dd DOUBLE,
                sharpe_like DOUBLE,
                confidence VARCHAR,
                fallback_level VARCHAR,
                built_at TIMESTAMP,
                PRIMARY KEY (policy_run_id, stock_code, formula_id, macd_context)
            )
            """
        )
        conn.execute("DELETE FROM mart_bestchoice_context_exit_policy_v1 WHERE policy_run_id = ?",
                     [args.policy_run_id])

        cands = conn.execute(
            "SELECT stock_code, formula_id, params_json, sell_rule "
            "FROM mart_stock_formula_optuna_bestchoice_v1 "
            "WHERE run_id = ? ORDER BY score DESC LIMIT ?",
            [args.run_id, int(args.top_n)],
        ).fetchall()
        print(f"[Phase 7 POC] processing {len(cands)} top candidates")

        # rule-compliance: ok evidence=panel start_date alignment
        START_DATE = "2023-01-03"
        policy_rows = []
        for i, (stock, formula_id, params_json, _sell_rule) in enumerate(cands):
            try:
                params = json.loads(params_json) if params_json else {}
            except Exception:
                params = {}
            df = pd.DataFrame(
                conn.execute(
                    "SELECT date, open, high, low, close FROM market.v_price_kline_qfq "
                    "WHERE freq='daily' AND adjust='qfq' AND code=? AND date>=? ORDER BY date",
                    [stock, START_DATE],
                ).fetchall(),
                columns=["date", "open", "high", "low", "close"],
            )
            if df.empty or len(df) < 60:
                continue
            df["date"] = pd.to_datetime(df["date"])
            for c in ["open", "high", "low", "close"]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            try:
                volume_arr = np.ones(len(df), dtype=float) * 1e6
                amount_arr = volume_arr * df["close"].fillna(0).values
                result = compute_formula_signals(
                    formula_id,
                    open_=df["open"].values, high=df["high"].values, low=df["low"].values,
                    close=df["close"].values, volume=volume_arr, amount=amount_arr,
                    params=params,
                )
            except Exception:
                continue
            entry = np.asarray(result.get("entry", []), dtype=bool)
            if not entry.any():
                continue
            close = df["close"].values
            dif, dea, _ = _macd(close)

            # Walk-forward: split trades into TRAIN (<= TRAIN_END) and TEST (TRAIN_END..TEST_END)
            train_bucket: dict[str, list[dict]] = {}
            test_bucket: dict[str, list[dict]] = {}
            entry_idx = np.where(entry)[0]
            for idx in entry_idx:
                if idx < 1 or idx + 30 >= len(close):
                    continue
                ctx = _classify_macd_context(dif[idx], dea[idx], dif[idx - 1], dea[idx - 1])
                buy_price = close[idx + 1] if idx + 1 < len(close) else None
                if buy_price is None or not np.isfinite(buy_price) or buy_price <= 0:
                    continue
                buy_date = df.iloc[idx + 1]["date"]
                future = close[idx + 1:]
                # Period classification
                buy_date_str = str(buy_date.date())
                if buy_date_str <= TRAIN_END:
                    target = train_bucket
                elif buy_date_str <= TEST_END:
                    target = test_bucket
                else:
                    continue
                for hold_d in sell_rules_to_test:
                    metrics = _compute_sell_metrics(buy_price, future, hold_d)
                    if metrics["ret"] is None:
                        continue
                    target.setdefault(ctx, []).append({
                        "buy_date": buy_date_str,
                        "hold_days": hold_d,
                        **metrics,
                    })

            # Per context: pick best sell_rule on TRAIN, evaluate on TEST
            for ctx in set(train_bucket.keys()) | set(test_bucket.keys()):
                train_list = train_bucket.get(ctx, [])
                test_list = test_bucket.get(ctx, [])
                if not train_list:
                    continue
                df_train = pd.DataFrame(train_list)
                # Group TRAIN by hold_days, find best
                train_grouped = df_train.groupby("hold_days").agg(
                    n_train=("ret", "count"),
                    train_ret=("ret", "mean"),
                    train_std=("ret", "std"),
                ).reset_index()
                train_grouped["train_sharpe"] = train_grouped["train_ret"] / train_grouped["train_std"].replace(0, np.nan) * np.sqrt(252.0 / train_grouped["hold_days"])
                train_grouped = train_grouped.dropna(subset=["train_sharpe"])
                if train_grouped.empty:
                    continue
                best_train = train_grouped.loc[train_grouped["train_sharpe"].idxmax()]
                best_hd = int(best_train["hold_days"])
                # Evaluate on TEST with that fixed hold_days
                if test_list:
                    df_test = pd.DataFrame([t for t in test_list if t["hold_days"] == best_hd])
                    if not df_test.empty:
                        test_n = int(len(df_test))
                        test_ret = float(df_test["ret"].mean())
                        test_win = float((df_test["ret"] > 0).mean())
                        test_dd = float(df_test["max_dd"].mean())
                        test_std = float(df_test["ret"].std())
                        test_sharpe = test_ret / test_std * np.sqrt(252.0 / best_hd) if test_std > 0 else 0.0
                    else:
                        test_n, test_ret, test_win, test_dd, test_sharpe = 0, 0.0, 0.0, 0.0, 0.0
                else:
                    test_n, test_ret, test_win, test_dd, test_sharpe = 0, 0.0, 0.0, 0.0, 0.0
                policy_rows.append({
                    "policy_run_id": args.policy_run_id,
                    "stock_code": stock,
                    "formula_id": formula_id,
                    "macd_context": ctx,
                    "best_sell_rule": f"fixed_{best_hd}",
                    "holding_days": best_hd,
                    "n_train_signals": int(best_train["n_train"]),
                    "avg_ret": test_ret,   # TEST metrics (not train)
                    "win_rate": test_win,
                    "avg_max_dd": test_dd,
                    "sharpe_like": test_sharpe,
                    "confidence": "high" if test_n >= 5 else "low",
                    "fallback_level": f"stock+formula+context (n_train={int(best_train['n_train'])}, n_test={test_n})",
                    "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                })
            if (i + 1) % 20 == 0:
                print(f"  processed {i+1}/{len(cands)}")

        if not policy_rows:
            print("[Phase 7 POC] 0 policy rows generated, abort")
            return 1

        # Insert
        for row in policy_rows:
            conn.execute(
                "INSERT INTO mart_bestchoice_context_exit_policy_v1 VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [row["policy_run_id"], row["stock_code"], row["formula_id"],
                 row["macd_context"], row["best_sell_rule"], row["holding_days"],
                 row["n_train_signals"], row["avg_ret"], row["win_rate"],
                 row["avg_max_dd"], row["sharpe_like"], row["confidence"],
                 row["fallback_level"], row["built_at"]],
            )
        conn.commit()

        # Summary
        print(f"\n[Phase 7 POC] inserted {len(policy_rows)} policy rows")
        df_p = pd.DataFrame(policy_rows)
        print("\n=== per macd_context summary ===")
        ctx_summary = df_p.groupby("macd_context").agg(
            n=("stock_code", "count"),
            avg_holding=("holding_days", "mean"),
            avg_win_rate=("win_rate", "mean"),
            avg_ret=("avg_ret", "mean"),
            avg_sharpe=("sharpe_like", "mean"),
        ).round(4)
        print(ctx_summary)

        # Phase 7 threshold gate (per goal.md)
        avg_sharpe = float(df_p["sharpe_like"].mean())
        avg_ret = float(df_p["avg_ret"].mean()) * 252 / df_p["holding_days"].mean()  # annualized approx
        avg_dd = float(df_p["avg_max_dd"].mean())
        print(f"\n=== Phase 7 gate check ===")
        print(f"  avg sharpe (per-context): {avg_sharpe:.3f}")
        print(f"  annualized ret approx: {avg_ret:.2%}")
        print(f"  avg max_dd: {avg_dd:.2%}")
        gate_pass = avg_sharpe >= 1.3 or (avg_ret >= 0.50 and avg_dd >= -0.25)
        print(f"  Phase 8 GCP gate: {'PASS' if gate_pass else 'FAIL'} (threshold: sharpe>=1.3 OR ann>=50% dd>=-25%)")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
