#!/usr/bin/env python3
"""Backfill mart_market_perception_leader_follower_daily for Market Perception P5."""

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
from services.market_perception import compute_leader_follower_for_range  # noqa: E402
from services.schema_marts import ensure_mart_schema  # noqa: E402

logger = logging.getLogger("build_market_perception_leader_follower_daily")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="start trading date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="end trading date, YYYY-MM-DD")
    parser.add_argument("--top-n", type=int, default=5, help="followers per theme per trading day")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    built_at = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect(str(DB_PATH), timeout=300) as conn:
        ensure_mart_schema(conn)
        df = compute_leader_follower_for_range(conn, args.start, args.end, top_n=args.top_n)
        if df.empty:
            logger.warning("no leader/follower rows computed for %s -> %s", args.start, args.end)
            return 0
        rows = []
        for rec in df.to_dict("records"):
            rows.append(
                (
                    rec["snapshot_date"], rec["theme_name"], rec["leader_stock_code"], rec["follower_stock_code"],
                    rec["relation_type"], rec["lag_days"], rec["leader_strength_score"], rec["follower_lag_score"],
                    rec["diffusion_score"], rec["leader_ret_5d"], rec["leader_ret_20d"], rec["follower_ret_1d"],
                    rec["follower_ret_3d"], rec["follower_ret_5d"], rec["follower_ret_20d"],
                    rec["follower_amount_ratio_5_20"], rec.get("theme_score"), rec.get("lifecycle_stage"),
                    rec["pit_member_confidence"], rec["pit_cutoff_date"], rec["source_engines"], built_at,
                )
            )
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_market_perception_leader_follower_daily (
                snapshot_date, theme_name, leader_stock_code, follower_stock_code,
                relation_type, lag_days, leader_strength_score, follower_lag_score,
                diffusion_score, leader_ret_5d, leader_ret_20d, follower_ret_1d,
                follower_ret_3d, follower_ret_5d, follower_ret_20d,
                follower_amount_ratio_5_20, theme_score, lifecycle_stage,
                pit_member_confidence, pit_cutoff_date, source_engines, built_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        logger.info(
            "wrote %d rows into mart_market_perception_leader_follower_daily, %s -> %s, diffusion_score=[%.4f, %.4f]",
            len(rows),
            df["snapshot_date"].min(),
            df["snapshot_date"].max(),
            float(df["diffusion_score"].min()),
            float(df["diffusion_score"].max()),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
