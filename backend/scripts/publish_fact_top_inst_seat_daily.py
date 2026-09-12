#!/usr/bin/env python3
"""Publish fact_top_inst_seat_daily from raw_tushare_top_inst (B2 strangler).

Examples:
  PYTHONPATH=backend python backend/scripts/publish_fact_top_inst_seat_daily.py
  PYTHONPATH=backend python backend/scripts/publish_fact_top_inst_seat_daily.py \\
      --start 20260701 --end 20260720
  PYTHONPATH=backend python backend/scripts/publish_fact_top_inst_seat_daily.py \\
      --audit-reasons
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

import services.top_inst_seat_publish as pub  # noqa: E402


def _run_audit_reasons() -> int:
    """Read-only scan of raw_tushare_top_inst for reason strings whose
    canonical form (top_inst_seat_publish.canonical_reason) is not
    registered in lhb_board_class.yaml (r2b §2.4 entry point: run this
    after a full relanding of top_inst, before a full rebuild of
    fact_top_inst_seat_daily, so new vendor strings get a one-shot verdict
    instead of tripping the fail-closed publisher mid-rebuild).

    Prints a JSON array of {reason, canonical, rows} (raw reason string,
    its canonical form, row count) sorted by rows desc. Exit code 1 if
    non-empty, 0 if every distinct reason under the table is known.
    """
    unknown = pub.audit_unknown_reasons_from_raw(table="raw_tushare_top_inst")
    out = [
        {"reason": reason, "canonical": pub.canonical_reason(reason), "rows": rows}
        for reason, rows in unknown
    ]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 1 if out else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default=None, help="YYYYMMDD inclusive")
    ap.add_argument("--end", default=None, help="YYYYMMDD inclusive")
    ap.add_argument(
        "--audit-reasons",
        action="store_true",
        help="只读扫描 raw_tushare_top_inst, 打印未登记理由 JSON (reason/canonical/rows), "
        "非空时退出码 1; 不发布",
    )
    args = ap.parse_args(argv)
    if args.audit_reasons:
        return _run_audit_reasons()
    out = pub.publish_fact_top_inst_seat_daily(start=args.start, end=args.end)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
