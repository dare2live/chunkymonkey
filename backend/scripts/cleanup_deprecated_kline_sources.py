#!/usr/bin/env python3
"""Phase 2 step 3: 物理 DELETE 退役 stock K-line source rows from price_kline.

按 Codex round 16 deliver (docs/engineering_governance.md Step 3) 实施:
- governance v1 verdict: price_kline 主表 retired except hs300 benchmark allowlist
- forbidden_sources (configs/data_governance.yaml): mootdx / chatgpt_import / eastmoney_direct
                                                    / akshare_* (except csindex_hs300)
- 物理 DELETE 4,879,870 rows (13 source) / 保留 HS300 1,048 + tdxhub_native 5,167,494

执行:
    # dry-run (默认): preview rows + sources, 不删
    PYTHONPATH=backend python backend/scripts/cleanup_deprecated_kline_sources.py
    # execute: 物理删除
    PYTHONPATH=backend python backend/scripts/cleanup_deprecated_kline_sources.py --execute

post-fix-audit (deprecation SOP step 4 rebuild gate):
- pre/post count 对账
- nightly_data_audit.py severity 应转 ok (no anomaly)
- mart_p0a_label_panel + mart_p0b_oos_predictions 标 deprecated (LABEL_VERSION 隔离)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import duckdb

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("cleanup_deprecated_kline_sources")

REPO_ROOT = Path(__file__).resolve().parents[2]
MARKET_DB = REPO_ROOT / "data" / "market.duckdb"

# from yaml: configs/data_governance.yaml deprecation.retired_sources_delete + audit observed sources
# (governance v1) — akshare_sina/eastmoney 等还在 sync 路径, 但 governance v1 verdict: 全 retire
DEPRECATED_SOURCES = (
    "akshare_sina",
    "akshare_eastmoney",
    "akshare_tx",
    "akshare_mootdx",
    "akshare_tdxhub",  # akshare 路 tdxhub backend, 跟 native tdxhub 不同, 也 retire
    "chatgpt_import",
    "mootdx",
    "eastmoney_direct",
    "derived_from_daily",
    "akshare_sina_derived_monthly",
    "akshare_mootdx_derived_monthly",
    "akshare_tx_derived_monthly",
    "akshare_eastmoney_derived_monthly",
)
ALLOWLIST = ("akshare_csindex_hs300",)


def main() -> int:
    parser = argparse.ArgumentParser(description="Cleanup deprecated price_kline sources (governance v1)")
    parser.add_argument("--execute", action="store_true",
                        help="实际删除. 不带此 flag 走 dry-run (preview only)")
    args = parser.parse_args()

    conn = duckdb.connect(str(MARKET_DB), read_only=not args.execute)
    try:
        sources_clause = ",".join(f"'{s}'" for s in DEPRECATED_SOURCES)
        # Pre-DELETE count
        r = conn.execute(
            f"""
            SELECT source, COUNT(*) AS n_rows, COUNT(DISTINCT code) AS n_codes,
                   MIN(date) AS min_d, MAX(date) AS max_d
            FROM price_kline WHERE source IN ({sources_clause})
            GROUP BY source ORDER BY n_rows DESC
            """
        ).fetchall()
        log.info(f"deprecated source breakdown ({len(r)} sources):")
        total = 0
        for row in r:
            log.info(f"  {row[0]:35s} rows={row[1]:>10,} codes={row[2]:>6,} {row[3]}~{row[4]}")
            total += row[1]
        log.info(f"  TOTAL deprecated rows: {total:,}")

        # Allowlist preservation
        allowlist_clause = ",".join(f"'{s}'" for s in ALLOWLIST)
        r2 = conn.execute(
            f"SELECT source, COUNT(*) FROM price_kline WHERE source IN ({allowlist_clause}) GROUP BY source"
        ).fetchall()
        log.info(f"preserved (allowlist) sources: {dict(r2)}")

        if not args.execute:
            log.info("DRY RUN — no rows deleted. Pass --execute to apply.")
            return 0

        # Execute DELETE
        log.info("Executing DELETE...")
        conn.execute(f"DELETE FROM price_kline WHERE source IN ({sources_clause})")
        # Verify post-count
        post_r = conn.execute(
            f"SELECT COUNT(*) FROM price_kline WHERE source IN ({sources_clause})"
        ).fetchone()
        if post_r[0] != 0:
            log.error(f"DELETE incomplete: {post_r[0]} rows remaining (expected 0)")
            return 1
        log.info(f"DELETE PASS: {total:,} rows removed, 0 residue")

        # Final state
        r3 = conn.execute(
            "SELECT source, COUNT(*) FROM price_kline GROUP BY source"
        ).fetchall()
        log.info(f"post-DELETE price_kline sources: {dict(r3)}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
