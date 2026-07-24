"""Type-B fact publish catchup — explicit ops CLI (no provider I/O).

Publishes bounded raw→fact windows for moneyflow/limit/index/dc_member/top_inst
when landing raw MAX(trade_date) leads fact MAX. Same module as pipeline acquire
hook; use this to clear lag immediately after registry drain.

Usage:
    python backend/scripts/type_b_fact_publish_catchup_cli.py
    python backend/scripts/type_b_fact_publish_catchup_cli.py --max-days 7
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.type_b_fact_publish_catchup import (  # noqa: E402
    TYPE_B_PUBLISH_CATCHUP_MAX_DAYS,
    catchup_type_b_fact_publish,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Type-B bounded raw→fact publish catchup")
    ap.add_argument(
        "--max-days",
        type=int,
        default=TYPE_B_PUBLISH_CATCHUP_MAX_DAYS,
        help=f"Max calendar days per domain (default {TYPE_B_PUBLISH_CATCHUP_MAX_DAYS})",
    )
    args = ap.parse_args()
    out = catchup_type_b_fact_publish(max_days=args.max_days)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    if out.get("status") == "error":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
