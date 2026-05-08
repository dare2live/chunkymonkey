#!/usr/bin/env python3
"""Build PIT industry membership tables and readiness quality evidence."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.industry_pit import (  # noqa: E402
    DEFAULT_FALLBACK_START,
    build_industry_pit,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id")
    parser.add_argument("--signal-table", default="mart_shareholder_plan_initial_feature_panel")
    parser.add_argument("--signal-stock-column", default="stock_code")
    parser.add_argument("--signal-date-column", default="date")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--fallback-start", default=DEFAULT_FALLBACK_START)
    args = parser.parse_args()

    conn = get_conn()
    try:
        result = build_industry_pit(
            conn,
            run_id=args.run_id,
            signal_table=args.signal_table,
            signal_stock_column=args.signal_stock_column,
            signal_date_column=args.signal_date_column,
            start_date=args.start_date,
            end_date=args.end_date,
            fallback_start=args.fallback_start,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
