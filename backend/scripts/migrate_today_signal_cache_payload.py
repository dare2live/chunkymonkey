#!/usr/bin/env python3
"""Migrate legacy today-signal whole-cache JSON blobs into bounded detail rows."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.signals_v2 import migrate_today_signal_cache_payload  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply the migration. Omit for a read-only dry run.",
    )
    args = parser.parse_args()
    with get_conn() as conn:
        result = migrate_today_signal_cache_payload(conn, execute=args.execute)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
