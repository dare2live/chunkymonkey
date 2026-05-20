#!/usr/bin/env python3
"""Backfill mart_market_perception_daily for Market Perception P1."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.db_connection import DB_PATH  # noqa: E402
from services.duck_adapter import connect  # noqa: E402
from services.market_perception import compute_regime_for_range  # noqa: E402
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
    built_at = datetime.now(timezone.utc).replace(tzinfo=None)

    with connect(str(DB_PATH), timeout=300) as conn:
        ensure_mart_schema(conn)
        df = compute_regime_for_range(conn, args.start, args.end)
        if df.empty:
            logger.warning("no trading-day rows computed for %s -> %s", args.start, args.end)
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
        logger.info(
            "wrote %d rows into mart_market_perception_daily, %s -> %s, regime_score=[%.4f, %.4f]",
            len(rows),
            df["snapshot_date"].min(),
            df["snapshot_date"].max(),
            score_min,
            score_max,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
