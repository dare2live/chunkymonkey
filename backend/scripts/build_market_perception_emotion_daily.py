#!/usr/bin/env python3
"""Backfill mart_market_perception_emotion_daily for Market Perception P2."""

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
from services.market_perception import compute_emotion_for_range  # noqa: E402
from services.schema_marts import ensure_mart_schema  # noqa: E402

logger = logging.getLogger("build_market_perception_emotion_daily")


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
        df = compute_emotion_for_range(conn, args.start, args.end)
        if df.empty:
            logger.warning("no emotion rows computed for %s -> %s", args.start, args.end)
            return 0
        rows = []
        for rec in df.to_dict("records"):
            rows.append(
                (
                    rec["snapshot_date"],
                    rec["emotion_score"],
                    rec["emotion_state"],
                    rec["action_bias"],
                    rec["cycle_phase"],
                    rec["market_breadth"],
                    rec["up_count"],
                    rec["down_count"],
                    rec["limit_up_count"],
                    rec["limit_down_count"],
                    rec["first_board_count"],
                    rec["second_board_count"],
                    rec["third_plus_count"],
                    rec["promotion_rate_1_to_2"],
                    rec["promotion_rate_2_to_3"],
                    rec["open_board_rate"],
                    rec["next_day_premium"],
                    rec["turnover_concentration"],
                    rec["lhb_event_count"],
                    rec["n_stocks"],
                    rec["unknown_metrics"],
                    rec["source_engines"],
                    rec["pit_cutoff_date"],
                    built_at,
                )
            )
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_market_perception_emotion_daily (
                snapshot_date, emotion_score, emotion_state, action_bias, cycle_phase,
                market_breadth, up_count, down_count, limit_up_count, limit_down_count,
                first_board_count, second_board_count, third_plus_count,
                promotion_rate_1_to_2, promotion_rate_2_to_3, open_board_rate,
                next_day_premium, turnover_concentration, lhb_event_count, n_stocks,
                unknown_metrics, source_engines, pit_cutoff_date, built_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        score_min = min(r[1] for r in rows)
        score_max = max(r[1] for r in rows)
        logger.info(
            "wrote %d rows into mart_market_perception_emotion_daily, %s -> %s, emotion_score=[%.4f, %.4f]",
            len(rows),
            df["snapshot_date"].min(),
            df["snapshot_date"].max(),
            score_min,
            score_max,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
