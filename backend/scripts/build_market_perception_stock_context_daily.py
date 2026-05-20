#!/usr/bin/env python3
"""Backfill mart_market_perception_stock_context_daily for Market Perception P7."""

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
from services.market_perception import compute_stock_context_for_range  # noqa: E402
from services.schema_marts import ensure_mart_schema  # noqa: E402

logger = logging.getLogger("build_market_perception_stock_context_daily")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="start trading date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="end trading date, YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=100, help="top under-reaction seed rows per trading day")
    return parser.parse_args()


def _clean(value):
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    built_at = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect(str(DB_PATH), timeout=300) as conn:
        ensure_mart_schema(conn)
        df = compute_stock_context_for_range(conn, args.start, args.end, limit=args.limit)
        if df.empty:
            logger.warning("no stock context rows computed for %s -> %s", args.start, args.end)
            return 0
        rows = []
        for rec in df.to_dict("records"):
            rows.append(
                (
                    rec["snapshot_date"], rec["stock_code"], rec["context_score"], rec["context_state"],
                    _clean(rec.get("market_regime_score")), _clean(rec.get("emotion_score")), _clean(rec.get("emotion_state")),
                    rec.get("theme_name"), _clean(rec.get("theme_score")), rec.get("lifecycle_stage"),
                    _clean(rec.get("under_reaction_score")), _clean(rec.get("fund_anomaly_score")),
                    _clean(rec.get("leader_follow_score")), rec.get("leader_stock_code"),
                    _clean(rec.get("chain_diffusion_score")), _clean(rec.get("style_rotation_score")),
                    rec.get("style_bias"), _clean(rec.get("crowding_risk_score")),
                    _clean(rec.get("overheat_reversal_risk")), rec["data_completeness_score"],
                    rec["missing_context_fields"], rec["pit_cutoff_date"], rec["source_engines"], built_at,
                )
            )
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_market_perception_stock_context_daily (
                snapshot_date, stock_code, context_score, context_state,
                market_regime_score, emotion_score, emotion_state, theme_name,
                theme_score, lifecycle_stage, under_reaction_score, fund_anomaly_score,
                leader_follow_score, leader_stock_code, chain_diffusion_score,
                style_rotation_score, style_bias, crowding_risk_score,
                overheat_reversal_risk, data_completeness_score,
                missing_context_fields, pit_cutoff_date, source_engines, built_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        logger.info(
            "wrote %d rows into mart_market_perception_stock_context_daily, %s -> %s, context=[%.4f, %.4f], completeness=[%.4f, %.4f]",
            len(rows),
            df["snapshot_date"].min(),
            df["snapshot_date"].max(),
            float(df["context_score"].min()),
            float(df["context_score"].max()),
            float(df["data_completeness_score"].min()),
            float(df["data_completeness_score"].max()),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
