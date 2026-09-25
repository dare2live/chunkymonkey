"""Daily-update pagination integrity (East Money v1 100-page cap class).

org_holding_population.population_for_period is the only production caller
(via provider_truncated_heuristic / under_modern_baseline_stocks). 2026-09-25
刀 A: sources/miaoxiang.py 已改走 aif10_scraper.pagination.fetch_pages_strict
严格引擎，不再调用这里；`assess_paginated_land`（连带它的
``row_tolerance_ratio=0.002``/``row_tolerance_min=500`` 缺省容差字面量与
``provider_count`` 分支）已删除——它唯一的调用路径是
``provider_truncated_heuristic`` 里 org 从不触发的 ``provider_count`` 分支
（org_holding_population.py:202 从不传 ``provider_count``），删除即消灭最后一处
判据字面量而不改变任何生产行为（spec_holders_pagination.md §3.5）。
"""
from __future__ import annotations

# Live probe 2026-07-24 RPT_MAIN_ORGHOLDDETAIL: page 101 → pages=0.
EASTMONEY_V1_MAX_PAGES_PER_QUERY = 100


def detect_eastmoney_page_cap_land(landed_rows: int, *, page_size: int) -> bool:
    """Heuristic: land stopped near 100×page_size (silent API truncation)."""
    cap = EASTMONEY_V1_MAX_PAGES_PER_QUERY * page_size
    return cap * 0.97 <= int(landed_rows or 0) <= cap * 1.01


def under_modern_baseline_stocks(
    *,
    landed_stocks: int,
    baseline_stocks: int,
    baseline_ratio: float = 0.95,
) -> tuple[bool, list[str]]:
    """Soft observe: stocks ≪ modern max (older thinner universes often trip this).

    NOT a repair trigger — live canary 2019-03-31 re-fetch proved
    provider_count==landed with truncated=false while still under modern baseline.
    """
    base = int(baseline_stocks or 0)
    if base <= 0:
        return False, []
    if int(landed_stocks or 0) < int(base * baseline_ratio):
        return True, [
            f"landed_stocks={landed_stocks}<{baseline_ratio:.2f}*baseline={base}"
        ]
    return False, []


def provider_truncated_heuristic(
    *,
    landed_rows: int,
    landed_stocks: int,
    baseline_stocks: int,
    page_size: int,
    baseline_ratio: float = 0.95,
    include_baseline_ratio: bool = False,
) -> tuple[bool, list[str]]:
    """Hard truncation: page-cap land signature only (no ``provider_count`` path).

    ``baseline_stocks`` ratio is soft by default (``include_baseline_ratio=False``).
    Passing True retains legacy combined verdict for callers that opt in. The
    ``provider_count`` branch (and ``assess_paginated_land``) was removed
    2026-09-25 刀 A: org_holding_population.py 从不传 ``provider_count``,
    删除只是让判据不再留一个死分支 (spec §3.5)。
    """
    reasons: list[str] = []
    truncated = False
    if detect_eastmoney_page_cap_land(landed_rows, page_size=page_size):
        truncated = True
        reasons.append("landed_rows≈100*page_size without provider_count")
    if include_baseline_ratio:
        soft, soft_reasons = under_modern_baseline_stocks(
            landed_stocks=landed_stocks,
            baseline_stocks=baseline_stocks,
            baseline_ratio=baseline_ratio,
        )
        if soft:
            truncated = True
            reasons.extend(soft_reasons)
    return truncated, reasons
