"""Org-holding population probes (existence ≠ population).

Authority: docs/MASTER_TOPLEVEL_DESIGN.md §5.8 (派生新鲜度闭环法)
Kept out of org_holding_aif10.py to avoid god-file ratchet growth.
"""
from __future__ import annotations

from typing import Any, Optional

from services.pipeline.closed_loop import evaluate_org_population


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
    """Distinct accepted stocks; None if canonical table unavailable."""
    from services.data_sources.org_holding_schema import CANONICAL_TABLE

    available = plannable_available_yyyymmdd(report_date)
    if not available:
        return None
    try:
        conn.execute(f"SELECT 1 FROM {CANONICAL_TABLE} LIMIT 0")
    except Exception:  # noqa: BLE001
        return None
    avail_iso = (
        f"{available[:4]}-{available[4:6]}-{available[6:8]}"
        if len(available) == 8
        else available
    )
    try:
        row = conn.execute(
            f"""
            SELECT COUNT(DISTINCT stock_code)
              FROM {CANONICAL_TABLE}
             WHERE available_date = ? OR available_date = ?
                OR report_date = ? OR report_date = ?
            """,
            [available, avail_iso, report_date, report_date.replace("-", "")],
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
) -> tuple[str, str]:
    """Map existence+population → acquire action/status (no by-date invent).

    under_populated + dense local raw → repair_accept_from_local_raw (no provider).
    under_populated + thin local raw → repair_fetch_period (one-period refresh only).
    """
    from services.pipeline.closed_loop import org_population_thresholds

    thr = org_population_thresholds()
    under = bool(population.get("under_populated"))
    truncated = bool(population.get("provider_truncated"))
    raw_n = int(population.get("raw_stocks") or 0)
    if truncated and local_has:
        return "repair_fetch_period", "provider_truncated"
    if accepted_has and under:
        if local_has and raw_n >= thr["min_accepted_stocks"]:
            return "repair_accept_from_local_raw", "under_populated_accepted"
        if local_has:
            return "repair_fetch_period", "under_populated_raw_thin"
        return "fetch_then_accept", "under_populated_missing_raw"
    if accepted_has:
        return "skip_current", "ok"
    if local_has:
        return "accept_from_local_raw", "plannable_raw_unaccepted"
    return "fetch_then_accept", "plannable_missing"
