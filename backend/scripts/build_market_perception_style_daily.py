#!/usr/bin/env python3
"""Backfill mart_market_perception_style_daily for Market Perception P6."""

from __future__ import annotations

import argparse
import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.db_connection import DB_PATH  # noqa: E402
from services.duck_adapter import connect  # noqa: E402
from services.market_perception import compute_style_rotation_for_range  # noqa: E402
from services.schema_marts import ensure_mart_schema  # noqa: E402

logger = logging.getLogger("build_market_perception_style_daily")


def _clean(value):
    if value is None:
        return None
    if isinstance(value, float) and (not math.isfinite(value)):
        return None
    return value


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
        df = compute_style_rotation_for_range(conn, args.start, args.end)
        if df.empty:
            logger.warning("no style rows computed for %s -> %s", args.start, args.end)
            return 0
        rows = []
        for rec in df.to_dict("records"):
            rows.append(
                (
                    rec["snapshot_date"], rec["style_rotation_score"], rec["style_bias"],
                    rec["size_preference_score"], rec["trend_preference_score"],
                    rec["crowding_risk_score"], rec["overheat_reversal_risk"],
                    rec["small_ret_1d"], rec["mid_ret_1d"], rec["large_ret_1d"],
                    rec["trend_ret_1d"], rec["reversal_ret_1d"],
                    rec["top_decile_turnover_share"], rec["hot_stock_share"],
                    rec["style_source"], _clean(rec.get("emotion_score")), _clean(rec.get("emotion_state")),
                    rec["pit_cutoff_date"], rec["source_engines"], built_at,
                )
            )
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_market_perception_style_daily (
                snapshot_date, style_rotation_score, style_bias, size_preference_score,
                trend_preference_score, crowding_risk_score, overheat_reversal_risk,
                small_ret_1d, mid_ret_1d, large_ret_1d, trend_ret_1d, reversal_ret_1d,
                top_decile_turnover_share, hot_stock_share, style_source, emotion_score,
                emotion_state, pit_cutoff_date, source_engines, built_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        logger.info(
            "wrote %d rows into mart_market_perception_style_daily, %s -> %s, style=[%.4f, %.4f], crowding=[%.4f, %.4f]",
            len(rows),
            df["snapshot_date"].min(),
            df["snapshot_date"].max(),
            float(df["style_rotation_score"].min()),
            float(df["style_rotation_score"].max()),
            float(df["crowding_risk_score"].min()),
            float(df["crowding_risk_score"].max()),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
