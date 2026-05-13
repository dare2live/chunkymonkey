"""Phase ε D2 — mart_formula_weight_history 每日 build。

读 mart_signal_ic 最近 60 日, softmax + clip + hysteresis → 每公式权重。

用法:
  PYTHONPATH=backend python backend/scripts/build_formula_weights_daily.py [--date 2026-05-12]
"""
from __future__ import annotations

import argparse
import logging
from datetime import date as _date

from services.db import get_conn
from services.paper_engine.ddl import ensure_paper_tables  # 需要 mart_signal_ic
from services.selection.ddl import ensure_selection_tables
from services.selection.feedback import derive_formula_weights


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("build_formula_weights_daily")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None,
                        help="默认 calendar-gated latest_closed_trade_date (Phase ψ.5)")
    parser.add_argument("--ic-window", type=int, default=60)
    args = parser.parse_args()

    if args.date is None:
        from services.utils import latest_closed_or_raise
        args.date = latest_closed_or_raise()
        log.info(f"--date 默认 (calendar-gated): {args.date}")

    conn = get_conn()
    try:
        ensure_paper_tables(conn)
        ensure_selection_tables(conn)
        log.info(f"derive formula weights asof={args.date} ic_window={args.ic_window}d")
        n = derive_formula_weights(conn, args.date, ic_window_days=args.ic_window)
        log.info(f"完成: {n} 公式权重")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
