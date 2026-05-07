"""Backfill notice-date source lineage for institution holdings/events."""

from __future__ import annotations

import json

from services.db import get_conn, init_db
from services.holder_availability import (
    backfill_institution_event_notice_sources,
    backfill_inst_holdings_notice_dates,
)
from services.schema_versions import record_actual_version


def main() -> None:
    init_db()
    with get_conn() as conn:
        holdings = backfill_inst_holdings_notice_dates(conn)
        events = backfill_institution_event_notice_sources(conn)
        record_actual_version(conn, "fact_institution_event")
    print(json.dumps({"inst_holdings": holdings, "fact_institution_event": events}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
