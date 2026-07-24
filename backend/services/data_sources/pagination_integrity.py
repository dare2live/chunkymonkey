"""Daily-update pagination integrity (East Money v1 100-page cap class).

Shared typed verdict for paginated lands — org wired first; other aif10/sync
surfaces should pass page-1 provider_count + landed_rows through here.
"""
from __future__ import annotations

from dataclasses import dataclass

# Live probe 2026-07-24 RPT_MAIN_ORGHOLDDETAIL: page 101 → pages=0.
EASTMONEY_V1_MAX_PAGES_PER_QUERY = 100


@dataclass(frozen=True)
class PaginationIntegrityVerdict:
    expected_count: int
    landed_rows: int
    page_size: int
    max_pages_per_query: int
    truncated: bool
    reasons: tuple[str, ...]

    @property
    def max_rows_per_query(self) -> int:
        return self.max_pages_per_query * self.page_size


def assess_paginated_land(
    *,
    expected_count: int,
    landed_rows: int,
    page_size: int,
    max_pages_per_query: int = EASTMONEY_V1_MAX_PAGES_PER_QUERY,
    row_tolerance_ratio: float = 0.002,
    row_tolerance_min: int = 500,
) -> PaginationIntegrityVerdict:
    """Fail-closed when landed mass is below provider page-1 count."""
    reasons: list[str] = []
    truncated = False
    exp = int(expected_count or 0)
    landed = int(landed_rows or 0)
    cap_rows = max_pages_per_query * page_size
    if exp > 0 and landed < exp:
        tol = max(row_tolerance_min, int(exp * row_tolerance_ratio))
        if landed + tol < exp:
            truncated = True
            reasons.append(
                f"landed_rows={landed}<provider_count={exp}-tol={tol}"
            )
    if (
        not truncated
        and exp > cap_rows
        and cap_rows * 0.97 <= landed <= cap_rows * 1.01
    ):
        truncated = True
        reasons.append(
            f"eastmoney_page_cap_signature landed≈{cap_rows} count={exp}"
        )
    return PaginationIntegrityVerdict(
        expected_count=exp,
        landed_rows=landed,
        page_size=page_size,
        max_pages_per_query=max_pages_per_query,
        truncated=truncated,
        reasons=tuple(reasons),
    )


def detect_eastmoney_page_cap_land(landed_rows: int, *, page_size: int) -> bool:
    """Heuristic: land stopped near 100×page_size (silent API truncation)."""
    cap = EASTMONEY_V1_MAX_PAGES_PER_QUERY * page_size
    return cap * 0.97 <= int(landed_rows or 0) <= cap * 1.01


def provider_truncated_heuristic(
    *,
    landed_rows: int,
    landed_stocks: int,
    baseline_stocks: int,
    page_size: int,
    provider_count: int | None = None,
    baseline_ratio: float = 0.95,
) -> tuple[bool, list[str]]:
    """Gap-time detection without live API (DB-only) + optional provider_count."""
    reasons: list[str] = []
    truncated = False
    if provider_count is not None and provider_count > 0:
        verdict = assess_paginated_land(
            expected_count=provider_count,
            landed_rows=landed_rows,
            page_size=page_size,
        )
        if verdict.truncated:
            truncated = True
            reasons.extend(verdict.reasons)
    elif detect_eastmoney_page_cap_land(landed_rows, page_size=page_size):
        truncated = True
        reasons.append("landed_rows≈100*page_size without provider_count")
    base = int(baseline_stocks or 0)
    if base > 0 and int(landed_stocks or 0) < int(base * baseline_ratio):
        truncated = True
        reasons.append(
            f"landed_stocks={landed_stocks}<{baseline_ratio:.2f}*baseline={base}"
        )
    return truncated, reasons
