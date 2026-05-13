"""Phase ε D2 — mart_stock_selection_summary 每日 build。

聚合 fact_stock_selection_log + mart_stock_selection_outcome → 每股 1 行。

用法:
  PYTHONPATH=backend python backend/scripts/build_selection_summary_daily.py [--date 2026-05-12]
"""
from __future__ import annotations

import argparse
import logging
from datetime import date as _date

from services.db import get_conn
from services.selection.ddl import ensure_selection_tables
from services.selection.summary import recompute_all_summaries


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("build_selection_summary_daily")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=_date.today().isoformat())
    args = parser.parse_args()

    conn = get_conn()
    try:
        ensure_selection_tables(conn)
        log.info(f"recompute summaries asof={args.date}")
        n = recompute_all_summaries(conn, args.date)
        log.info(f"完成: {n:,} 行")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
