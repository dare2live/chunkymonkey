#!/usr/bin/env python3
"""P0a feature_label_panel v3 build — Codex 7-day plan Day 2 + Day 3.

读 dim_all_ever_listed + alpha158.fact_alpha158_panel 决定 universe + dates,
调 services.labels.feature_join_v3.build_p0a_feature_label_panel_v3.

入库 mart_p0a_feature_label_panel_v3 (v2 保留兼容).

用法:
    PYTHONPATH=backend python backend/scripts/build_p0a_feature_panel_v3.py \
        [--start-date 2024-01-01] [--end-date 2026-04-30]
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb

from services.labels.feature_join_v3 import (
    FEATURE_PANEL_VERSION_V3,
    build_p0a_feature_label_panel_v3,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("build_p0a_v3")


REPO = Path(__file__).resolve().parents[2]
SMART_DB = REPO / "data" / "smartmoney.duckdb"
ALPHA_DB = REPO / "data" / "alpha158.duckdb"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build mart_p0a_feature_label_panel_v3")
    parser.add_argument("--start-date", default="2024-01-01")  # rule-compliance: ok evidence=alpha158-panel-实测范围
    parser.add_argument("--end-date", default="2026-04-30")    # rule-compliance: ok evidence=alpha158-panel-实测范围
    args = parser.parse_args()

    t0 = time.time()
    log.info(f"=== Build feature_label_panel_v3 (range {args.start_date} → {args.end_date}) ===")

    # 1. KEEP universe (60/00/30/68 active A-share)
    sm = duckdb.connect(str(SMART_DB), read_only=True)
    stocks = [r[0] for r in sm.execute(
        "SELECT stock_code FROM dim_all_ever_listed "
        "WHERE is_active=1 AND SUBSTR(stock_code,1,2) IN ('60','00','30','68') "
        "ORDER BY stock_code"
    ).fetchall()]
    sm.close()
    log.info(f"  KEEP universe: {len(stocks):,} stocks")

    # 2. signal_dates from alpha158 panel (intersection range)
    a158 = duckdb.connect(str(ALPHA_DB), read_only=True)
    dates = [str(r[0]) for r in a158.execute(
        "SELECT DISTINCT date FROM fact_alpha158_panel "
        "WHERE date >= ? AND date <= ? ORDER BY date",
        [args.start_date, args.end_date],
    ).fetchall()]
    a158.close()
    log.info(f"  signal_dates: {len(dates):,} dates ({dates[0] if dates else 'N/A'} → {dates[-1] if dates else 'N/A'})")

    if not stocks or not dates:
        log.error("Empty universe or dates — aborting")
        return 1

    # 3. Build (this writes mart_p0a_feature_label_panel_v3)
    log.info("Building v3 panel (SQL CTE + INSERT) ...")
    t_build = time.time()
    result = build_p0a_feature_label_panel_v3(
        db_path=str(SMART_DB),
        alpha158_db_path=str(ALPHA_DB),
        signal_dates=dates,
        stock_codes=stocks,
    )
    build_elapsed = time.time() - t_build
    log.info(f"  rows_built: {result['rows_built']:,} ({build_elapsed:.0f}s)")
    log.info(f"  feature_version: {result['feature_version']}")
    log.info(f"  built_at: {result['built_at']}")

    total_elapsed = time.time() - t0
    log.info(f"=== Done in {total_elapsed:.0f}s ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
