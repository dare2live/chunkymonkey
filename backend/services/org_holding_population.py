"""Org-holding population probes (existence ≠ population).

Authority: backend/services/pipeline/closed_loop.py (派生新鲜度闭环法, 法条正文见该模块 docstring)
Kept out of org_holding_aif10.py to avoid god-file ratchet growth.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from services.pipeline.closed_loop import evaluate_org_population

PROBE_TABLE = "org_holding_source_probe"
_PROBE_DDL = f"""
CREATE TABLE IF NOT EXISTS {PROBE_TABLE} (
    report_date VARCHAR PRIMARY KEY,
    source_count INTEGER NOT NULL,
    local_rows INTEGER NOT NULL,
    new_grains INTEGER NOT NULL,
    observed_at VARCHAR NOT NULL
)
"""


def ensure_probe_table(conn: Any) -> None:
    conn.execute(_PROBE_DDL)


COUNT_PROBE_MIN_DELTA = 500
COUNT_PROBE_RATIO = 0.001


def source_count_ahead(
    *,
    local_rows: int,
    source_count: int,
    last_reconciled_count: int | None = None,
    page_size: int | None = None,
) -> bool:
    """True when Eastmoney page-1 count justifies a one-period MERGE, not a skip.

    Margin is max(500, 0.1% of local) — enough to ignore envelope jitter
    (~hundreds) without swallowing a real late-filing delta (年报 live
    +1545 was skipped when the bar was one 2000-row page). After a fetch
    that added zero new grains, ``last_reconciled_count`` blocks re-pull
    until the source count itself grows by that margin.
    """
    size = int(page_size if page_size is not None else COUNT_PROBE_MIN_DELTA)
    src = int(source_count or 0)
    local = int(local_rows or 0)
    if src <= 0:
        return False
    margin = max(size, int(max(local, 1) * COUNT_PROBE_RATIO))
    if last_reconciled_count is not None:
        if src < int(last_reconciled_count) + margin:
            return False
    if local <= 0:
        return True
    return src >= local + margin


def read_reconciled_source_count(conn: Any, report_date: str) -> int | None:
    ensure_probe_table(conn)
    try:
        row = conn.execute(
            f"SELECT source_count FROM {PROBE_TABLE} WHERE report_date = ?",
            [report_date],
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not row or row[0] is None:
        return None
    return int(row[0])


def write_source_probe(
    conn: Any,
    report_date: str,
    *,
    source_count: int,
    local_rows: int,
    new_grains: int,
) -> None:
    ensure_probe_table(conn)
    observed = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        f"""
        INSERT INTO {PROBE_TABLE}
            (report_date, source_count, local_rows, new_grains, observed_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (report_date) DO UPDATE SET
            source_count = excluded.source_count,
            local_rows = excluded.local_rows,
            new_grains = excluded.new_grains,
            observed_at = excluded.observed_at
        """,
        [report_date, int(source_count), int(local_rows), int(new_grains), observed],
    )
    conn.commit()


def plannable_available_yyyymmdd(report_date: str) -> Optional[str]:
    from services.data_sources.org_holding_schema import disclosure_deadline_yyyymmdd

    return disclosure_deadline_yyyymmdd(report_date)


def list_local_org_report_periods(conn: Any) -> list[str]:
    """Distinct report_date values present in legacy raw org holding."""
    try:
        rows = conn.execute(
            "SELECT DISTINCT report_date FROM raw_org_holding_aif10"
        ).fetchall()
    except Exception:  # noqa: BLE001
        return []
    out: list[str] = []
    for row in rows:
        if not row or not row[0]:
            continue
        text = str(row[0]).strip()[:10]
        if text:
            out.append(text)
    return out


def count_raw_org_stocks(conn: Any, report_date: str) -> int:
    try:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT stock_code)
              FROM raw_org_holding_aif10
             WHERE report_date = ? OR report_date = ?
            """,
            [report_date, report_date.replace("-", "")],
        ).fetchone()
    except Exception:  # noqa: BLE001
        return 0
    return int(row[0] or 0) if row else 0


