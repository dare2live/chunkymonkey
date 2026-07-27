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
from typing import Callable

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


def _count_mismatches(con) -> list[dict]:
    from services.org_holding_pointer_integrity import count_org_pointer_mismatches

    return list(count_org_pointer_mismatches(con))


def repair_connection(
    con,
    *,
    dry_run: bool,
    after_update: Callable[[str], None] | None = None,
) -> dict:
    """Repair every repairable mismatch as one transaction, then re-verify.

    Missing canonical or pointer sides are not inferable by this utility and
    therefore fail closed instead of being silently skipped.
    """

    transaction_open = False
    repaired: list[dict] = []
    try:
        if not dry_run:
            con.execute("BEGIN TRANSACTION")
            transaction_open = True
        mismatches = _count_mismatches(con)
        unresolved = [
            item
            for item in mismatches
            if str(item.get("reason") or "")
            in {"pointer_missing", "canonical_missing"}
            or str(item.get("reason") or "").startswith("hash_recompute_failed:")
        ]
        if unresolved:
            reasons = ", ".join(
                f"{item.get('partition_value')}:{item.get('reason')}"
                for item in unresolved
            )
            raise RuntimeError(f"unresolved org pointer mismatches: {reasons}")

        for mismatch in mismatches:
            pv = str(mismatch["partition_value"])
            before_n = int(mismatch["pointer_row_count"])
            canon_n = int(mismatch["canonical_row_count"])
            after_n, after_hash = partition_accepted_pointer_stats(con, DOMAIN, pv)
            if after_n != canon_n:
                raise RuntimeError(
                    f"partition {pv}: COUNT={canon_n} but hash scan n={after_n}"
                )
            before_hash = str(mismatch.get("pointer_content_hash") or "")
            item = {
                "partition_value": pv,
                "reason": str(mismatch.get("reason") or "unknown"),
                "before_row_count": before_n,
                "after_row_count": after_n,
                "before_hash": before_hash,
                "after_hash": str(after_hash),
            }
            if not dry_run:
                result = con.execute(
                    f"""
                    UPDATE {ACCEPTED_TABLE}
                       SET row_count = ?, content_hash = ?
                     WHERE dataset_id = ?
                       AND replace(CAST(partition_value AS VARCHAR), '-', '') = ?
                       AND row_count = ?
                       AND content_hash = ?
                    """,
                    [after_n, after_hash, DATASET_ID, pv, before_n, before_hash],
                )
                if int(getattr(result, "rowcount", -1)) == 0:
                    # DuckDB rowcount is not reliable across all adapters; the
                    # exact before-state is checked explicitly below.
                    current = con.execute(
                        f"""
                        SELECT row_count, content_hash
                          FROM {ACCEPTED_TABLE}
                         WHERE dataset_id = ?
                           AND replace(CAST(partition_value AS VARCHAR), '-', '') = ?
                        """,
                        [DATASET_ID, pv],
                    ).fetchone()
                    if current is None or (
                        int(current[0]),
                        str(current[1]),
                    ) != (after_n, str(after_hash)):
                        raise RuntimeError(
                            f"partition {pv}: pointer changed during repair"
                        )
                if after_update is not None:
                    after_update(pv)
            repaired.append(item)

        if not dry_run:
            remaining = _count_mismatches(con)
            if remaining:
                raise RuntimeError(
                    "post-repair verifier still reports mismatches: "
                    + json.dumps(remaining[:5], ensure_ascii=False)
                )
            con.execute("COMMIT")
            transaction_open = False
        return {
            "mismatches": len(mismatches),
            "repaired": len(repaired),
            "partitions": repaired,
        }
    except Exception:
        if transaction_open:
            con.execute("ROLLBACK")
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    path = get_database_manifest().path_for("smartmoney")
    con = duck_connect(str(path), read_only=bool(args.dry_run))
    try:
        result = repair_connection(con, dry_run=bool(args.dry_run))
        print(
            f"org_pointer_repair: count_mismatches={result['mismatches']} "
            f"dry_run={args.dry_run}",
            flush=True,
        )
    finally:
        con.close()

    out = {
        "dataset_id": DATASET_ID,
        "dry_run": bool(args.dry_run),
        "as_of": datetime.now(timezone.utc).isoformat(),
        **result,
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
