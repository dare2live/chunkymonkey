"""SEF Phase IV 一键运行: Bandit + Drift + Counterfactual + Walk-Forward."""

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
        choices=["all", "bandit", "drift", "counterfactual", "walk_forward"],
    )
    args = parser.parse_args()

    from services.db import get_conn

    conn = get_conn(timeout=120)
    report = {"started_at": datetime.utcnow().isoformat(timespec="seconds"), "steps": {}}

    try:
        if args.step in ("all", "bandit"):
            from services.sef.thompson_sampling import update_bandit_state

            report["steps"]["bandit"] = update_bandit_state(conn)

        if args.step in ("all", "drift"):
            from services.sef.drift_monitor import run_drift_monitor

            report["steps"]["drift"] = run_drift_monitor(conn)

        if args.step in ("all", "counterfactual"):
            from services.sef.counterfactual import run_counterfactual

            report["steps"]["counterfactual"] = run_counterfactual(conn)

        if args.step in ("all", "walk_forward"):
            from services.sef.walk_forward import run_walk_forward

            report["steps"]["walk_forward"] = run_walk_forward(
                conn, train_window_months=6, test_window_months=2
            )
    finally:
        conn.close()

    report["finished_at"] = datetime.utcnow().isoformat(timespec="seconds")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
