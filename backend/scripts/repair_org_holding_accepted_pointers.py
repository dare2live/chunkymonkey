#!/usr/bin/env python3
"""Repair org_holding accepted_partition pointers to full canonical partition stats.

Class-A: after report_dates_in_batch merge, pointer row_count/content_hash must
match the merged canonical available_date partition (not the last batch alone).

Fast path: SQL COUNT finds mismatches; only those partitions recompute content_hash.

Usage:
  PYTHONPATH=backend .venv/bin/python backend/scripts/repair_org_holding_accepted_pointers.py
  PYTHONPATH=backend .venv/bin/python backend/scripts/repair_org_holding_accepted_pointers.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.data_sources.accepted_schema import ACCEPTED_TABLE  # noqa: E402
from services.data_sources.disclosure_event_partition import (  # noqa: E402
    partition_accepted_pointer_stats,
)
from services.data_sources.org_holding_acceptance import DOMAIN  # noqa: E402
from services.data_sources.org_holding_schema import (  # noqa: E402
    CANONICAL_TABLE,
    DATASET_ID,
    PARTITION_FIELD,
)
from services.database_manifest import get_database_manifest  # noqa: E402
from services.duck_adapter import connect as duck_connect  # noqa: E402


def _count_mismatches(con) -> list[tuple[str, int, int]]:
    rows = con.execute(
        f"""
        WITH ptr AS (
          SELECT partition_value, row_count
            FROM {ACCEPTED_TABLE}
           WHERE dataset_id = ?
        ),
        can AS (
          SELECT {PARTITION_FIELD} AS partition_value, COUNT(*) AS canon_n
            FROM {CANONICAL_TABLE}
           GROUP BY 1
        )
        SELECT p.partition_value, p.row_count, c.canon_n
          FROM ptr p
          JOIN can c USING (partition_value)
         WHERE p.row_count <> c.canon_n
         ORDER BY 1
        """,
        [DATASET_ID],
    ).fetchall()
    return [(str(r[0]), int(r[1]), int(r[2])) for r in rows]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    path = get_database_manifest().path_for("smartmoney")
    con = duck_connect(str(path), read_only=bool(args.dry_run))
    repaired: list[dict] = []
    try:
        mismatches = _count_mismatches(con)
        print(
            f"org_pointer_repair: count_mismatches={len(mismatches)} "
            f"dry_run={args.dry_run}",
            flush=True,
        )
        for pv, before_n, canon_n in mismatches:
            print(f"  hashing partition={pv} canon_n={canon_n} ...", flush=True)
            after_n, after_hash = partition_accepted_pointer_stats(con, DOMAIN, pv)
            if after_n != canon_n:
                raise SystemExit(
                    f"partition {pv}: COUNT={canon_n} but hash scan n={after_n}"
                )
            before_hash = con.execute(
                f"""
                SELECT content_hash FROM {ACCEPTED_TABLE}
                 WHERE dataset_id = ? AND partition_value = ?
                """,
                [DATASET_ID, pv],
            ).fetchone()[0]
            item = {
                "partition_value": pv,
                "before_row_count": before_n,
                "after_row_count": after_n,
                "before_hash": str(before_hash),
                "after_hash": str(after_hash),
            }
            if not args.dry_run:
                con.execute(
                    f"""
                    UPDATE {ACCEPTED_TABLE}
                       SET row_count = ?, content_hash = ?
                     WHERE dataset_id = ? AND partition_value = ?
                    """,
                    [after_n, after_hash, DATASET_ID, pv],
                )
            repaired.append(item)
            print(f"  done partition={pv} {before_n}->{after_n}", flush=True)
    finally:
        con.close()

    out = {
        "dataset_id": DATASET_ID,
        "dry_run": bool(args.dry_run),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "repaired": len(repaired),
        "partitions": repaired,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if args.json_out:
        out_path = Path(args.json_out)
        if not out_path.is_absolute():
            out_path = REPO / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
