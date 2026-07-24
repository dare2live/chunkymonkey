"""stk_holdertrade tip-leap catchup — ann_date axis, local raw → formal.

MAX(ann_date) can leap while mid partitions remain only in
``raw_tushare_stk_holdertrade``. Due-set law is shared
``plan_partition_catchup`` (source\\accepted, P≤watermark — not tip+1).

Evidence: ``analysis/partition_leap_integrity_20260724.md``.
"""
from __future__ import annotations

from typing import Any

from services.data_sources.frontier_decision import plan_partition_catchup
from services.data_sources.stk_holdertrade_schema import (
    CANONICAL_TABLE,
    COMPATIBILITY_TABLE,
    PROVIDER_FIELDS,
)

ANN_PARTITION_CATCHUP_MAX = 40


def _table_present(conn: Any, name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
            [name],
        ).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001
        return False


def _distinct_ann_dates(conn: Any, table: str) -> list[str]:
    if not _table_present(conn, table):
        return []
    rows = conn.execute(
        f"""
        SELECT DISTINCT replace(CAST(ann_date AS VARCHAR), '-', '') AS ad
          FROM {table}
         WHERE ann_date IS NOT NULL
        """
    ).fetchall()
    out: list[str] = []
    for row in rows:
        if not row or not row[0]:
            continue
        digits = "".join(ch for ch in str(row[0]) if ch.isdigit())
        if len(digits) >= 8:
            out.append(digits[:8])
    return out


def _canonical_watermark(conn: Any) -> str | None:
    if not _table_present(conn, CANONICAL_TABLE):
        return None
    row = conn.execute(
        f"""
        SELECT replace(CAST(MAX(ann_date) AS VARCHAR), '-', '')
          FROM {CANONICAL_TABLE}
        """
    ).fetchone()
    if not row or not row[0]:
        return None
    digits = "".join(ch for ch in str(row[0]) if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else None


def list_missing_holdertrade_ann_partitions(
    conn: Any, *, limit: int = ANN_PARTITION_CATCHUP_MAX
) -> list[str]:
    """Raw ann_dates absent from canonical (newest first; local-only)."""
    if limit <= 0:
        return []
    plan = plan_partition_catchup(
        axis="ann_date",
        source_partitions=_distinct_ann_dates(conn, COMPATIBILITY_TABLE),
        accepted_partitions=_distinct_ann_dates(conn, CANONICAL_TABLE),
        watermark=_canonical_watermark(conn),
        max_partitions=limit,
        order="newest_first",
    )
    return list(plan.due_partitions)


def _rows_for_ann(conn: Any, ann_date: str) -> list[dict[str, Any]]:
    cols = ", ".join(PROVIDER_FIELDS)
    rows = conn.execute(
        f"""
        SELECT {cols}
          FROM {COMPATIBILITY_TABLE}
         WHERE replace(CAST(ann_date AS VARCHAR), '-', '') = ?
        """,
        [ann_date],
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = {name: row[i] for i, name in enumerate(PROVIDER_FIELDS)}
        item["ann_date"] = ann_date
        out.append(item)
    return out


def catchup_missing_holdertrade_ann_partitions(
    conn: Any, *, max_partitions: int = ANN_PARTITION_CATCHUP_MAX
) -> dict[str, Any]:
    """Accept missing ann partitions from local raw via formal land→accept."""
    from services.data_sources.disclosure_dual_write import (
        write_stk_holdertrade_formal_then_mirror,
    )

    missing = list_missing_holdertrade_ann_partitions(
        conn, limit=max_partitions
    )
    repaired: list[str] = []
    errors: list[str] = []
    canonical_rows = 0
    for ann in missing:
        try:
            rows = _rows_for_ann(conn, ann)
            if not rows:
                continue
            outcome = write_stk_holdertrade_formal_then_mirror(
                conn, rows, enable_legacy_mirror=False
            )
            repaired.append(ann)
            canonical_rows += int(outcome.canonical_rows)
        except Exception as exc:  # noqa: BLE001
            if len(errors) < 20:
                errors.append(f"{ann}:{type(exc).__name__}:{str(exc)[:80]}")
    if repaired or errors:
        print(
            f"stk_holdertrade: ann-partition catchup "
            f"repaired={len(repaired)} missing={len(missing)} "
            f"errors={len(errors)}"
        )
    return {
        "missing_partitions": missing,
        "repaired_partitions": repaired,
        "errors": errors,
        "canonical_rows": canonical_rows,
        "catchup_source": "local_raw_ann",
        "catchup_law": "plan_partition_catchup",
    }


__all__ = [
    "ANN_PARTITION_CATCHUP_MAX",
    "catchup_missing_holdertrade_ann_partitions",
    "list_missing_holdertrade_ann_partitions",
]
