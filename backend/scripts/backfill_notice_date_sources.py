"""Backfill notice-date source lineage for institution holdings/events."""

from __future__ import annotations

import json

from services.db import get_conn, init_db
from services.holder_availability import (
    backfill_future_holder_period_page_update_availability,
    backfill_institution_event_notice_sources,
    backfill_inst_holdings_notice_dates,
)
from services.return_engine import calculate_returns
from services.schema_versions import record_actual_version


def main() -> None:
    init_db()
    with get_conn() as conn:
        page_update = backfill_future_holder_period_page_update_availability(conn)
        holdings = backfill_inst_holdings_notice_dates(conn)
        events = backfill_institution_event_notice_sources(conn)
        recalculated_returns = calculate_returns(conn)
        record_actual_version(conn, "fact_institution_event")
    print(
        json.dumps(
            {
                "holder_page_update": page_update,
                "inst_holdings": holdings,
                "fact_institution_event": events,
                "recalculated_returns": recalculated_returns,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
