"""org_holding accepted_partition pointer vs full canonical partition.

Class-A guard: multi-report_date sharing one available_date must not leave the
accepted pointer describing only the last batch. Compares row_count AND
content_hash; reports pointer-only and canonical-only partitions.
"""
from __future__ import annotations

from typing import Any

from services.data_sources.accepted_schema import ACCEPTED_TABLE
from services.data_sources.disclosure_event_partition import (
    partition_accepted_pointer_stats,
)
from services.data_sources.org_holding_acceptance import DOMAIN
from services.data_sources.org_holding_schema import (
    CANONICAL_TABLE,
    DATASET_ID,
    PARTITION_FIELD,
)


def count_org_pointer_mismatches(
    conn,
    *,
    verify_content_hash: bool = True,
) -> list[dict[str, Any]]:
    """Return partitions where pointer is missing, orphaned, count-drifts, or hash-drifts.

    ``verify_content_hash=False`` keeps the FULL OUTER JOIN count/direction
    check only (fast). Default True recomputes full-partition content_hash.
    """
    rows = conn.execute(
        f"""
        WITH ptr AS (
          SELECT replace(CAST(partition_value AS VARCHAR), '-', '') AS partition_value,
                 row_count,
                 content_hash
            FROM {ACCEPTED_TABLE}
           WHERE dataset_id = ?
        ),
        can AS (
          SELECT replace(CAST({PARTITION_FIELD} AS VARCHAR), '-', '') AS partition_value,
                 COUNT(*) AS canon_n
            FROM {CANONICAL_TABLE}
           GROUP BY 1
        )
        SELECT COALESCE(p.partition_value, c.partition_value) AS partition_value,
               p.row_count AS pointer_row_count,
               c.canon_n AS canonical_row_count,
               p.content_hash AS pointer_content_hash
          FROM ptr p
          FULL OUTER JOIN can c USING (partition_value)
         ORDER BY 1
        """,
        [DATASET_ID],
    ).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        pv = str(r[0])
        ptr_n = int(r[1]) if r[1] is not None else None
        can_n = int(r[2]) if r[2] is not None else None
        ptr_hash = str(r[3]) if r[3] is not None else None

        if ptr_n is None:
            out.append(
                {
                    "partition_value": pv,
                    "pointer_row_count": None,
                    "canonical_row_count": can_n,
                    "pointer_content_hash": None,
                    "canonical_content_hash": None,
                    "reason": "pointer_missing",
                }
            )
            continue
        if can_n is None:
            out.append(
                {
                    "partition_value": pv,
                    "pointer_row_count": ptr_n,
                    "canonical_row_count": None,
                    "pointer_content_hash": ptr_hash,
                    "canonical_content_hash": None,
                    "reason": "canonical_missing",
                }
            )
            continue
        if ptr_n != can_n:
            canon_hash = None
            if verify_content_hash:
                try:
                    _, canon_hash = partition_accepted_pointer_stats(conn, DOMAIN, pv)
                except Exception:  # noqa: BLE001
                    pass
            out.append(
                {
                    "partition_value": pv,
                    "pointer_row_count": ptr_n,
                    "canonical_row_count": can_n,
                    "pointer_content_hash": ptr_hash,
                    "canonical_content_hash": canon_hash,
                    "reason": "row_count_mismatch",
                }
            )
            continue

        if not verify_content_hash:
            continue

        # Count matches — still verify content_hash against full partition.
        try:
            recomputed_n, canon_hash = partition_accepted_pointer_stats(
                conn, DOMAIN, pv
            )
        except Exception as exc:  # noqa: BLE001
            out.append(
                {
                    "partition_value": pv,
                    "pointer_row_count": ptr_n,
                    "canonical_row_count": can_n,
                    "pointer_content_hash": ptr_hash,
                    "canonical_content_hash": None,
                    "reason": f"hash_recompute_failed:{type(exc).__name__}",
                }
            )
            continue
        if recomputed_n != can_n or (ptr_hash or "") != canon_hash:
            out.append(
                {
                    "partition_value": pv,
                    "pointer_row_count": ptr_n,
                    "canonical_row_count": can_n,
                    "pointer_content_hash": ptr_hash,
                    "canonical_content_hash": canon_hash,
                    "reason": (
                        "content_hash_mismatch"
                        if ptr_n == can_n and recomputed_n == can_n
                        else "row_count_or_hash_mismatch"
                    ),
                }
            )
    return out


__all__ = ["count_org_pointer_mismatches"]
