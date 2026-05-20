#!/usr/bin/env python3
"""Backfill mart_market_perception_daily for Market Perception P1."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.db_connection import DB_PATH  # noqa: E402
from services.duck_adapter import connect  # noqa: E402
from services.market_perception import compute_regime_for_range  # noqa: E402
from services.market_perception.regime_engine import get_regime_config  # noqa: E402
from services.schema_marts import ensure_mart_schema  # noqa: E402

logger = logging.getLogger("build_market_perception_daily")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="start trading date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="end trading date, YYYY-MM-DD")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    built_at = datetime.now(timezone.utc).replace(tzinfo=None)
    run_id = f"market_perception_daily_{started_at.strftime('%Y%m%dT%H%M%S')}_{uuid4().hex[:8]}"

    with connect(str(DB_PATH), timeout=300) as conn:
        ensure_mart_schema(conn)
        trading_days_requested = _count_trading_days(conn, args.start, args.end)
        before_rows = _count_rows(conn, "mart_market_perception_daily")
        df = compute_regime_for_range(conn, args.start, args.end)
        if df.empty:
            logger.warning("no trading-day rows computed for %s -> %s", args.start, args.end)
            _write_audit_log(
                conn,
                run_id=run_id,
                started_at=started_at,
                ended_at=datetime.now(timezone.utc).replace(tzinfo=None),
                status="skipped",
                start_date=args.start,
                end_date=args.end,
                trading_days_requested=trading_days_requested,
                rows_written=0,
                missing_days=trading_days_requested,
                score_min=None,
                score_max=None,
                guard_status="no_rows",
                input_row_counts={"mart_market_perception_daily_before": before_rows},
                notes="no trading-day rows computed",
                built_at=built_at,
            )
            conn.commit()
            return 0
        rows = []
        for rec in df.to_dict("records"):
            rows.append(
                (
                    rec["snapshot_date"],
                    rec["regime_score"],
                    rec["breadth_state"],
                    rec["volatility_state"],
                    rec["sentiment_phase"],
                    rec["hs300_ret_60d"],
                    rec["hs300_vol_20d"],
                    rec["breadth_ratio"],
                    rec["breadth_p75_90d"],
                    rec["limit_up_count"],
                    rec["lhb_event_count"],
                    rec["n_obs_days"],
                    rec["source_engines"],
                    rec["pit_cutoff_date"],
                    built_at,
                )
            )
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_market_perception_daily (
                snapshot_date, regime_score, breadth_state, volatility_state,
                sentiment_phase, hs300_ret_60d, hs300_vol_20d, breadth_ratio,
                breadth_p75_90d, limit_up_count, lhb_event_count, n_obs_days,
                source_engines, pit_cutoff_date, built_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        score_min = min(r[1] for r in rows)
        score_max = max(r[1] for r in rows)
        after_rows = _count_rows(conn, "mart_market_perception_daily")
        missing_days = max(trading_days_requested - len(rows), 0)
        guard_abs_max = get_regime_config().regime_score_abs_max
        guard_status = "ok" if abs(score_min) <= guard_abs_max and abs(score_max) <= guard_abs_max else "alert"
        _write_audit_log(
            conn,
            run_id=run_id,
            started_at=started_at,
            ended_at=datetime.now(timezone.utc).replace(tzinfo=None),
            status="success",
            start_date=args.start,
            end_date=args.end,
            trading_days_requested=trading_days_requested,
            rows_written=len(rows),
            missing_days=missing_days,
            score_min=score_min,
            score_max=score_max,
            guard_status=guard_status,
            input_row_counts={
                "mart_market_perception_daily_before": before_rows,
                "mart_market_perception_daily_after": after_rows,
            },
            notes="PIT-strict build_market_perception_daily completed",
            built_at=built_at,
        )
        conn.commit()
        logger.info(
            "wrote %d rows into mart_market_perception_daily, %s -> %s, regime_score=[%.4f, %.4f], guard=%s",
            len(rows),
            df["snapshot_date"].min(),
            df["snapshot_date"].max(),
            score_min,
            score_max,
            guard_status,
        )
    return 0


def _count_trading_days(conn, start: str, end: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
          FROM dim_trading_calendar
         WHERE is_trading = 1
           AND CAST(trade_date AS DATE) BETWEEN ? AND ?
        """,
        [start, end],
    ).fetchone()
    return int(row[0] if row else 0)


def _count_rows(conn, table: str) -> int:
    exists = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        [table],
    ).fetchone()
    if not exists or int(exists[0]) == 0:
        return 0
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row[0] if row else 0)


def _write_audit_log(
    conn,
    *,
    run_id: str,
    started_at: datetime,
    ended_at: datetime,
    status: str,
    start_date: str,
    end_date: str,
    trading_days_requested: int,
    rows_written: int,
    missing_days: int,
    score_min: float | None,
    score_max: float | None,
    guard_status: str,
    input_row_counts: dict[str, int],
    notes: str,
    built_at: datetime,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_market_perception_audit_log (
            run_id, started_at, ended_at, status, start_date, end_date,
            trading_days_requested, rows_written, missing_days, score_min,
            score_max, guard_status, input_row_counts_json, notes, built_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            run_id,
            started_at,
            ended_at,
            status,
            start_date,
            end_date,
            trading_days_requested,
            rows_written,
            missing_days,
            score_min,
            score_max,
            guard_status,
            json.dumps(input_row_counts, ensure_ascii=False, sort_keys=True),
            notes,
            built_at,
        ],
    )


if __name__ == "__main__":
    raise SystemExit(main())
