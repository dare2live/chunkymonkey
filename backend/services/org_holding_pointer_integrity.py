"""org_holding accepted_partition pointer vs full canonical partition.

Class-A guard: multi-report_date sharing one available_date must not leave the
accepted pointer describing only the last batch.
"""
from __future__ import annotations

from typing import Any

from services.data_sources.accepted_schema import ACCEPTED_TABLE
from services.data_sources.org_holding_schema import (
    CANONICAL_TABLE,
    DATASET_ID,
    PARTITION_FIELD,
)


def count_org_pointer_mismatches(conn) -> list[dict[str, Any]]:
    """Return partitions where accepted row_count != full canonical COUNT(*)."""
    rows = conn.execute(
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
          LEFT JOIN can c USING (partition_value)
         WHERE c.canon_n IS NULL OR p.row_count <> c.canon_n
         ORDER BY 1
        """,
        [DATASET_ID],
    ).fetchall()
    return [
        {
            "partition_value": str(r[0]),
            "pointer_row_count": int(r[1]),
            "canonical_row_count": int(r[2]) if r[2] is not None else 0,
        }
        for r in rows
    ]


__all__ = ["count_org_pointer_mismatches"]
