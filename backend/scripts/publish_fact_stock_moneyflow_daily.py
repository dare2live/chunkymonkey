#!/usr/bin/env python3
"""Publish fact_stock_moneyflow(_dc)_daily from raw moneyflow tables (B2 strangler).

Examples:
  PYTHONPATH=backend python backend/scripts/publish_fact_stock_moneyflow_daily.py
  PYTHONPATH=backend python backend/scripts/publish_fact_stock_moneyflow_daily.py \\
      --which both --start 20260701 --end 20260720
  PYTHONPATH=backend python backend/scripts/publish_fact_stock_moneyflow_daily.py \\
      --which moneyflow
  PYTHONPATH=backend python backend/scripts/publish_fact_stock_moneyflow_daily.py \\
      --which moneyflow_dc
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.stock_moneyflow_publish import (  # noqa: E402
    publish_fact_stock_moneyflow_daily,
    publish_fact_stock_moneyflow_dc_daily,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--which",
        choices=("both", "moneyflow", "moneyflow_dc"),
        default="both",
        help="which publication plane to rebuild (default: both)",
    )
    ap.add_argument("--start", default=None, help="YYYYMMDD inclusive")
    ap.add_argument("--end", default=None, help="YYYYMMDD inclusive")
    args = ap.parse_args(argv)
    out: list[dict] = []
    if args.which in ("both", "moneyflow"):
        out.append(
            publish_fact_stock_moneyflow_daily(start=args.start, end=args.end)
        )
    if args.which in ("both", "moneyflow_dc"):
        out.append(
            publish_fact_stock_moneyflow_dc_daily(start=args.start, end=args.end)
        )
    print(json.dumps(out if len(out) > 1 else out[0], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
