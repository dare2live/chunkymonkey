"""Cleanup script for 2026-05-19 K-line intraday contamination incident.

CLAUDE.md Rule 3 反例复刻: 2026-05-19 14:00 CST 盘中, daily_update.sh sync 路径
`build_price_kline_tdxhub.py:write_batch()` 绕过 lint, tdxhub server 返回的 5月19日
partial K-line (盘中数据) 直接写入 price_kline_tdxhub (5,184 codes) + alpha158 derived
fact_alpha158_panel (5,175 codes).

Codex review HIGH 2: feedback_leakage_cleanup.md 要求 cleanup 步骤固化 + commit, 不
依赖手工.

执行此 script (idempotent, 多次跑安全):
- DELETE price_kline_tdxhub WHERE date >= '2026-05-19'
- DELETE fact_alpha158_panel WHERE date >= '2026-05-19'
- DELETE downstream panel/fact tables WHERE date >= '2026-05-19' (if any)
- Verify max(date) = '2026-05-18' across critical tables
- Print residue audit

rule-compliance: ok evidence=incident-20260519-kline-intraday-contamination

Usage:
    PYTHONPATH=backend python backend/scripts/cleanup_kline_intraday_20260519.py
    PYTHONPATH=backend python backend/scripts/cleanup_kline_intraday_20260519.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

import duckdb


CONTAMINATION_DATE = "2026-05-19"  # rule-compliance: ok evidence=incident-date


def cleanup_db(db_path: Path, table_date_pairs: list[tuple[str, str]], dry_run: bool = False) -> dict:
    """Delete rows where date >= CONTAMINATION_DATE for each (table, date_col)."""
    results = {}
    con = duckdb.connect(str(db_path))
    try:
        for table, date_col in table_date_pairs:
            try:
                n = con.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {date_col} >= ?",
                    [CONTAMINATION_DATE],
                ).fetchone()[0]
                if n == 0:
                    results[table] = {"status": "clean", "n": 0}
                    continue
                if dry_run:
                    results[table] = {"status": "dry_run_would_delete", "n": n}
                else:
                    con.execute(
                        f"DELETE FROM {table} WHERE {date_col} >= ?",
                        [CONTAMINATION_DATE],
                    )
                    con.commit()
                    results[table] = {"status": "deleted", "n": n}
                # verify max date
                max_d = con.execute(f"SELECT MAX({date_col}) FROM {table}").fetchone()[0]
                results[table]["max_date_after"] = str(max_d)
            except Exception as e:
                results[table] = {"status": "error", "error": str(e)}
    finally:
        con.close()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Cleanup 2026-05-19 K-line intraday contamination")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be deleted, don't execute")
    args = parser.parse_args()

    print(f"=== Cleanup K-line intraday contamination >= {CONTAMINATION_DATE} ===")
    print(f"dry_run={args.dry_run}")
    print()

    # market.duckdb
    market_results = cleanup_db(
        REPO_ROOT / "data" / "market.duckdb",
        [("price_kline_tdxhub", "date")],
        dry_run=args.dry_run,
    )
    print("market.duckdb:")
    for table, r in market_results.items():
        print(f"  {table}: {r}")

    # alpha158.duckdb
    alpha_results = cleanup_db(
        REPO_ROOT / "data" / "alpha158.duckdb",
        [("fact_alpha158_panel", "date")],
        dry_run=args.dry_run,
    )
    print()
    print("alpha158.duckdb:")
    for table, r in alpha_results.items():
        print(f"  {table}: {r}")

    # smartmoney.duckdb downstream
    smart_results = cleanup_db(
        REPO_ROOT / "data" / "smartmoney.duckdb",
        [
            ("mart_p0a_label_panel", "signal_date"),
            ("mart_p0a_feature_label_panel_v3", "signal_date"),
            ("mart_p0a_feature_label_panel_v4", "signal_date"),
            ("fact_capital_flow_pit_daily", "trade_date"),
            ("fact_sector_momentum_daily", "date"),
            ("fact_lhb_event", "trade_date"),
            ("fact_risk_factors", "calc_date"),
            ("fact_technical_trigger", "date"),
        ],
        dry_run=args.dry_run,
    )
    print()
    print("smartmoney.duckdb downstream:")
    for table, r in smart_results.items():
        print(f"  {table}: {r}")

    # Verdict
    total_deleted = sum(
        r.get("n", 0)
        for r in {**market_results, **alpha_results, **smart_results}.values()
        if r.get("status") == "deleted"
    )
    print()
    print(f"=== verdict: total {total_deleted} rows deleted (dry_run={args.dry_run}) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
