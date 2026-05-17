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
    # Codex round 17 Q3 REDLINE: tdxhub sync 滞后 2026-05-07+, 最后 full coverage 日 2026-05-06
    parser.add_argument("--end-date", default="2026-05-06")    # rule-compliance: ok evidence=tdxhub-last-full-coverage-day
    parser.add_argument("--min-coverage-pct", type=float, default=0.95,
                        help="每个 signal_date stock 覆盖率 (Codex Q8.2 coverage gate)")
    parser.add_argument("--run-audit-gate", action="store_true",
                        help="Codex Q2c: 跑 audit_p0a_panel.py post-build gate (governance v1 default ON)")
    parser.add_argument("--universe-filter", default="60,00,30,68",
                        help="KEEP universe prefix (default: 60/00/30/68 A-share)")
    args = parser.parse_args()

    t0 = time.time()
    log.info(f"=== Rebuild mart_p0a_label_panel (LABEL_VERSION={LABEL_VERSION}) ===")
    log.info(f"  range: {args.start_date} → {args.end_date}")

    # 1. KEEP universe — PIT/ever-listed (Codex round 17 Q2a REDLINE: 不用 is_active=1 防 survivorship bias)
    # 用 dim_all_ever_listed 全部 (含退市股), build_p0a_label_panel SQL LEFT JOIN v_price_kline_qfq
    # 退市股 / 未上市 stock 在 signal_date 时无 K 线 → entry_vwap=NULL → label=NULL (自动 PIT)
    sm = duckdb.connect(str(SMART_DB), read_only=True)
    prefixes = tuple(args.universe_filter.split(","))
    placeholders = ",".join("?" for _ in prefixes)
    stocks = [r[0] for r in sm.execute(
        f"SELECT stock_code FROM dim_all_ever_listed "
        f"WHERE SUBSTR(stock_code,1,2) IN ({placeholders}) "
        f"ORDER BY stock_code",
        list(prefixes),
    ).fetchall()]
    sm.close()
    log.info(f"  ever-listed universe: {len(stocks):,} stocks (prefix {args.universe_filter}) — PIT via LEFT JOIN NULL")

    # 2. signal_dates from v_price_kline_qfq (tier-1 tdxhub primary)
    # Codex Q2b FIX: 跟 alpha158 dates intersection (防 label/feature date 不一致)
    # 加 coverage gate (Codex Q8.2): 每个 signal_date 必须覆盖 >= min_coverage_pct * universe
    mkt = duckdb.connect(str(MARKET_DB), read_only=True)
    date_coverage = mkt.execute(
        "SELECT date, COUNT(DISTINCT code) AS n_codes FROM v_price_kline_qfq "
        "WHERE freq='daily' AND adjust='qfq' AND date >= ? AND date <= ? "
        "GROUP BY date ORDER BY date",
        [args.start_date, args.end_date],
    ).fetchall()
    mkt.close()
    if not date_coverage:
        log.error("v_price_kline_qfq empty in date range — aborting")
        return 1

    # Q2b: intersect with alpha158 dates (feature panel source)
    ALPHA_DB = REPO_ROOT / "data" / "alpha158.duckdb"
    a158 = duckdb.connect(str(ALPHA_DB), read_only=True)
    a158_dates_set = {str(r[0]) for r in a158.execute(
        "SELECT DISTINCT date FROM fact_alpha158_panel WHERE date >= ? AND date <= ?",
        [args.start_date, args.end_date],
    ).fetchall()}
    a158.close()
    log.info(f"  alpha158 dates: {len(a158_dates_set):,}")
    market_only_count = sum(1 for d, _ in date_coverage if str(d) not in a158_dates_set)
    if market_only_count > 0:
        log.warning(f"  market-only dates: {market_only_count} (will be dropped to keep label/feature in sync)")
    date_coverage = [(d, n) for d, n in date_coverage if str(d) in a158_dates_set]

    min_codes = int(len(stocks) * args.min_coverage_pct * 0.5)  # 历史可能少一些股, 用 universe 50% 做下限
    valid_dates = [d for d, n in date_coverage if n >= min_codes]
    partial_dates = [(d, n) for d, n in date_coverage if n < min_codes]
    if partial_dates:
        log.warning(f"  Coverage gate: {len(partial_dates)} dates dropped (codes < {min_codes}):")
        for d, n in partial_dates[:5]: log.warning(f"    {d} | {n} codes")
        if len(partial_dates) > 5:
            log.warning(f"    ... and {len(partial_dates)-5} more")
    dates = valid_dates
    log.info(f"  signal_dates: {len(dates):,} dates ({dates[0] if dates else 'N/A'} → {dates[-1] if dates else 'N/A'})"
             f" — {len(partial_dates)} partial-coverage dates excluded")

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

    # Codex Q2c FIX: invoke audit_p0a_panel.py 作为 post-build hard gate (governance v1)
    if args.run_audit_gate:
        import subprocess
        log.info("  Codex Q2c: invoking audit_p0a_panel.py gate ...")
        rc = subprocess.run(
            ["python", str(REPO_ROOT / "backend" / "scripts" / "audit_p0a_panel.py")],
            cwd=str(REPO_ROOT),
            env={**__import__('os').environ, 'PYTHONPATH': 'backend'},
        ).returncode
        if rc != 0:
            log.error(f"  audit_p0a_panel.py FAIL (exit {rc}) — governance v1 gate not passed")
            return rc

    total_elapsed = time.time() - t0
    log.info(f"=== Done in {total_elapsed:.0f}s ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
