"""Phase ε D2 — fact_stock_selection_log 一次性回填。

来源:
  - fact_technical_trigger (Phase β, 750K 公式触发) → source='formula'
  - mart_daily_recommendation (Phase α, 100 topk) → source='daily_topk'

用法:
  PYTHONPATH=backend python backend/scripts/build_selection_log_backfill.py
"""
from __future__ import annotations

import argparse
import logging

from services.db import get_conn
from services.selection.ddl import ensure_selection_tables
from services.selection.logger import backfill_from_existing_tables


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("build_selection_log_backfill")


def main():
    parser = argparse.ArgumentParser()
    args = parser.parse_args()

    conn = get_conn()
    try:
        ensure_selection_tables(conn)
        log.info("开始回填...")
        result = backfill_from_existing_tables(conn)
        log.info(f"完成: {result}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
