#!/usr/bin/env python3
"""BestChoice walk-forward lite audit (BC Phase 5).

Without re-running optuna search, audit selection bias by testing each BC candidate's
fixed params on chronological windows. Measures per-window metric stability.

Method:
  For each BC candidate (stock_code, formula_id, params):
    1. Load K-line 2023-2026 for stock
    2. Run formula → get entry signals
    3. Split signals by buy_date into windows:
       W1: pre-cutoff_1 (e.g. < 2024-06-01)
       W2: cutoff_1 to cutoff_2 (e.g. 2024-06-01 to 2025-01-01)
       W3: cutoff_2 onwards (e.g. >= 2025-01-01)
    4. Compute per-window metrics (n_signals, win_rate, avg_ret, avg_dd)
  Aggregate: compare distribution of (W1 vs W2 vs W3) metrics across all candidates.

Output: data/reports/bestchoice_walkforward_lite/audit_<ts>.csv + summary stats.

Verdict heuristic:
  - W1 (train period) >> W2, W3 → strong selection bias (optimal in fit, weak forward)
  - W1 ≈ W2 ≈ W3 → params robust across windows
  - W3 > W1 → bias toward late (full-period optimization weighted recent)

Limitation: this audits PARAM stability, not SELECTION bias (selection of 1146 from 26K
was done with full-period info — that would require full re-optimization which is infeasible
locally). But this lite test gives indicative evidence cheaper than full walk-forward.
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
BESTCHOICE_ROOT = REPO_ROOT / "bestchoice"  # 2026-05-22 moved sibling → main project subdir
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(BESTCHOICE_ROOT))

from services.duck_adapter import connect  # noqa: E402
from services.bestchoice_config import DEFAULT_BESTCHOICE_PIPELINE_CONFIG  # noqa: E402
from formula_engine import compute_formula_signals  # noqa: E402

# rule-compliance: ok evidence=BC plan §5 audit cutoffs aligned with paper_sim period 2024-07→2026-04
DEFAULT_CUTOFFS = list(DEFAULT_BESTCHOICE_PIPELINE_CONFIG.walkforward_cutoffs)
DEFAULT_HOLDING_DAYS = 20  # rule-compliance: ok evidence=plan §5 default holding when sell_rule not fixed_N
DEFAULT_RUN_ID = DEFAULT_BESTCHOICE_PIPELINE_CONFIG.bc_run_id


# rule-compliance: ok evidence=BC plan §5 audit aligned with panel start_date 2023-01-03
def _load_kline(conn, stock_code: str, start_date: str = DEFAULT_BESTCHOICE_PIPELINE_CONFIG.walkforward_start_date) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT date, open, high, low, close, volume, amount
          FROM market.v_price_kline_qfq
         WHERE freq = 'daily'
           AND adjust = 'qfq'
           AND code = ?
           AND date >= ?
         ORDER BY date
        """,
        [stock_code, start_date],
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _holding_days(sell_rule: str, default: int = DEFAULT_HOLDING_DAYS) -> int:
    if sell_rule and sell_rule.startswith("fixed_"):
        try:
            return int(sell_rule.replace("fixed_", ""))
        except ValueError:
            # rule-compliance: ok evidence=fallback to default holding_days if sell_rule format unexpected
            return default
    if sell_rule and "_or_" in sell_rule:  # formula_exit_or_N
        try:
            return int(sell_rule.split("_or_")[-1])
        except ValueError:
            # rule-compliance: ok evidence=fallback to default holding_days if sell_rule format unexpected
            return default
    return default


def _compute_window_metrics(buy_dates: list[pd.Timestamp], rets: list[float], window_start: str | None, window_end: str | None) -> dict:
    """Compute n / win_rate / avg_ret / std for trades within [window_start, window_end)."""
    ws = pd.to_datetime(window_start) if window_start else pd.Timestamp.min
    we = pd.to_datetime(window_end) if window_end else pd.Timestamp.max
    filtered_rets = [r for d, r in zip(buy_dates, rets) if ws <= d < we and np.isfinite(r)]
    if not filtered_rets:
        return {"n": 0, "win_rate": None, "avg_ret": None, "std_ret": None}
    arr = np.array(filtered_rets)
    return {
        "n": len(arr),
        "win_rate": round(float((arr > 0).mean()), 4),
        "avg_ret": round(float(arr.mean()), 6),
        "std_ret": round(float(arr.std()), 6),
    }