def count_accepted_org_stocks(conn: Any, report_date: str) -> Optional[int]:
    """Distinct accepted stocks for this report period; None if table missing.

    Count by ``report_date`` only. After announcement-day partitions, matching
    ``available_date`` to the statutory deadline misses the real rows.
    """
    from services.data_sources.org_holding_schema import CANONICAL_TABLE

    try:
        conn.execute(f"SELECT 1 FROM {CANONICAL_TABLE} LIMIT 0")
    except Exception:  # noqa: BLE001
        return None
    try:
        row = conn.execute(
            f"""
            SELECT COUNT(DISTINCT stock_code)
              FROM {CANONICAL_TABLE}
             WHERE report_date = ? OR report_date = ?
            """,
            [report_date, report_date.replace("-", "")],
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return int(row[0] or 0) if row else 0


def count_raw_org_rows(conn: Any, report_date: str) -> int:
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
              FROM raw_org_holding_aif10
             WHERE report_date = ? OR report_date = ?
            """,
            [report_date, report_date.replace("-", "")],
        ).fetchone()
    except Exception:  # noqa: BLE001
        return 0
    return int(row[0] or 0) if row else 0


def population_for_period(
    conn: Any,
    *,
    report_date: str,
    local_has: bool,
    accepted_has: bool,
) -> dict[str, Any]:
    raw_stocks = count_raw_org_stocks(conn, report_date) if local_has else 0
    raw_rows = count_raw_org_rows(conn, report_date) if local_has else 0
    accepted_stocks = (
        count_accepted_org_stocks(conn, report_date) if accepted_has else 0
    )
    baseline = max_accepted_stocks_across_partitions(conn)
    from services.data_sources.pagination_integrity import (
        provider_truncated_heuristic,
        under_modern_baseline_stocks,
    )
    from services.org_holding_aif10 import PAGE_SIZE

    # Hard only — modern baseline soft-observe must not queue mass re-fetch.
    truncated, trunc_reasons = provider_truncated_heuristic(
        landed_rows=raw_rows,
        landed_stocks=raw_stocks,
        baseline_stocks=baseline,
        page_size=PAGE_SIZE,
        include_baseline_ratio=False,
    )
    soft_under, soft_reasons = under_modern_baseline_stocks(
        landed_stocks=raw_stocks,
        baseline_stocks=baseline,
    )
    if accepted_has and accepted_stocks is None:
        return {
            "under_populated": False,
            "provider_truncated": truncated,
            "under_modern_baseline": soft_under,
            "accepted_stocks": None,
            "raw_stocks": raw_stocks,
            "raw_rows": raw_rows,
            "accepted_over_raw_ratio": None,
            "reasons": ["canonical_unavailable", *trunc_reasons, *soft_reasons],
            "status": "population_unknown",
        }
    pop = evaluate_org_population(
        accepted_stocks=int(accepted_stocks or 0),
        raw_stocks=raw_stocks,
    )
    pop = dict(pop)
    pop["raw_rows"] = raw_rows
    pop["provider_truncated"] = truncated
    pop["under_modern_baseline"] = soft_under
    if soft_reasons:
        pop["reasons"] = list(pop.get("reasons") or []) + soft_reasons
    if truncated:
        pop["under_populated"] = True
        pop["reasons"] = list(pop.get("reasons") or []) + trunc_reasons
    return pop


def max_accepted_stocks_across_partitions(conn: Any) -> int:
    """Max distinct stocks across any accepted org canonical partition."""
    from services.data_sources.org_holding_schema import CANONICAL_TABLE

    try:
        row = conn.execute(
            f"""
            SELECT COALESCE(MAX(n), 0) FROM (
              SELECT COUNT(DISTINCT stock_code) AS n
                FROM {CANONICAL_TABLE}
               GROUP BY available_date
            )
            """
        ).fetchone()
    except Exception:  # noqa: BLE001
        return 0
    return int(row[0] or 0) if row else 0


def decide_org_gap_action(
    *,
    accepted_has: bool,
    local_has: bool,
    population: dict[str, Any],
    accept_unlocked: bool = True,
    source_count: int | None = None,
    last_reconciled_count: int | None = None,
) -> tuple[str, str]:
    """Map existence+population → acquire action/status (no by-date invent).

    under_populated + dense local raw → repair_accept_from_local_raw (no provider).
    under_populated + thin local raw / truncated / source count ahead →
    merge_period (page-1 count then grain MERGE; never daily DELETE+full re-pull).
    Period ended but announcement join still empty → stamp first-seen and
    accept that calendar day (no future partition). Do not wait for the
    statutory completeness date; that clock is not known-at.
    """
    from services.pipeline.closed_loop import org_population_thresholds

    thr = org_population_thresholds()
    under = bool(population.get("under_populated"))
    truncated = bool(population.get("provider_truncated"))
    raw_n = int(population.get("raw_stocks") or 0)
    raw_rows = int(population.get("raw_rows") or 0)
    ahead = False
    if source_count is not None:
        ahead = source_count_ahead(
            local_rows=raw_rows,
            source_count=int(source_count),
            last_reconciled_count=last_reconciled_count,
        )
    if not accept_unlocked:
        if accepted_has:
            return "skip_current", "ok"
        if local_has:
            if ahead or truncated:
                return "merge_raw", "source_ahead_before_accept"
            return "skip_current", "pending_accept_clock"
        return "fetch_raw", "acquire_before_accept"
    if truncated and local_has:
        return "merge_period", "provider_truncated"
    if accepted_has and under:
        if local_has and raw_n >= thr["min_accepted_stocks"]:
            return "repair_accept_from_local_raw", "under_populated_accepted"
        if local_has:
            return "merge_period", "under_populated_raw_thin"
        return "fetch_then_accept", "under_populated_missing_raw"
    if accepted_has:
        if ahead:
            return "merge_period", "source_ahead"
        return "skip_current", "ok"
    if local_has:
        if ahead:
            return "merge_period", "source_ahead_unaccepted"
        return "accept_from_local_raw", "plannable_raw_unaccepted"
    return "fetch_then_accept", "plannable_missing"
