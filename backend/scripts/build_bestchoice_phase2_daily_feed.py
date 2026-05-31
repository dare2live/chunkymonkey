#!/usr/bin/env python3
"""BestChoice Phase 2 — build daily candidate simulation feed.

Per `bestchoice/analysis/bestchoice_chunkymonkey_validation_plan.md` §5 Phase 2:
- For each trading day, detect formula triggers per (stock_code, formula_id) candidate
- Join to optimized params from mart_stock_formula_optuna_bestchoice_v1
- Rank same-day candidates by confidence score
- Emit daily candidate feed to mart_daily_formula_candidate_bestchoice_v1

This is a read-only challenger feed; it does not modify champion v4 mart tables.
"""

from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
BESTCHOICE_ROOT = REPO_ROOT / "bestchoice"  # 2026-05-22 moved sibling → main project subdir
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(BESTCHOICE_ROOT))

from services.duck_adapter import connect  # noqa: E402
from formula_engine import compute_formula_signals  # noqa: E402

DEFAULT_RUN_ID = "bestchoice_formula_optuna_20260521_v1"
FEED_COLUMNS = [
    "run_id", "signal_date", "buy_date", "stock_code", "formula_id", "variant_id",
    "sell_rule", "holding_days", "confidence_score", "expected_return",
    "expected_drawdown", "historical_win_rate", "validation_win_rate",
    "rank_in_date", "created_at",
]


