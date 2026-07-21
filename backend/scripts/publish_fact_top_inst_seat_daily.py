#!/usr/bin/env python3
"""Publish fact_top_inst_seat_daily from raw_tushare_top_inst (B2 strangler).

Examples:
  PYTHONPATH=backend python backend/scripts/publish_fact_top_inst_seat_daily.py
  PYTHONPATH=backend python backend/scripts/publish_fact_top_inst_seat_daily.py \\
      --start 20260701 --end 20260720
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.top_inst_seat_publish import publish_fact_top_inst_seat_daily  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default=None, help="YYYYMMDD inclusive")
    ap.add_argument("--end", default=None, help="YYYYMMDD inclusive")
    args = ap.parse_args(argv)
    out = publish_fact_top_inst_seat_daily(start=args.start, end=args.end)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
