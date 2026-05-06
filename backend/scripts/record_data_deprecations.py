#!/usr/bin/env python3
"""Record data-asset deprecation metadata without dropping tables."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.data_deprecation import record_data_deprecations  # noqa: E402
from services.db import get_conn  # noqa: E402


logger = logging.getLogger("record_data_deprecations")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_conn()
    try:
        result = record_data_deprecations(conn, dry_run=args.dry_run)
        logger.info("data deprecation result: %s", result)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
