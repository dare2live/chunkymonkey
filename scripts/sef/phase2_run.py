"""SEF Phase II 一键运行: Cox + stock_character + Sharpe Style + HMM.

用法:
    python3 scripts/sef/phase2_run.py --step all
    python3 scripts/sef/phase2_run.py --step cox
    python3 scripts/sef/phase2_run.py --step stock_char
    python3 scripts/sef/phase2_run.py --step inst_style
    python3 scripts/sef/phase2_run.py --step hmm
"""

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
        "--step",
        default="all",
        choices=["all", "cox", "stock_char", "inst_style", "hmm"],
    )
    parser.add_argument("--limit-stocks", type=int, default=None)
    parser.add_argument("--limit-inst", type=int, default=None)
    args = parser.parse_args()

    from services.db import get_conn
    from services.market_db import get_market_conn

    conn = get_conn(timeout=120)
    mkt_conn = get_market_conn(timeout=120)

    report = {"started_at": datetime.utcnow().isoformat(timespec="seconds"), "steps": {}}
    try:
        if args.step in ("all", "cox"):
            from services.sef.cox_survival import build_institution_capability

            report["steps"]["cox"] = build_institution_capability(conn)

        if args.step in ("all", "stock_char"):
            from services.sef.stock_character import build_stock_character

            report["steps"]["stock_char"] = build_stock_character(
                conn, mkt_conn, limit_stocks=args.limit_stocks
            )

        if args.step in ("all", "inst_style"):
            from services.sef.institution_style import build_institution_style

            report["steps"]["inst_style"] = build_institution_style(
                conn, mkt_conn, limit_inst=args.limit_inst
            )

        if args.step in ("all", "hmm"):
            from services.sef.hmm_regime import build_regime_state

            report["steps"]["hmm"] = build_regime_state(mkt_conn, conn)
    finally:
        conn.close()
        mkt_conn.close()

    report["finished_at"] = datetime.utcnow().isoformat(timespec="seconds")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