def _load_kline(conn, stock_code: str) -> pd.DataFrame:
    """Load qfq daily kline for a stock from main project market db (already attached)."""
    rows = conn.execute(
        """
        SELECT date, open, high, low, close, volume, amount
          FROM market.v_price_kline_qfq
         WHERE freq = 'daily'
           AND adjust = 'qfq'
           AND code = ?
         ORDER BY date
        """,
        [stock_code],
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    return df


def _buy_date_after_signal(signal_date: pd.Timestamp, trading_days: list[pd.Timestamp]) -> pd.Timestamp | None:
    pos = bisect_right(trading_days, signal_date)
    return trading_days[pos] if pos < len(trading_days) else None


def _entry_indices(entry: np.ndarray) -> np.ndarray:
    return np.flatnonzero(entry)


def _append_feed_rows_for_entries(
    feed_rows: list[tuple],
    entry_idx: np.ndarray,
    df: pd.DataFrame,
    start_dt: pd.Timestamp,
    max_trading_day: pd.Timestamp,
    trading_days: list[pd.Timestamp],
    row_template: tuple,
) -> None:
    (
        run_id,
        stock_code,
        formula_id,
        variant_id,
        sell_rule,
        holding_days,
        conf_score,
        avg_ret,
        avg_dd,
        hist_win,
        val_win,
        now_utc,
    ) = row_template
    for idx in entry_idx:
        signal_date = df.iloc[idx]["date"]
        if signal_date < start_dt or signal_date > max_trading_day:
            continue
        buy_date = _buy_date_after_signal(signal_date, trading_days)
        if buy_date is None:
            continue
        feed_rows.append(
            (
                run_id,
                signal_date.date(),
                buy_date.date(),
                stock_code,
                formula_id,
                variant_id,
                sell_rule,
                holding_days,
                conf_score,
                avg_ret,
                avg_dd,
                hist_win,
                val_win,
                None,
                now_utc,
            )
        )


def _rank_and_deduplicate_feed_rows(feed_rows: list[tuple]) -> tuple[pd.DataFrame, int]:
    feed_df = pd.DataFrame(feed_rows, columns=FEED_COLUMNS)
    feed_df["rank_in_date"] = (
        feed_df.groupby("signal_date")["confidence_score"]
        .rank(method="dense", ascending=False)
        .astype("int64")
        .apply(int)
    )
    before_dedup = len(feed_df)
    feed_df = (
        feed_df.sort_values(["signal_date", "stock_code", "confidence_score"], ascending=[True, True, False])
        .drop_duplicates(subset=["signal_date", "stock_code"], keep="first")
    )
    return feed_df, before_dedup - len(feed_df)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(REPO_ROOT / "data" / "smartmoney.duckdb"))
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--limit-candidates", type=int, default=0, help="0 = no limit")
    # rule-compliance: ok evidence=main project paper_sim period_start 2023-01-03 (mart_paper_sim_kpi)
    parser.add_argument("--start-date", default="2023-01-03")
    args = parser.parse_args()

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    start_dt = pd.to_datetime(args.start_date)

    market_db = str(REPO_ROOT / "data" / "market.duckdb")
    with connect(args.db_path, read_only=False, attach={"market": market_db}) as conn:
        # Ensure target table
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mart_daily_formula_candidate_bestchoice_v1 (
                run_id VARCHAR,
                signal_date DATE,
                buy_date DATE,
                stock_code VARCHAR,
                formula_id VARCHAR,
                variant_id VARCHAR,
                sell_rule VARCHAR,
                holding_days INTEGER,
                confidence_score DOUBLE,
                expected_return DOUBLE,
                expected_drawdown DOUBLE,
                historical_win_rate DOUBLE,
                validation_win_rate DOUBLE,
                rank_in_date INTEGER,
                created_at TIMESTAMP
            )
            """
        )
        conn.execute(
            "DELETE FROM mart_daily_formula_candidate_bestchoice_v1 WHERE run_id = ?",
            [args.run_id],
        )

        # Read candidate config
        cand_rows = conn.execute(
            """
            SELECT stock_code, formula_id, variant_id, params_json, sell_rule, holding_days,
                   score, avg_ret, avg_dd, win_rate, validation_win_rate
              FROM mart_stock_formula_optuna_bestchoice_v1
             WHERE run_id = ?
             ORDER BY stock_code, formula_id
            """,
            [args.run_id],
        ).fetchall()
        if args.limit_candidates > 0:
            cand_rows = cand_rows[: args.limit_candidates]
        n_candidates = len(cand_rows)
        print(f"[phase2] processing {n_candidates} candidates from run_id={args.run_id}")

        # Trading calendar (one fetch, reused)
        trading_days = [
            pd.to_datetime(r[0])
            for r in conn.execute(
                "SELECT trade_date FROM dim_trading_calendar WHERE is_trading = 1 ORDER BY trade_date"
            ).fetchall()
        ]
        max_trading_day = max(trading_days)
        print(f"[phase2] trading calendar: {len(trading_days)} days, max={max_trading_day.date()}")

        feed_rows: list[tuple] = []
        kline_cache: dict[str, pd.DataFrame] = {}
        skipped_no_kline = 0
        skipped_no_entry = 0

        for i, cand in enumerate(cand_rows):
            (
                stock_code,
                formula_id,
                variant_id,
                params_json,
                sell_rule,
                holding_days,
                conf_score,
                avg_ret,
                avg_dd,
                hist_win,
                val_win,
            ) = cand

            if stock_code not in kline_cache:
                kline_cache[stock_code] = _load_kline(conn, stock_code)
            df = kline_cache[stock_code]
            if df.empty:
                skipped_no_kline += 1
                continue

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
            except Exception as e:
                print(f"  [skip] {stock_code} {formula_id}: {e}")
                skipped_no_entry += 1
                continue

            entry = np.asarray(result.get("entry", []), dtype=bool)
            if not entry.any():
                skipped_no_entry += 1
                continue

            row_template = (
                args.run_id,
                stock_code,
                formula_id,
                variant_id,
                sell_rule,
                holding_days,
                conf_score,
                avg_ret,
                avg_dd,
                hist_win,
                val_win,
                now_utc,
            )
            _append_feed_rows_for_entries(
                feed_rows,
                _entry_indices(entry),
                df,
                start_dt,
                max_trading_day,
                trading_days,
                row_template,
            )

            if (i + 1) % 100 == 0:
                print(f"  [phase2] processed {i+1}/{n_candidates} candidates, {len(feed_rows)} signals so far")

        print(f"[phase2] generated {len(feed_rows)} raw signals")
        print(f"  skipped_no_kline={skipped_no_kline}, skipped_no_entry={skipped_no_entry}")

        if not feed_rows:
            print("[phase2] ERROR: 0 signals generated, no output")
            return 1

        # Assign rank before deduplicating: downstream consumers expect rank gaps when
        # same-day duplicate stock signals occupied score slots in the raw feed.
        feed_df, dedup_dropped = _rank_and_deduplicate_feed_rows(feed_rows)

        # Insert
        conn.executemany(
            """
            INSERT INTO mart_daily_formula_candidate_bestchoice_v1 (
                run_id, signal_date, buy_date, stock_code, formula_id, variant_id, sell_rule,
                holding_days, confidence_score, expected_return, expected_drawdown,
                historical_win_rate, validation_win_rate, rank_in_date, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            list(feed_df.itertuples(index=False, name=None)),
        )
        conn.commit()

        audit = conn.execute(
            """
            SELECT COUNT(*) AS n_rows,
                   COUNT(DISTINCT signal_date) AS n_dates,
                   COUNT(DISTINCT stock_code) AS n_stocks,
                   COUNT(DISTINCT formula_id) AS n_formulas,
                   MIN(signal_date), MAX(signal_date),
                   AVG(confidence_score), AVG(historical_win_rate)
              FROM mart_daily_formula_candidate_bestchoice_v1
             WHERE run_id = ?
            """,
            [args.run_id],
        ).fetchone()
        print(f"\n[OK] mart_daily_formula_candidate_bestchoice_v1 imported")
        print(f"  rows={audit[0]} signal_dates={audit[1]} stocks={audit[2]} formulas={audit[3]}")
        print(f"  signal_date range: {audit[4]} -> {audit[5]}")
        print(f"  avg confidence_score={audit[6]:.2f}, avg historical_win_rate={audit[7]:.4f}")
        print(f"  deduplicated same-day same-stock duplicates: {dedup_dropped}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
