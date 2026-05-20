#!/usr/bin/env python3
"""Backfill mart_market_perception_theme_daily for Market Perception P3."""

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
from services.market_perception import compute_theme_lifecycle_for_range  # noqa: E402
from services.schema_marts import ensure_mart_schema  # noqa: E402

logger = logging.getLogger("build_market_perception_theme_daily")


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
        df = compute_theme_lifecycle_for_range(conn, args.start, args.end)
        if df.empty:
            logger.warning("no theme rows computed for %s -> %s", args.start, args.end)
            return 0
        rows = []
        for rec in df.to_dict("records"):
            rows.append(
                (
                    rec["snapshot_date"],
                    rec["theme_name"],
                    rec["theme_score"],
                    rec["lifecycle_stage"],
                    rec["mainline_rank"],
                    rec["is_mainline"],
                    rec["diffusion_state"],
                    rec["sector_breadth"],
                    rec["sector_ret_20d"],
                    rec["sector_ret_60d"],
                    rec["sector_excess_20d"],
                    rec["sector_excess_60d"],
                    rec["price_vs_ma20"],
                    rec["price_vs_ma60"],
                    rec["limit_up_count"],
                    rec["n_stocks"],
                    rec["top3_turnover_share"],
                    rec["pit_member_confidence"],
                    rec["source_engines"],
                    rec["pit_cutoff_date"],
                    built_at,
                )
            )
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_market_perception_theme_daily (
                snapshot_date, theme_name, theme_score, lifecycle_stage, mainline_rank,
                is_mainline, diffusion_state, sector_breadth, sector_ret_20d,
                sector_ret_60d, sector_excess_20d, sector_excess_60d, price_vs_ma20,
                price_vs_ma60, limit_up_count, n_stocks, top3_turnover_share,
                pit_member_confidence, source_engines, pit_cutoff_date, built_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        logger.info(
            "wrote %d rows into mart_market_perception_theme_daily, %s -> %s, theme_score=[%.4f, %.4f]",
            len(rows),
            df["snapshot_date"].min(),
            df["snapshot_date"].max(),
            float(df["theme_score"].min()),
            float(df["theme_score"].max()),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
