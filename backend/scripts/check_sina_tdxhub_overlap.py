#!/usr/bin/env python3
"""Check whether legacy Sina K-line codes add coverage beyond TDXHub.

The script connects to smartmoney.duckdb and attaches market.duckdb so the SQL
can be run from the business database context while reading market tables.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import duckdb


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SMARTMONEY_DB = REPO_ROOT / "data" / "smartmoney.duckdb"
DEFAULT_MARKET_DB = REPO_ROOT / "data" / "market.duckdb"

OVERLAP_SQL = """
WITH sina_codes AS (
    SELECT DISTINCT code
    FROM market.price_kline
    WHERE source = ?
      AND freq = 'daily'
      AND adjust = 'qfq'
),
tdx_codes AS (
    SELECT DISTINCT code
    FROM market.price_kline_tdxhub
    WHERE freq = 'daily'
      AND adjust = 'qfq'
)
SELECT
    (SELECT COUNT(*) FROM sina_codes) AS sina_codes,
    (SELECT COUNT(*) FROM tdx_codes) AS tdxhub_codes,
    (SELECT COUNT(*) FROM sina_codes s INNER JOIN tdx_codes t USING (code)) AS overlap_codes,
    (SELECT COUNT(*) FROM sina_codes s LEFT JOIN tdx_codes t USING (code) WHERE t.code IS NULL) AS sina_not_in_tdxhub_codes;
"""

MISSING_SAMPLE_SQL = """
WITH sina_codes AS (
    SELECT DISTINCT code
    FROM market.price_kline
    WHERE source = ?
      AND freq = 'daily'
      AND adjust = 'qfq'
),
tdx_codes AS (
    SELECT DISTINCT code
    FROM market.price_kline_tdxhub
    WHERE freq = 'daily'
      AND adjust = 'qfq'
)
SELECT s.code
FROM sina_codes s
LEFT JOIN tdx_codes t USING (code)
WHERE t.code IS NULL
ORDER BY s.code
LIMIT ?;
"""


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check legacy Sina coverage against TDXHub")
    parser.add_argument("--smartmoney-db", type=Path, default=DEFAULT_SMARTMONEY_DB)
    parser.add_argument("--market-db", type=Path, default=DEFAULT_MARKET_DB)
    parser.add_argument("--source", default="akshare_sina")
    parser.add_argument("--sample-limit", type=int, default=30)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    con = duckdb.connect(str(args.smartmoney_db), read_only=True)
    try:
        con.execute(f"ATTACH '{_sql_path(args.market_db)}' AS market (READ_ONLY)")
        row = con.execute(OVERLAP_SQL, [args.source]).fetchone()
        columns = [desc[0] for desc in con.description]
        result = dict(zip(columns, row))
        sample = [
            item[0]
            for item in con.execute(
                MISSING_SAMPLE_SQL,
                [args.source, max(0, args.sample_limit)],
            ).fetchall()
        ]
    finally:
        con.close()

    print("| metric | value |")
    print("|---|---:|")
    for key in columns:
        print(f"| {key} | {result[key]} |")
    print()
    print("SQL:")
    print(OVERLAP_SQL.strip())
    print()
    if sample:
        print("Sina-only code sample:")
        for code in sample:
            print(code)
    else:
        print("Sina-only code sample: none")
    return 0 if int(result["sina_not_in_tdxhub_codes"]) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
