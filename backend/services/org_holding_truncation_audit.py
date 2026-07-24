"""List and repair org periods truncated by East Money 100-page cap.

Ops-only: ``sync_period(..., allow_existing_refresh=True)`` one period per
iteration, oldest truncated first, session cap ≤40. NOT wired to daily_update.
"""
from __future__ import annotations

from typing import Any

from services.org_holding_aif10 import DEFAULT_START_PERIOD, enumerate_quarter_ends
from services.org_holding_population import (
    count_raw_org_rows,
    count_raw_org_stocks,
    max_accepted_stocks_across_partitions,
    population_for_period,
)


def list_truncated_org_periods(
    conn: Any,
    *,
    start_period: str = DEFAULT_START_PERIOD,
    end_period: str | None = None,
) -> list[dict[str, Any]]:
    """Periods with local raw flagged provider_truncated (existence ≠ complete)."""
    from services.org_holding_aif10 import latest_plannable_report_date

    end = end_period or latest_plannable_report_date()
    if not end:
        return []
    periods = enumerate_quarter_ends(start_period, end)
    baseline = max_accepted_stocks_across_partitions(conn)
    out: list[dict[str, Any]] = []
    for period in periods:
        rows = count_raw_org_rows(conn, period)
        if rows <= 0:
            continue
        stocks = count_raw_org_stocks(conn, period)
        pop = population_for_period(
            conn,
            report_date=period,
            local_has=True,
            accepted_has=True,
        )
        if not pop.get("provider_truncated"):
            continue
        out.append(
            {
                "report_date": period,
                "raw_rows": rows,
                "raw_stocks": stocks,
                "baseline_stocks": baseline,
                "reasons": list(pop.get("reasons") or []),
            }
        )
    return out
