"""Backfill conservative PIT availability dates for holder period facts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))

from services.db import get_conn, init_db  # noqa: E402
from services.holder_availability import backfill_holder_period_availability_rows  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-init", action="store_true", help="skip init_db before backfill")
    parser.add_argument(
        "--overwrite-regulatory",
        action="store_true",
        help="recompute rows previously filled from regulatory_deadline",
    )
    args = parser.parse_args()

    if not args.no_init:
        init_db()
    conn = get_conn()
    try:
        result = backfill_holder_period_availability_rows(
            conn,
            overwrite_regulatory=args.overwrite_regulatory,
        )
        conn.commit()
    finally:
        conn.close()
    print(result)


if __name__ == "__main__":
    main()
