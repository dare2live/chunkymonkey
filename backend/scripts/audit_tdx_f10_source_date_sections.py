#!/usr/bin/env python3
"""Audit source-date phrases by section in captured TDX/F10 holder research."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.tdx_f10_source_date_audit import audit_tdx_f10_source_date_sections  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    conn = get_conn(timeout=120)
    try:
        result = audit_tdx_f10_source_date_sections(
            conn,
            run_id=args.run_id,
            limit=args.limit,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
