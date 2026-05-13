"""Phase ε D2 — mart_stock_selection_outcome 每日 build。

对 [start, end] 间所有 selection_log 行计算 5/10/30d forward return + outcome 分类。

用法:
  PYTHONPATH=backend python backend/scripts/build_selection_outcome_daily.py [--from 2024-01-01]
"""
from __future__ import annotations

import argparse
import logging
from datetime import date as _date

from services.db import get_conn
from services.market_db import get_market_conn
from services.selection.ddl import ensure_selection_tables
from services.selection.outcome import compute_outcomes_for_period


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("build_selection_outcome_daily")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="from_date", default="2024-01-01")
    parser.add_argument("--to", dest="to_date", default=_date.today().isoformat())
    args = parser.parse_args()

    conn = get_conn()
    mkt = get_market_conn()
    try:
        ensure_selection_tables(conn)
        log.info(f"build outcomes {args.from_date} ~ {args.to_date}")
        n = compute_outcomes_for_period(conn, mkt, args.from_date, args.to_date)
        log.info(f"完成: {n:,} 行")
    finally:
        conn.close()
        mkt.close()


if __name__ == "__main__":
    main()
