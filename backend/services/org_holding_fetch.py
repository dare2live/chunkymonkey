"""Org holding aif10 provider fetch (sharded pagination). Split from org_holding_aif10 ratchet."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_MIAOXIANG = Path(__file__).resolve().parents[2] / "miaoxiang"
if str(_MIAOXIANG) not in sys.path:
    sys.path.insert(0, str(_MIAOXIANG))

REPORT_NAME = "RPT_MAIN_ORGHOLDDETAIL"
PAGE_SIZE = 2000
EASTMONEY_MAX_PAGES = 100
FETCH_RETRY = 5
FETCH_TIMEOUT = 60

_CLIENT = None


def _robust_client():
    global _CLIENT
    if _CLIENT is None:
        from aif10_scraper.client import AIF10Client

        _CLIENT = AIF10Client(retry=FETCH_RETRY, timeout=FETCH_TIMEOUT)
    return _CLIENT


def fetch_period(report_date_iso: str) -> dict[str, Any]:
    """Sharded aif10 pull for one report period (100-page cap safe)."""
    from aif10_scraper import fetch_all_pages_sharded

    out = fetch_all_pages_sharded(
        report_name=REPORT_NAME,
        page_size=PAGE_SIZE,
        max_pages=0,
        sort_columns="REPORT_DATE,SECURITY_CODE",
        sort_types="-1,1",
        extra_filters=[f"(REPORT_DATE='{report_date_iso}')"],
        client=_robust_client(),
        max_pages_per_query=EASTMONEY_MAX_PAGES,
    )
    return {
        "rows": out.get("rows") or [],
        "provider_count": int(out.get("provider_count") or 0),
        "fetched_rows": int(out.get("fetched_rows") or 0),
        "truncated": bool(out.get("truncated")),
        "shard_count": int(out.get("shard_count") or 0),
        "land_reasons": list(out.get("land_reasons") or []),
    }
