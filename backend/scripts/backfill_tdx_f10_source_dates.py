#!/usr/bin/env python3
"""Backfill explicit source-date columns for parsed TDX F10 fact tables."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.tdx_f10_extra_client import (  # noqa: E402
    backfill_tdx_f10_source_dates,
    build_tdx_f10_capability_matrix,
)


def main() -> int:
    conn = get_conn()
    try:
        result = backfill_tdx_f10_source_dates(conn)
        result["capability_matrix"] = build_tdx_f10_capability_matrix(conn)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        remaining = sum(
            int(item.get("remaining_missing") or 0)
            for item in result.get("tables", {}).values()
            if isinstance(item, dict)
        )
        return 0 if remaining == 0 else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
