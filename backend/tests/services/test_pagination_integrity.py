"""Pagination integrity helpers (East Money 100-page cap class)."""
from __future__ import annotations

from services.data_sources.pagination_integrity import (
    assess_paginated_land,
    detect_eastmoney_page_cap_land,
    provider_truncated_heuristic,
)


def test_detect_page_cap_land_signature():
    assert detect_eastmoney_page_cap_land(200_000, page_size=2000) is True
    assert detect_eastmoney_page_cap_land(832_000, page_size=2000) is False


def test_assess_paginated_land_below_count():
    v = assess_paginated_land(
        expected_count=832_906,
        landed_rows=200_000,
        page_size=2000,
    )
    assert v.truncated is True


def test_provider_truncated_heuristic_baseline_stocks():
    truncated, reasons = provider_truncated_heuristic(
        landed_rows=200_000,
        landed_stocks=1200,
        baseline_stocks=5520,
        page_size=2000,
    )
    assert truncated is True
    assert reasons
