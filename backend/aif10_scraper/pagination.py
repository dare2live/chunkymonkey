"""East Money v1 pagination integrity (100-page cap per filter query).

RPT_MAIN_ORGHOLDDETAIL and peers return pages=0 for page>100 while count stays high.
Shard by SECURITY_CODE numeric range until each probe fits max_pages.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Sequence

logger = logging.getLogger("aif10_scraper")

# Live probe 2026-07-24: page 101+ → pages=0 for same filter.
DEFAULT_MAX_PAGES_PER_QUERY = 100


@dataclass(frozen=True)
class PaginationProbe:
    provider_count: int
    provider_pages: int
    page_size: int
    max_pages_per_query: int

    @property
    def max_rows_per_query(self) -> int:
        return self.max_pages_per_query * self.page_size

    @property
    def exceeds_page_cap(self) -> bool:
        if self.provider_pages > self.max_pages_per_query:
            return True
        if self.provider_count > self.max_rows_per_query:
            return True
        return False


@dataclass(frozen=True)
class PaginationLandResult:
    expected_count: int
    landed_rows: int
    truncated: bool
    reasons: tuple[str, ...]


def security_code_range_filter(lo: int, hi: int) -> str:
    """Inclusive 6-digit SECURITY_CODE numeric range (no quotes — API accepts)."""
    return f"(SECURITY_CODE>={lo:06d})(SECURITY_CODE<={hi:06d})"


def assess_pagination_land(
    *,
    expected_count: int,
    landed_rows: int,
    max_pages_per_query: int = DEFAULT_MAX_PAGES_PER_QUERY,
    page_size: int,
    row_tolerance_ratio: float = 0.002,
    row_tolerance_min: int = 500,
) -> PaginationLandResult:
    """Typed truncation verdict after a paginated land."""
    reasons: list[str] = []
    truncated = False
    cap_rows = max_pages_per_query * page_size
    if expected_count > 0 and landed_rows < expected_count:
        tol = max(row_tolerance_min, int(expected_count * row_tolerance_ratio))
        if landed_rows + tol < expected_count:
            truncated = True
            reasons.append(
                f"landed_rows={landed_rows}<provider_count={expected_count}-tol={tol}"
            )
    if (
        not truncated
        and expected_count > cap_rows
        and landed_rows <= cap_rows
        and landed_rows >= cap_rows - max(row_tolerance_min, int(cap_rows * 0.01))
    ):
        truncated = True
        reasons.append(
            f"suspicious_page_cap_land landed≈{cap_rows} count={expected_count}"
        )
    return PaginationLandResult(
        expected_count=int(expected_count or 0),
        landed_rows=int(landed_rows or 0),
        truncated=truncated,
        reasons=tuple(reasons),
    )


def _probe_v1(
    client: Any,
    report_name: str,
    *,
    page_size: int,
    sort_columns: str,
    sort_types: str,
    columns: str,
    secucode: str | None,
    extra_filters: list[str] | None,
    extra_params: dict[str, Any] | None,
    max_pages_per_query: int,
) -> PaginationProbe:
    head = client.get_v1(
        report_name,
        page=1,
        page_size=page_size,
        sort_columns=sort_columns,
        sort_types=sort_types,
        columns=columns,
        secucode=secucode,
        extra_filters=extra_filters,
        extra_params=extra_params,
    )
    return PaginationProbe(
        provider_count=int(head.get("count") or 0),
        provider_pages=int(head.get("pages") or 0),
        page_size=page_size,
        max_pages_per_query=max_pages_per_query,
    )


def plan_security_code_shards(
    client: Any,
    report_name: str,
    *,
    base_filters: Sequence[str],
    page_size: int,
    max_pages_per_query: int = DEFAULT_MAX_PAGES_PER_QUERY,
    sort_columns: str = "",
    sort_types: str = "",
    columns: str = "ALL",
    secucode: str | None = None,
    extra_params: dict[str, Any] | None = None,
    lo: int = 0,
    hi: int = 999_999,
) -> list[list[str]]:
    """Return extra_filters lists (base + range) each fitting max_pages."""
    filters = list(base_filters)
    probe = _probe_v1(
        client,
        report_name,
        page_size=page_size,
        sort_columns=sort_columns,
        sort_types=sort_types,
        columns=columns,
        secucode=secucode,
        extra_filters=filters if filters else None,
        extra_params=extra_params,
        max_pages_per_query=max_pages_per_query,
    )
    if probe.provider_count <= 0:
        return [list(filters)]
    if not probe.exceeds_page_cap:
        return [list(filters)]

    if lo >= hi:
        raise RuntimeError(
            f"cannot shard SECURITY_CODE range {lo}-{hi}: "
            f"count={probe.provider_count} pages={probe.provider_pages}"
        )
    mid = (lo + hi) // 2
    left = plan_security_code_shards(
        client,
        report_name,
        base_filters=[*filters, security_code_range_filter(lo, mid)],
        page_size=page_size,
        max_pages_per_query=max_pages_per_query,
        sort_columns=sort_columns,
        sort_types=sort_types,
        columns=columns,
        secucode=secucode,
        extra_params=extra_params,
        lo=lo,
        hi=mid,
    )
    right = plan_security_code_shards(
        client,
        report_name,
        base_filters=[*filters, security_code_range_filter(mid + 1, hi)],
        page_size=page_size,
        max_pages_per_query=max_pages_per_query,
        sort_columns=sort_columns,
        sort_types=sort_types,
        columns=columns,
        secucode=secucode,
        extra_params=extra_params,
        lo=mid + 1,
        hi=hi,
    )
    return left + right


def fetch_pages_for_filters(
    client: Any,
    report_name: str,
    *,
    page_size: int,
    max_pages: int,
    sort_columns: str,
    sort_types: str,
    columns: str,
    secucode: str | None,
    extra_filters: list[str] | None,
    extra_params: dict[str, Any] | None,
    progress_callback: Callable[[int, int, int], None] | None,
    max_pages_per_query: int = DEFAULT_MAX_PAGES_PER_QUERY,
) -> tuple[list[dict], PaginationLandResult]:
    """Fetch one filter query with truncate-aware loop."""
    head = client.get_v1(
        report_name,
        page=1,
        page_size=page_size,
        sort_columns=sort_columns,
        sort_types=sort_types,
        columns=columns,
        secucode=secucode,
        extra_filters=extra_filters,
        extra_params=extra_params,
    )
    expected = int(head.get("count") or 0)
    rows: list[dict] = list(head.get("data") or [])
    total_pages = int(head.get("pages") or 0)
    if progress_callback:
        try:
            progress_callback(1, total_pages, len(rows))
        except Exception:  # noqa: BLE001 — 进度回调是显示层, 挂了不该中断取数
            # 并入本仓时改: 原上游是 pass。吞掉不记 = 回调坏了没人知道。
            logger.warning("progress_callback 抛异常, 已忽略(不影响已取到的行)", exc_info=True)
    page = 2
    while page <= total_pages:
        if max_pages and page > max_pages:
            break
        result = client.get_v1(
            report_name,
            page=page,
            page_size=page_size,
            sort_columns=sort_columns,
            sort_types=sort_types,
            columns=columns,
            secucode=secucode,
            extra_filters=extra_filters,
            extra_params=extra_params,
        )
        batch = list(result.get("data") or [])
        api_pages = int(result.get("pages") or 0)
        if not batch and api_pages == 0 and expected > len(rows):
            land = assess_pagination_land(
                expected_count=expected,
                landed_rows=len(rows),
                max_pages_per_query=max_pages_per_query,
                page_size=page_size,
            )
            land = PaginationLandResult(
                expected_count=expected,
                landed_rows=len(rows),
                truncated=True,
                reasons=land.reasons + ("provider_pages_zero_mid_fetch",),
            )
            return rows, land
        rows.extend(batch)
        if progress_callback:
            try:
                progress_callback(page, total_pages, len(rows))
            except Exception:  # noqa: BLE001 — 进度回调是显示层, 挂了不该中断取数
                # 并入本仓时改: 原上游是 pass。吞掉不记 = 回调坏了没人知道。
                logger.warning("progress_callback 抛异常, 已忽略(不影响已取到的行)", exc_info=True)
        if api_pages <= page:
            break
        page += 1
    land = assess_pagination_land(
        expected_count=expected,
        landed_rows=len(rows),
        max_pages_per_query=max_pages_per_query,
        page_size=page_size,
    )
    return rows, land
