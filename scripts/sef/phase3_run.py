"""SEF Phase III 一键运行: Bayesian + Meta-Labeling + Black-Litterman."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "backend"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--step", default="all",
        choices=["all", "bayes", "meta", "bl"],
    )
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--lookback-days", type=int, default=180)
    args = parser.parse_args()

    from services.db import get_conn
    from services.market_db import get_market_conn

    conn = get_conn(timeout=120)
    mkt_conn = get_market_conn(timeout=120)
    report = {"started_at": datetime.utcnow().isoformat(timespec="seconds"), "steps": {}}

    try:
        if args.step in ("all", "bayes"):
            from services.sef.bayesian_updater import build_bayesian_posterior

            report["steps"]["bayes"] = build_bayesian_posterior(
                conn, as_of_date=args.as_of_date, lookback_days=args.lookback_days
            )
        if args.step in ("all", "meta"):
            from services.sef.meta_labeling import train_meta_model

            report["steps"]["meta"] = train_meta_model(conn)
        if args.step in ("all", "bl"):
            from services.sef.black_litterman import build_daily_portfolio

            report["steps"]["bl"] = build_daily_portfolio(
                conn, mkt_conn, as_of_date=args.as_of_date
            )
    finally:
        conn.close()
        mkt_conn.close()

    report["finished_at"] = datetime.utcnow().isoformat(timespec="seconds")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
