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
KLINE_START_DATE = "2023-07-01"  # rule-compliance: ok evidence=MACD warmup 6mo before panel start 2024-01-02


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


def _raw_conn(conn):
    return getattr(conn, "_con", conn)


def _register_frame(conn, name: str, frame: pd.DataFrame):
    raw = _raw_conn(conn)
    raw.register(name, frame)
    return raw


def _unregister_frame(raw, name: str) -> None:
    raw.unregister(name)


def _load_kline_frame(conn, stock_codes) -> pd.DataFrame:
    codes = sorted({str(code) for code in stock_codes if code})
    columns = ["stock_code", "date", "close"]
    if not codes:
        return pd.DataFrame(columns=columns)
    view_name = "_phase7_context_codes"
    raw = _register_frame(conn, view_name, pd.DataFrame({"code": codes}))
    try:
        rows = conn.execute(
            f"""
            SELECT k.code AS stock_code, k.date, k.close
              FROM market.v_price_kline_qfq k
              JOIN {view_name} c ON c.code = k.code
             WHERE k.freq = 'daily'
               AND k.adjust = 'qfq'
               AND k.date >= ?
             ORDER BY k.code, k.date
            """,
            [KLINE_START_DATE],
        ).fetchall()
    finally:
        _unregister_frame(raw, view_name)
    return pd.DataFrame(rows, columns=columns)


def _context_labels(close_arr) -> list[str | None]:
    dif, dea = _macd(close_arr)
    return [
        _classify(dif[i], dea[i], dif[i - 1], dea[i - 1]) if i >= 1 else None
        for i in range(len(close_arr))
    ]


def _contexts_for_stock(stock: str, frame: pd.DataFrame) -> dict[tuple[str, pd.Timestamp], str]:
    if len(frame) < 30:
        return {}
    labels = _context_labels(frame["close"].values)
    return {
        (stock, date): ctx
        for date, ctx in zip(frame["date"], labels)
        if ctx
    }


def _build_contexts(kline: pd.DataFrame) -> dict[tuple[str, pd.Timestamp], str]:
    if kline.empty:
        return {}
    frame = kline.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["close"])
    frame = frame.sort_values(["stock_code", "date"])
    contexts: dict[tuple[str, pd.Timestamp], str] = {}
    for stock, group in frame.groupby("stock_code", sort=False):
        contexts.update(_contexts_for_stock(str(stock), group))
    return contexts


def _apply_context_filter(v7: pd.DataFrame, contexts: dict[tuple[str, pd.Timestamp], str]) -> pd.DataFrame:
    out = v7.copy()
    keys = zip(out["stock_code"], out["signal_date"])
    out["ctx"] = [contexts.get((str(stock), signal_date)) for stock, signal_date in keys]
    out["score_out"] = out["v7_score"].where(out["ctx"].isin(POSITIVE_CONTEXTS), None)
    return out


def main() -> int:
    market_db = str(REPO_ROOT / "data" / "market.duckdb")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")  # Phase ψ.5 allowlist: built_at lineage 非 trade_date

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
        print("Computing MACD context (bulk kline cache)...")
        contexts = _build_contexts(_load_kline_frame(conn, v7["stock_code"].unique()))
        print(f"  computed contexts: {len(contexts):,}")

        # Build ensemble: v7 score IF context in positive whitelist ELSE NULL
        v7 = _apply_context_filter(v7, contexts)
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
