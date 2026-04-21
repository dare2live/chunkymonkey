"""SEF Phase I 一键执行：migrate + chain 回填 + TB + survivorship + Alpha158.

用法:
    cd backend && python3 -m scripts.sef.phase1_run --step all
    cd backend && python3 -m scripts.sef.phase1_run --step schema
    cd backend && python3 -m scripts.sef.phase1_run --step chain
    cd backend && python3 -m scripts.sef.phase1_run --step triple_barrier
    cd backend && python3 -m scripts.sef.phase1_run --step survivorship
    cd backend && python3 -m scripts.sef.phase1_run --step alpha158

Alpha158 生成耗时最长（~10-30 min），默认不在 all 中自动跑，需要 --with-alpha158 。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# Allow "python3 scripts/sef/phase1_run.py" from project root
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("cm-api.sef.phase1")


def run_schema(conn):
    from services.sef.schema import migrate_phase1

    return migrate_phase1(conn)


def run_chain(conn, mkt_conn):
    from services.sef.chain_alpha import (
        backfill_chain_alpha,
        link_events_to_chains,
        refresh_event_pnl_snapshot,
    )

    stats = backfill_chain_alpha(conn, mkt_conn)
    stats["events_linked"] = link_events_to_chains(conn)
    stats["events_snapshot_refreshed"] = refresh_event_pnl_snapshot(conn)
    return stats


def run_triple_barrier(conn, mkt_conn):
    from services.sef.triple_barrier import apply_triple_barrier

    return apply_triple_barrier(conn, mkt_conn)


def run_survivorship(conn, mkt_conn):
    from services.sef.survivorship import build_dim_all_ever_listed

    return build_dim_all_ever_listed(conn, mkt_conn)


def run_alpha158(conn, start_date: str = "2023-01-01"):
    from services.sef.qlib_alpha158 import generate_alpha158

    return generate_alpha158(conn, start_date=start_date)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--step",
        default="all",
        choices=[
            "all",
            "schema",
            "chain",
            "triple_barrier",
            "survivorship",
            "alpha158",
        ],
    )
    parser.add_argument("--with-alpha158", action="store_true", help="在 all 里包含 Alpha158")
    parser.add_argument("--alpha158-start", default="2023-01-01")
    args = parser.parse_args()

    from services.db import get_conn
    from services.market_db import get_market_conn

    conn = get_conn()
    mkt_conn = get_market_conn()

    report = {"started_at": datetime.utcnow().isoformat(timespec="seconds"), "steps": {}}

    try:
        if args.step in ("all", "schema"):
            report["steps"]["schema"] = run_schema(conn)
        if args.step in ("all", "chain"):
            report["steps"]["chain"] = run_chain(conn, mkt_conn)
        if args.step in ("all", "triple_barrier"):
            report["steps"]["triple_barrier"] = run_triple_barrier(conn, mkt_conn)
        if args.step in ("all", "survivorship"):
            report["steps"]["survivorship"] = run_survivorship(conn, mkt_conn)
        if args.step == "alpha158" or (args.step == "all" and args.with_alpha158):
            report["steps"]["alpha158"] = run_alpha158(conn, start_date=args.alpha158_start)
    finally:
        conn.close()
        mkt_conn.close()

    report["finished_at"] = datetime.utcnow().isoformat(timespec="seconds")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