def _audit_one_candidate(conn, kline_cache: dict, candidate: dict, cutoffs: list[str]) -> dict:
    stock = candidate["stock_code"]
    formula_id = candidate["formula_id"]
    params_json = candidate.get("params_json") or "{}"
    sell_rule = candidate.get("sell_rule") or "fixed_20"
    hold_d = _holding_days(sell_rule)

    if stock not in kline_cache:
        kline_cache[stock] = _load_kline(conn, stock)
    df = kline_cache[stock]
    if df.empty or len(df) < hold_d + 10:
        return {"stock_code": stock, "formula_id": formula_id, "status": "no_kline_or_too_short"}

    try:
        params = json.loads(params_json) if params_json else {}
    except Exception:
        params = {}

    try:
        result = compute_formula_signals(
            formula_id,
            open_=df["open"].values,
            high=df["high"].values,
            low=df["low"].values,
            close=df["close"].values,
            volume=df["volume"].values,
            amount=df["amount"].values,
            params=params,
        )
    except Exception as exc:
        return {"stock_code": stock, "formula_id": formula_id, "status": f"formula_error: {type(exc).__name__}"}

    entry = np.asarray(result.get("entry", []), dtype=bool)
    if not entry.any():
        return {"stock_code": stock, "formula_id": formula_id, "status": "no_signal"}

    entry_idx = np.where(entry)[0]
    buy_dates = []
    rets = []
    for idx in entry_idx:
        if idx + hold_d >= len(df):
            continue
        bd = df.iloc[idx]["date"]
        bp = df.iloc[idx + 1]["open"] if idx + 1 < len(df) else None  # T+1 buy
        if bp is None or not np.isfinite(bp) or bp <= 0:
            continue
        sp = df.iloc[idx + 1 + hold_d]["close"] if idx + 1 + hold_d < len(df) else None
        if sp is None or not np.isfinite(sp) or sp <= 0:
            continue
        ret = sp / bp - 1.0
        buy_dates.append(bd)
        rets.append(ret)

    if not rets:
        return {"stock_code": stock, "formula_id": formula_id, "status": "no_valid_trade"}

    # Per-window metrics
    windows = ["W1_pre"] + [f"W{i+2}_{c}" for i, c in enumerate(cutoffs)] + [f"W{len(cutoffs)+2}_post"]
    boundaries = [None] + list(cutoffs) + [None]
    out = {"stock_code": stock, "formula_id": formula_id, "sell_rule": sell_rule, "holding_days": hold_d,
           "status": "ok", "total_signals": len(rets)}
    for i, name in enumerate(windows):
        start = boundaries[i]
        end = boundaries[i + 1] if i + 1 < len(boundaries) else None
        m = _compute_window_metrics(buy_dates, rets, start, end)
        out[f"{name}_n"] = m["n"]
        out[f"{name}_win_rate"] = m["win_rate"]
        out[f"{name}_avg_ret"] = m["avg_ret"]
        out[f"{name}_std_ret"] = m["std_ret"]
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-path", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    p.add_argument("--run-id", default=DEFAULT_RUN_ID)
    p.add_argument("--cutoffs", default=",".join(DEFAULT_CUTOFFS))
    p.add_argument("--limit", type=int, default=0, help="0 = all 1146")
    p.add_argument("--report-dir", default=str(REPO_ROOT / "data" / "reports" / "bestchoice_walkforward_lite"))
    args = p.parse_args()

    Path(args.report_dir).mkdir(parents=True, exist_ok=True)
    cutoffs = [c.strip() for c in args.cutoffs.split(",") if c.strip()]
    print(f"[bc-audit-lite] cutoffs: {cutoffs}")

    market_db = str(REPO_ROOT / "data" / "market.duckdb")
    with connect(args.db_path, read_only=True, attach={"market": market_db}) as conn:
        cands_sql = """
            SELECT stock_code, formula_id, variant_id, params_json, sell_rule, holding_days, score
              FROM mart_stock_formula_optuna_bestchoice_v1
             WHERE run_id = ?
             ORDER BY score DESC
        """
        if args.limit > 0:
            cands_sql += f" LIMIT {int(args.limit)}"
        cur = conn.execute(cands_sql, [args.run_id])
        cols = [d[0] for d in cur.description]
        cands = [dict(zip(cols, r)) for r in cur.fetchall()]
        print(f"[bc-audit-lite] {len(cands)} candidates to audit")

        kline_cache: dict = {}
        results: list[dict] = []
        for i, c in enumerate(cands):
            r = _audit_one_candidate(conn, kline_cache, c, cutoffs)
            results.append(r)
            if (i + 1) % 100 == 0:
                print(f"  processed {i+1}/{len(cands)}")

    now_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_csv = Path(args.report_dir) / f"audit_{now_ts}.csv"
    df = pd.DataFrame(results)
    df.to_csv(out_csv, index=False)
    print(f"\n[bc-audit-lite] saved: {out_csv}")

    # Summary
    ok = df[df["status"] == "ok"]
    print(f"\n=== Summary ({len(ok)}/{len(df)} candidates ok) ===")
    win_cols = [c for c in df.columns if c.endswith("_win_rate")]
    ret_cols = [c for c in df.columns if c.endswith("_avg_ret")]
    n_cols = [c for c in df.columns if c.endswith("_n") and c != "total_signals"]
    print("\n--- avg win_rate per window (across all candidates) ---")
    for c in win_cols:
        v = ok[c].dropna()
        print(f"  {c}: n={len(v)}, mean={v.mean():.4f}, median={v.median():.4f}")
    print("\n--- avg ret per window ---")
    for c in ret_cols:
        v = ok[c].dropna()
        print(f"  {c}: n={len(v)}, mean={v.mean():.4f}, median={v.median():.4f}")
    print("\n--- signal count per window ---")
    for c in n_cols:
        v = ok[c]
        print(f"  {c}: total signals={int(v.sum())}, candidates with >=1={int((v >= 1).sum())}")

    # Selection bias verdict
    print("\n=== Selection bias verdict ===")
    w_cols = [c for c in win_cols if c.startswith("W")]
    if len(w_cols) >= 2:
        first = ok[w_cols[0]].dropna().mean()
        last = ok[w_cols[-1]].dropna().mean()
        if first and last:
            drop_pct = (first - last) / first * 100 if first > 0 else 0
            print(f"  first window {w_cols[0]} avg win: {first:.4f}")
            print(f"  last window  {w_cols[-1]} avg win: {last:.4f}")
            print(f"  drop pct: {drop_pct:.2f}%")
            if drop_pct > 30:
                print(f"  >>> STRONG selection bias (drop > 30%)")
            elif drop_pct > 10:
                print(f"  >>> MILD selection bias (drop 10-30%)")
            else:
                print(f"  >>> WEAK / no selection bias (params robust across windows)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
