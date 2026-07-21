#!/usr/bin/env python3
"""chunkyctl derive — S5 independent qfq/form rebuild (no acquire/accept)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.derive_runtime import DERIVE_TARGETS, run_derive  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "S5 derive: rebuild qfq or form from accepted/canonical inputs. "
            "Independent of acquire; does not run inside accept."
        )
    )
    ap.add_argument(
        "target",
        choices=sorted(DERIVE_TARGETS),
        help="derive target: qfq (price_kline_qfq_tushare) or form (fact_stock_form_daily)",
    )
    ap.add_argument(
        "--from-accepted",
        action="store_true",
        help="S5: nominal from accepted canonical only (no legacy raw_tushare_daily fill)",
    )
    ap.add_argument(
        "--rebuild",
        action="store_true",
        help="form only: full rebuild_all (default is incremental build_latest)",
    )
    ap.add_argument(
        "--check-only",
        action="store_true",
        help="qfq only: sanity check without rebuild",
    )
    args = ap.parse_args(argv)
    if args.target == "qfq" and args.rebuild:
        ap.error("--rebuild applies to form only")
    if args.target == "form" and args.check_only:
        ap.error("--check-only applies to qfq only")
    try:
        result = run_derive(
            args.target,
            from_accepted=bool(args.from_accepted),
            rebuild=bool(args.rebuild),
            check_only=bool(args.check_only),
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, default=str))
    if args.target == "qfq":
        return int(result.get("returncode") or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
