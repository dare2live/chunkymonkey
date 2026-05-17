#!/usr/bin/env python3
"""P0a feature_label_panel v4 build CLI — Phase 4 features wired into canonical panel.

Codex round 20 verdict: Phase 4 7 modules (50 features) 都没 wire 到生产 panel.
v4 = v3_ext + 22 cols (mcap_decile, beta_60d/zscore, sector_momentum 9 raw, survey 4 raw, tom 7 inline).

prerequisite:
1. Optuna PID 25088 已结束 (DB single-writer lock)
2. mart_p0a_feature_label_panel_v3_ext 已 build (capital_flow wired)
3. fact_market_cap_decile_daily / fact_industry_beta_daily / fact_sector_momentum_daily 都 fresh
4. mart_stock_industry_pit / mart_stock_survey_features 都 fresh

usage:
    PYTHONPATH=backend python backend/scripts/build_p0a_feature_panel_v4.py \\
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

from services.labels.feature_join_v4 import (
    FEATURE_PANEL_VERSION_V4,
    build_p0a_feature_label_panel_v4,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("build_p0a_v4")


REPO = Path(__file__).resolve().parents[2]
SMART_DB = REPO / "data" / "smartmoney.duckdb"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build mart_p0a_feature_label_panel_v4")
    parser.add_argument("--start-date", default="2024-01-01")  # rule-compliance: ok evidence=alpha158-panel-实测范围
    parser.add_argument("--end-date", default="2026-04-30")    # rule-compliance: ok evidence=alpha158-panel-实测范围
    args = parser.parse_args()

    t0 = time.time()
    log.info(f"=== Build feature_label_panel_v4 (range {args.start_date} → {args.end_date}) ===")

    # 1. Universe + dates from v3 panel (v3_ext skipped, capital_flow inlined)
    sm = duckdb.connect(str(SMART_DB), read_only=True)
    try:
        n_v3 = sm.execute("SELECT COUNT(*) FROM mart_p0a_feature_label_panel_v3").fetchone()[0]
        log.info(f"  v3 panel: {n_v3:,} rows")
    except Exception as e:
        log.error(f"v3 panel missing: {e}")
        return 1

    dates = [str(r[0]) for r in sm.execute(
        "SELECT DISTINCT signal_date FROM mart_p0a_feature_label_panel_v3 "
        "WHERE signal_date >= ? AND signal_date <= ? ORDER BY signal_date",
        [args.start_date, args.end_date],
    ).fetchall()]
    stocks = [r[0] for r in sm.execute(
        "SELECT DISTINCT stock_code FROM mart_p0a_feature_label_panel_v3 "
        "ORDER BY stock_code"
    ).fetchall()]
    sm.close()
    log.info(f"  dates: {len(dates):,}, stocks: {len(stocks):,}")

    if not stocks or not dates:
        log.error("Empty universe or dates — aborting")
        return 1

    # 2. Build v4
    log.info("Building v4 panel (SQL JOIN: mcd + ib + sm + survey + tom inline) ...")
    t_build = time.time()
    result = build_p0a_feature_label_panel_v4(
        db_path=str(SMART_DB),
        signal_dates=dates,
        stock_codes=stocks,
    )
    build_elapsed = time.time() - t_build
    log.info(f"  rows_built: {result['rows_built']:,} ({build_elapsed:.0f}s)")
    log.info(f"  feature_version: {result['feature_version']}")
    log.info(f"  new_cols_count: {result['new_cols_count']}")
    log.info(f"  built_at: {result['built_at']}")

    # 3. Audit non-NULL coverage by feature group
    log.info("Audit coverage by feature group:")
    audit = duckdb.connect(str(SMART_DB), read_only=True)
    audit_cols = [
        ("market_cap_decile", "mcap_decile"),
        ("industry_beta", "beta_60d"),
        ("sector_momentum (PIT industry)", "sm_ret_60d"),
        ("institution_survey (coverage 2025-04+)", "survey_count_30d"),
        ("time_of_month (inline)", "tom_day_of_month"),
    ]
    for label, col in audit_cols:
        try:
            r = audit.execute(
                f"SELECT COUNT(*), COUNT({col}) FROM mart_p0a_feature_label_panel_v4"
            ).fetchone()
            pct = r[1] / r[0] * 100 if r[0] else 0
            log.info(f"  {label} ({col}): {r[1]:,}/{r[0]:,} ({pct:.1f}%)")
        except Exception as e:
            log.warning(f"  {label}: audit failed — {e}")
    audit.close()

    total_elapsed = time.time() - t0
    log.info(f"=== Done in {total_elapsed:.0f}s ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
