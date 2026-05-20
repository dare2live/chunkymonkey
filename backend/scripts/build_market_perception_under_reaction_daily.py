#!/usr/bin/env python3
"""Backfill mart_market_perception_under_reaction_daily for Market Perception P4."""

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
from services.market_perception import compute_under_reaction_for_range  # noqa: E402
from services.schema_marts import ensure_mart_schema  # noqa: E402

logger = logging.getLogger("build_market_perception_under_reaction_daily")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="start trading date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="end trading date, YYYY-MM-DD")
    parser.add_argument("--top-n", type=int, default=100, help="rows per trading day to persist")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    built_at = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect(str(DB_PATH), timeout=300) as conn:
        ensure_mart_schema(conn)
        df = compute_under_reaction_for_range(conn, args.start, args.end, top_n=args.top_n)
        if df.empty:
            logger.warning("no under-reaction rows computed for %s -> %s", args.start, args.end)
            return 0
        rows = []
        for rec in df.to_dict("records"):
            rows.append(
                (
                    rec["snapshot_date"], rec["stock_code"], rec["under_reaction_score"],
                    rec["fund_anomaly_score"], rec["price_reaction_score"], rec["capital_flow_score"],
                    rec["amount_expansion_score"], rec["crowding_penalty"], rec["ret_5d"],
                    rec["ret_20d"], rec["amount_ratio_5_20"], rec["lhb_count_30d"],
                    rec["lhb_inst_buy_30d"], rec["lhb_net_buy_pct_30d"], rec["exec_net_signal"],
                    rec["holder_count_change_q_pct"], rec.get("theme_name"), rec.get("theme_score"),
                    rec.get("lifecycle_stage"), rec["pit_cutoff_date"], rec["source_engines"], built_at,
                )
            )
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_market_perception_under_reaction_daily (
                snapshot_date, stock_code, under_reaction_score, fund_anomaly_score,
                price_reaction_score, capital_flow_score, amount_expansion_score,
                crowding_penalty, ret_5d, ret_20d, amount_ratio_5_20, lhb_count_30d,
                lhb_inst_buy_30d, lhb_net_buy_pct_30d, exec_net_signal,
                holder_count_change_q_pct, theme_name, theme_score, lifecycle_stage,
                pit_cutoff_date, source_engines, built_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        logger.info(
            "wrote %d rows into mart_market_perception_under_reaction_daily, %s -> %s, score=[%.4f, %.4f]",
            len(rows),
            df["snapshot_date"].min(),
            df["snapshot_date"].max(),
            float(df["under_reaction_score"].min()),
            float(df["under_reaction_score"].max()),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
