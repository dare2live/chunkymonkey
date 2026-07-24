"""Holders notice_date hole repair — announcement axis, not report_period.

Companion to ``holders_aif10`` incremental. MAX(notice_date) can advance while
sparse mid-period UPDATE_DATE partitions stay only in legacy fact (measured
2026-07-24: 600388 notice 20260613). Repair is holdernumber-class:
local-fact accept + optional forward by_notice day land. Never by_ts_code mass
or org by-period invent. Evidence: analysis/holders_ann_date_axis_20260724.md.
"""
from __future__ import annotations

from datetime import datetime, timedelta

NOTICE_PARTITION_CATCHUP_MAX = 40  # eng_gov ≤40d / max partitions per run
CANONICAL_TABLE = "canonical_top10_float_holders_period"
FACT_TABLE = "fact_top10_holder_period"
SOURCE = "miaoxiang"


def _table_present(conn, name: str) -> bool:
    try:
        r = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
            [name],
        ).fetchone()
        return r is not None
    except Exception:  # noqa: BLE001
        return False


def list_missing_notice_partitions_from_fact(
    conn, *, limit: int = NOTICE_PARTITION_CATCHUP_MAX
) -> list[str]:
    """Fact notice_dates absent from canonical (newest first; local-only)."""
    if limit <= 0:
        return []
    if not _table_present(conn, CANONICAL_TABLE):
        return []
    if not _table_present(conn, FACT_TABLE):
        return []
    # CTE names must not use fact_/mart_/dim_/raw_/stg_ prefixes — dead-ref
    # scanner treats those as physical table refs (check_dead_references E).
    rows = conn.execute(
        f"""
        WITH legacy_notice AS (
          SELECT DISTINCT replace(CAST(notice_date AS VARCHAR), '-', '') AS nd
            FROM {FACT_TABLE}
           WHERE source = ?
             AND notice_date IS NOT NULL
        ),
        accepted_notice AS (
          SELECT DISTINCT replace(CAST(notice_date AS VARCHAR), '-', '') AS nd
            FROM {CANONICAL_TABLE}
        )
        SELECT ln.nd
          FROM legacy_notice ln
          LEFT JOIN accepted_notice an ON ln.nd = an.nd
         WHERE an.nd IS NULL
           AND length(ln.nd) = 8
         ORDER BY ln.nd DESC
         LIMIT ?
        """,
        [SOURCE, int(limit)],
    ).fetchall()
    return [str(r[0]) for r in rows if r and r[0]]


def catchup_missing_holders_notice_partitions(
    conn, *, max_partitions: int = NOTICE_PARTITION_CATCHUP_MAX
) -> dict:
    """Accept missing notice partitions from local fact (no provider mass)."""
    from services.holders_aif10 import accept_holders_top10_partition_from_legacy

    missing = list_missing_notice_partitions_from_fact(
        conn, limit=max_partitions
    )
    repaired: list[str] = []
    errors: list[str] = []
    for nd in missing:
        try:
            accept_holders_top10_partition_from_legacy(conn, nd)
            repaired.append(nd)
        except Exception as exc:  # noqa: BLE001
            if len(errors) < 20:
                errors.append(f"{nd}:{type(exc).__name__}:{str(exc)[:80]}")
    if repaired or errors:
        print(
            f"holders_aif10: notice-partition catchup "
            f"repaired={len(repaired)} missing={len(missing)} "
            f"errors={len(errors)}"
        )
    return {
        "missing_partitions": missing,
        "repaired_partitions": repaired,
        "errors": errors,
        "catchup_source": "local_fact_notice",
    }


def _canonical_has_notice_partition(conn, notice_date: str) -> bool:
    digits = "".join(ch for ch in str(notice_date or "") if ch.isdigit())
    if len(digits) < 8 or not _table_present(conn, CANONICAL_TABLE):
        return False
    part = digits[:8]
    row = conn.execute(
        f"SELECT 1 FROM {CANONICAL_TABLE} WHERE notice_date = ? LIMIT 1",
        [part],
    ).fetchone()
    return row is not None


def land_holders_notice_partitions_forward(
    conn,
    *,
    from_exclusive: str,
    to_inclusive: str,
    max_partitions: int = NOTICE_PARTITION_CATCHUP_MAX,
) -> dict:
    """Forward fill: full-market by UPDATE_DATE/notice_date for absent days."""
    from services.holders_aif10 import _write, fetch_holders_top10_by_notice_date

    start = "".join(ch for ch in str(from_exclusive or "") if ch.isdigit())[:8]
    end = "".join(ch for ch in str(to_inclusive or "") if ch.isdigit())[:8]
    if len(start) != 8 or len(end) != 8 or end <= start or max_partitions <= 0:
        return {
            "landed_partitions": [],
            "empty_partitions": [],
            "errors": [],
            "catchup_source": "provider_by_notice_date",
        }
    d0 = datetime.strptime(start, "%Y%m%d") + timedelta(days=1)
    d1 = datetime.strptime(end, "%Y%m%d")
    landed: list[str] = []
    empty: list[str] = []
    errors: list[str] = []
    while d0 <= d1 and len(landed) < max_partitions:
        nd = d0.strftime("%Y%m%d")
        d0 += timedelta(days=1)
        if _canonical_has_notice_partition(conn, nd):
            continue
        try:
            rows = fetch_holders_top10_by_notice_date(nd)
        except Exception as exc:  # noqa: BLE001
            if len(errors) < 20:
                errors.append(f"{nd}:{type(exc).__name__}:{str(exc)[:80]}")
            continue
        if not rows:
            empty.append(nd)
            continue
        try:
            _write(conn, rows)
            landed.append(nd)
        except Exception as exc:  # noqa: BLE001
            if len(errors) < 20:
                errors.append(f"{nd}:write:{type(exc).__name__}:{str(exc)[:80]}")
    if landed or errors:
        print(
            f"holders_aif10: forward by_notice "
            f"landed={len(landed)} empty={len(empty)} errors={len(errors)} "
            f"range=({start},{end}]"
        )
    return {
        "landed_partitions": landed,
        "empty_partitions": empty,
        "errors": errors,
        "catchup_source": "provider_by_notice_date",
    }


__all__ = [
    "NOTICE_PARTITION_CATCHUP_MAX",
    "catchup_missing_holders_notice_partitions",
    "land_holders_notice_partitions_forward",
    "list_missing_notice_partitions_from_fact",
]
