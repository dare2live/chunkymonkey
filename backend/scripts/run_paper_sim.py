"""Phase δ D2 — Paper engine CLI 入口。

用法:
  PYTHONPATH=backend python backend/scripts/run_paper_sim.py [--date 2026-05-12]
  PYTHONPATH=backend python backend/scripts/run_paper_sim.py --replay --from 2026-01-01 --to 2026-05-12

单日: 用最新 daily-topk + trade_plan 跑一日, 写 4 表。
replay: 回放历史交易日, 重建 NAV 序列 (Phase δ D3)。
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from services.db import get_conn
from services.market_db import get_market_conn
from services.paper_engine.ddl import ensure_paper_tables
from services.paper_engine.driver import run_paper_day


log = logging.getLogger("run_paper_sim")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


def _trading_days(conn, start: str, end: str) -> list[str]:
    """读 dim_trading_calendar 当起止区间的交易日。"""
    rows = conn.execute(
        """
        SELECT trade_date FROM dim_trading_calendar
         WHERE trade_date >= ? AND trade_date <= ? AND is_trading = 1
         ORDER BY trade_date
        """,
        [start, end],
    ).fetchall()
    return [r[0] for r in rows]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="单日跑")
    parser.add_argument("--replay", action="store_true", help="回放历史")
    parser.add_argument("--from", dest="from_date", default=None)
    parser.add_argument("--to", dest="to_date", default=None)
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0)
    parser.add_argument("--max-positions", type=int, default=20)
    parser.add_argument("--model-id", default="paper_v1")
    args = parser.parse_args()

    conn = get_conn()
    mkt_conn = get_market_conn()
    try:
        ensure_paper_tables(conn)

        if args.replay:
            if not args.from_date or not args.to_date:
                log.error("replay 模式需 --from 和 --to")
                return
            days = _trading_days(conn, args.from_date, args.to_date)
            log.info(f"replay: {len(days)} 个交易日 {args.from_date} ~ {args.to_date}")
            prev = None
            for i, d in enumerate(days):
                try:
                    run_paper_day(
                        conn=conn, mkt_conn=mkt_conn,
                        snapshot_date=d, prev_date=prev,
                        initial_capital=args.initial_capital,
                        max_positions=args.max_positions,
                        model_id=args.model_id,
                    )
                    prev = d
                except Exception as e:
                    log.warning(f"  day {d} failed: {e}")
                if (i + 1) % 10 == 0:
                    log.info(f"  replay {i+1}/{len(days)} ({d})")
        else:
            if args.date:
                d = args.date
            else:
                from services.utils import latest_closed_or_raise
                d = latest_closed_or_raise()  # Phase ψ.5: calendar-gated
            # 找前一交易日
            prev_row = conn.execute(
                "SELECT trade_date FROM dim_trading_calendar WHERE trade_date < ? AND is_trading=1 ORDER BY trade_date DESC LIMIT 1",
                [d],
            ).fetchone()
            prev = prev_row[0] if prev_row else None
            run_paper_day(
                conn=conn, mkt_conn=mkt_conn,
                snapshot_date=d, prev_date=prev,
                initial_capital=args.initial_capital,
                max_positions=args.max_positions,
                model_id=args.model_id,
            )
    finally:
        conn.close()
        mkt_conn.close()


if __name__ == "__main__":
    main()
