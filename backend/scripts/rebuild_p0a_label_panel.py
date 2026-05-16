#!/usr/bin/env python3
"""Rebuild mart_p0a_label_panel from clean tdxhub (governance v1 LABEL_VERSION=p0a_v2).

Phase 3 step 1 (post Phase 2 DELETE 4.88M rows):
- 现状 mart_p0a_label_panel = LABEL_VERSION=p0a_v1, 含 corrupt label (704K outlier + 253K NaN)
- 此 script rebuild 走 governance v1:
  - v_price_kline_qfq view (tier-1 tdxhub primary, fallback HS300 only)
  - vwap 公式 amount/(volume*100) (governance v1 lot_size_shares)
  - LABEL_VERSION=p0a_v2_governance_v1

执行:
    PYTHONPATH=backend python backend/scripts/rebuild_p0a_label_panel.py
    PYTHONPATH=backend python backend/scripts/rebuild_p0a_label_panel.py --start-date 2024-01-01 --end-date 2026-05-15

post-fix-audit:
- nightly_data_audit.py fwd_cost_after_outlier 应转 severity=ok
- 旧 p0a_v1 row 不删 (LABEL_VERSION 隔离), 后续 cleanup script 处理
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.labels.build import build_p0a_label_panel, LABEL_VERSION

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("rebuild_p0a_label_panel")

SMART_DB = REPO_ROOT / "data" / "smartmoney.duckdb"
MARKET_DB = REPO_ROOT / "data" / "market.duckdb"


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild mart_p0a_label_panel governance v1")
    parser.add_argument("--start-date", default="2024-01-01")  # rule-compliance: ok evidence=alpha158-panel-实测起点
    parser.add_argument("--end-date", default="2026-05-15")    # rule-compliance: ok evidence=tdxhub-sync-latest-trading-day
    parser.add_argument("--universe-filter", default="60,00,30,68",
                        help="KEEP universe prefix (default: 60/00/30/68 A-share)")
    args = parser.parse_args()

    t0 = time.time()
    log.info(f"=== Rebuild mart_p0a_label_panel (LABEL_VERSION={LABEL_VERSION}) ===")
    log.info(f"  range: {args.start_date} → {args.end_date}")

    # 1. KEEP universe
    sm = duckdb.connect(str(SMART_DB), read_only=True)
    prefixes = tuple(args.universe_filter.split(","))
    placeholders = ",".join("?" for _ in prefixes)
    stocks = [r[0] for r in sm.execute(
        f"SELECT stock_code FROM dim_all_ever_listed "
        f"WHERE is_active=1 AND SUBSTR(stock_code,1,2) IN ({placeholders}) "
        f"ORDER BY stock_code",
        list(prefixes),
    ).fetchall()]
    sm.close()
    log.info(f"  KEEP universe: {len(stocks):,} stocks (prefix {args.universe_filter})")

    # 2. signal_dates from v_price_kline_qfq (tier-1 tdxhub primary)
    mkt = duckdb.connect(str(MARKET_DB), read_only=True)
    dates = [str(r[0]) for r in mkt.execute(
        "SELECT DISTINCT date FROM v_price_kline_qfq "
        "WHERE freq='daily' AND adjust='qfq' AND date >= ? AND date <= ? "
        "ORDER BY date",
        [args.start_date, args.end_date],
    ).fetchall()]
    mkt.close()
    log.info(f"  signal_dates: {len(dates):,} dates ({dates[0] if dates else 'N/A'} → {dates[-1] if dates else 'N/A'})")

    if not stocks or not dates:
        log.error("Empty universe or dates — aborting")
        return 1

    # 3. Build (INSERT OR REPLACE into mart_p0a_label_panel)
    log.info(f"Building (expected ~{len(stocks)*len(dates):,} signal × stock pairs)...")
    t_build = time.time()
    result = build_p0a_label_panel(
        db_path=str(SMART_DB),
        market_db_path=str(MARKET_DB),
        signal_dates=dates,
        stock_codes=stocks,
    )
    build_elapsed = time.time() - t_build
    log.info(f"  rows_built: {result['rows_built']:,} ({build_elapsed:.0f}s)")
    log.info(f"  round_trip_cost_pct: {result['round_trip_cost_pct']:.6f}")
    log.info(f"  label_version: {result['label_version']}")

    # 4. Quick sanity: outlier count
    sm = duckdb.connect(str(SMART_DB), read_only=True)
    sanity = sm.execute(
        "SELECT "
        "  COUNT(*) total, "
        "  SUM(CASE WHEN ABS(fwd_cost_after_20d) > 1.0 THEN 1 ELSE 0 END) outliers_20d, "
        "  MAX(ABS(fwd_cost_after_20d)) max_abs_20d "
        "FROM mart_p0a_label_panel WHERE label_version = ?",
        [result['label_version']]
    ).fetchone()
    sm.close()
    log.info(f"  Sanity ({result['label_version']}): total={sanity[0]:,} | "
             f"outliers_20d={sanity[1]} | max_abs_20d={sanity[2]}")
    if sanity[1] > 0:
        log.warning(f"  WARN: {sanity[1]} outlier rows |fwd|>1.0 (检查 v_price_kline_qfq 数据是否还有混染)")

    total_elapsed = time.time() - t0
    log.info(f"=== Done in {total_elapsed:.0f}s ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
