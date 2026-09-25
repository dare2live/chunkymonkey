"""Pagination integrity helpers (East Money 100-page cap class)."""
from __future__ import annotations

import pytest

from services.data_sources.pagination_integrity import (
    detect_eastmoney_page_cap_land,
    provider_truncated_heuristic,
    under_modern_baseline_stocks,
)


def test_provider_truncated_heuristic_has_no_provider_count_kwarg():
    """2026-09-25 返修 (blocking finding): ``assess_paginated_land`` 与
    ``provider_truncated_heuristic`` 的 ``provider_count`` 分支一起删除 ——
    唯一生产调用方 ``org_holding_population.py:202`` 从不传 ``provider_count``
    (spec_holders_pagination.md §3.5)。这条钉住"参数真的不在了", 不只是
    "分支走不到"。"""
    with pytest.raises(TypeError):
        provider_truncated_heuristic(  # type: ignore[call-arg]
            landed_rows=200_000,
            landed_stocks=1200,
            baseline_stocks=5520,
            page_size=2000,
            provider_count=832_906,
        )


def test_detect_page_cap_land_signature():
    assert detect_eastmoney_page_cap_land(200_000, page_size=2000) is True
    assert detect_eastmoney_page_cap_land(832_000, page_size=2000) is False


def test_provider_truncated_heuristic_page_cap_hard():
    truncated, reasons = provider_truncated_heuristic(
        landed_rows=200_000,
        landed_stocks=1200,
        baseline_stocks=5520,
        page_size=2000,
    )
    assert truncated is True
    assert any("100*page_size" in r for r in reasons)


def test_under_modern_baseline_is_soft_not_hard_trunc():
    soft, soft_reasons = under_modern_baseline_stocks(
        landed_stocks=3607,
        baseline_stocks=5562,
    )
    assert soft is True
    assert soft_reasons
    hard, hard_reasons = provider_truncated_heuristic(
        landed_rows=54_895,
        landed_stocks=3607,
        baseline_stocks=5562,
        page_size=2000,
    )
    assert hard is False
    assert hard_reasons == []


def test_legacy_include_baseline_ratio_opt_in():
    truncated, reasons = provider_truncated_heuristic(
        landed_rows=54_895,
        landed_stocks=3607,
        baseline_stocks=5562,
        page_size=2000,
        include_baseline_ratio=True,
    )
    assert truncated is True
    assert any("baseline" in r for r in reasons)
