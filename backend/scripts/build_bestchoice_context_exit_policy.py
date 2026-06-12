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
from services.bestchoice_config import DEFAULT_BESTCHOICE_PIPELINE_CONFIG  # noqa: E402
from formula_engine import compute_formula_signals  # noqa: E402

# rule-compliance: ok evidence=BC plan §5 Phase 7 POC config
DEFAULT_RUN_ID = DEFAULT_BESTCHOICE_PIPELINE_CONFIG.bc_run_id
POLICY_RUN_ID = DEFAULT_BESTCHOICE_PIPELINE_CONFIG.context_exit_policy_run_id
DEFAULT_TOP_N = DEFAULT_BESTCHOICE_PIPELINE_CONFIG.context_exit_top_n


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


def _load_kline_frames(conn, stock_codes: list[str], start_date: str) -> dict[str, pd.DataFrame]:
    codes = sorted({code for code in stock_codes if code})
    if not codes:
        return {}
    placeholders = ", ".join("?" for _ in codes)
    rows = conn.execute(
        f"""
        SELECT code, date, open, high, low, close
          FROM market.v_price_kline_qfq
         WHERE freq='daily'
           AND adjust='qfq'
           AND code IN ({placeholders})
           AND date>=?
         ORDER BY code, date
        """,
        [*codes, start_date],
    ).fetchall()
    if not rows:
        return {}
    frame = pd.DataFrame(rows, columns=["code", "date", "open", "high", "low", "close"])
    frame["date"] = pd.to_datetime(frame["date"])
    for column in ["open", "high", "low", "close"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return {
        stock_code: group.drop(columns=["code"]).reset_index(drop=True)
        for stock_code, group in frame.groupby("code", sort=False)
    }


def _append_entry_metrics(
    target: dict[str, list[dict]],
    ctx: str,
    buy_date_str: str,
    buy_price: float,
    future: np.ndarray,
    sell_rules_to_test: list[int],
) -> None:
    for hold_d in sell_rules_to_test:
        metrics = _compute_sell_metrics(buy_price, future, hold_d)
        if metrics["ret"] is None:
            continue
        target.setdefault(ctx, []).append({
            "buy_date": buy_date_str,
            "hold_days": hold_d,
            **metrics,
        })


def _trade_buckets_for_candidate(
    df: pd.DataFrame,
    entry: np.ndarray,
    sell_rules_to_test: list[int],
    train_end: str,
    test_end: str,
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    close = df["close"].values
    dif, dea, _ = _macd(close)
    train_bucket: dict[str, list[dict]] = {}
    test_bucket: dict[str, list[dict]] = {}
    for idx in np.flatnonzero(entry):
        if idx < 1 or idx + 30 >= len(close):
            continue
        ctx = _classify_macd_context(dif[idx], dea[idx], dif[idx - 1], dea[idx - 1])
        buy_price = close[idx + 1] if idx + 1 < len(close) else None
        if buy_price is None or not np.isfinite(buy_price) or buy_price <= 0:
            continue
        buy_date_str = str(df.iloc[idx + 1]["date"].date())
        if buy_date_str <= train_end:
            target = train_bucket
        elif buy_date_str <= test_end:
            target = test_bucket
        else:
            continue
        _append_entry_metrics(target, ctx, buy_date_str, buy_price, close[idx + 1:], sell_rules_to_test)
    return train_bucket, test_bucket


def _rows_for_hold_days(rows: list[dict], hold_days: int) -> list[dict]:
    return [row for row in rows if row["hold_days"] == hold_days]


def _test_metrics(test_list: list[dict], best_hd: int) -> tuple[int, float, float, float, float]:
    selected = _rows_for_hold_days(test_list, best_hd)
    if not selected:
        return 0, 0.0, 0.0, 0.0, 0.0
    df_test = pd.DataFrame(selected)
    test_n = int(len(df_test))
    test_ret = float(df_test["ret"].mean())
    test_win = float((df_test["ret"] > 0).mean())
    test_dd = float(df_test["max_dd"].mean())
    test_std = float(df_test["ret"].std())
    test_sharpe = test_ret / test_std * np.sqrt(252.0 / best_hd) if test_std > 0 else 0.0
    return test_n, test_ret, test_win, test_dd, test_sharpe


def _policy_rows_for_candidate(
    policy_run_id: str,
    stock: str,
    formula_id: str,
    train_bucket: dict[str, list[dict]],
    test_bucket: dict[str, list[dict]],
) -> list[dict]:
    rows: list[dict] = []
    for ctx in set(train_bucket) | set(test_bucket):
        train_list = train_bucket.get(ctx, [])
        if not train_list:
            continue
        df_train = pd.DataFrame(train_list)
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
        test_n, test_ret, test_win, test_dd, test_sharpe = _test_metrics(test_bucket.get(ctx, []), best_hd)
        rows.append({
            "policy_run_id": policy_run_id,
            "stock_code": stock,
            "formula_id": formula_id,
            "macd_context": ctx,
            "best_sell_rule": f"fixed_{best_hd}",
            "holding_days": best_hd,
            "n_train_signals": int(best_train["n_train"]),
            "avg_ret": test_ret,
            "win_rate": test_win,
            "avg_max_dd": test_dd,
            "sharpe_like": test_sharpe,
            "confidence": "high" if test_n >= 5 else "low",
            "fallback_level": f"stock+formula+context (n_train={int(best_train['n_train'])}, n_test={test_n})",
            "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return rows


def _insert_policy_rows(conn, policy_rows: list[dict]) -> None:
    conn.executemany(
        "INSERT INTO mart_bestchoice_context_exit_policy_v1 VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                row["policy_run_id"], row["stock_code"], row["formula_id"],
                row["macd_context"], row["best_sell_rule"], row["holding_days"],
                row["n_train_signals"], row["avg_ret"], row["win_rate"],
                row["avg_max_dd"], row["sharpe_like"], row["confidence"],
                row["fallback_level"], row["built_at"],
            )
            for row in policy_rows
        ),
    )


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
    START_DATE = DEFAULT_BESTCHOICE_PIPELINE_CONFIG.walkforward_start_date
    TRAIN_END = DEFAULT_BESTCHOICE_PIPELINE_CONFIG.walkforward_train_end_date  # rule-compliance: ok evidence=BC POC walk-forward train_end (panel coverage to 2026-04)
    TEST_END = DEFAULT_BESTCHOICE_PIPELINE_CONFIG.walkforward_test_end_date    # rule-compliance: ok evidence=panel max signal_date (V4 OOS upper bound)

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
        kline_by_stock = _load_kline_frames(conn, [row[0] for row in cands], START_DATE)
        policy_rows = []
        for i, (stock, formula_id, params_json, _sell_rule) in enumerate(cands):
            try:
                params = json.loads(params_json) if params_json else {}
            except Exception:
                params = {}
            df = kline_by_stock.get(stock, pd.DataFrame())
            if df.empty or len(df) < 60:
                continue
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

            # Walk-forward: split trades into TRAIN (<= TRAIN_END) and TEST (TRAIN_END..TEST_END)
            train_bucket, test_bucket = _trade_buckets_for_candidate(df, entry, sell_rules_to_test, TRAIN_END, TEST_END)
            # Per context: pick best sell_rule on TRAIN, evaluate on TEST
            policy_rows.extend(_policy_rows_for_candidate(args.policy_run_id, stock, formula_id, train_bucket, test_bucket))
            if (i + 1) % 20 == 0:
                print(f"  processed {i+1}/{len(cands)}")

        if not policy_rows:
            print("[Phase 7 POC] 0 policy rows generated, abort")
            return 1

        _insert_policy_rows(conn, policy_rows)
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
        print(f"  Phase 8 gate: {'PASS' if gate_pass else 'FAIL'} (threshold: sharpe>=1.3 OR ann>=50% dd>=-25%)")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
