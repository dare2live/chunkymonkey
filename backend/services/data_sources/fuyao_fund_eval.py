"""Evaluate Fuyao fund REST (28 GET paths) as 跟随策略「资金方侧」discovery.

This knife records per-path status. It does not invent landing tables or
register TuShare domains. Endpoints that return 0 rows / not_ready /
product_mismatch stay evaluation-only.
"""
from __future__ import annotations

from typing import Any, Mapping

from services.data_sources.sources.fuyao import classify_fuyao_failure

# Official list: fuyao/skills/hithink-finance/references/api/endpoints-fund.md
FUND_ENDPOINT_PATHS: tuple[str, ...] = (
    "/api/fund/profile/detail",
    "/api/fund/portfolio/holdings",
    "/api/fund/performance/nav",
    "/api/fund/performance/returns",
    "/api/fund/holders/detail",
    "/api/fund/market/snapshot",
    "/api/fund/market/historical",
    "/api/fund/companies/detail",
    "/api/fund/portfolio/industry-allocation",
    "/api/fund/performance/indicators-historical",
    "/api/fund/performance/drawdowns",
    "/api/fund/holders/top",
    "/api/fund/corporate-actions/dividends",
    "/api/fund/diagnostics/detail",
    "/api/fund/financials/indicators",
    "/api/fund/financials/income-statements",
    "/api/fund/financials/balance-sheets",
    "/api/fund/managers/investment-style",
    "/api/fund/managers/performance",
    "/api/fund/managers/experience",
    "/api/fund/managers/detail",
    "/api/fund/news/article-list",
    "/api/fund/offerings/list",
    "/api/fund/portfolio/stock-history",
    "/api/fund/portfolio/stock-report-dates",
    "/api/fund/portfolio/bond-history",
    "/api/fund/portfolio/bond-report-dates",
    "/api/fund/portfolio/asset-allocation",
)

# No landing tables this knife: evaluation only, even when a path returned rows.
FUND_LANDING_TABLES: dict[str, str] = {}


def item_count(payload: Any) -> int | None:
    if payload is None:
        return None
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, Mapping):
        items = payload.get("item")
        if isinstance(items, list):
            return len(items)
    return None


def evaluate_fund_result(
    path: str,
    *,
    payload: Any = None,
    error: BaseException | None = None,
) -> dict[str, Any]:
    """Classify one fund GET. Does not recommend a landing table."""
    if path not in FUND_ENDPOINT_PATHS:
        raise KeyError(f"not a documented fund-28 path: {path}")
    if error is not None:
        status = classify_fuyao_failure(error)
        return {
            "path": path,
            "status": status,
            "n_item": None,
            "land": False,
            "reason": str(error)[:240],
        }
    n = item_count(payload)
    if payload is None:
        status = "empty"
    elif n == 0:
        status = "zero_rows"
    else:
        status = "ok"
    return {
        "path": path,
        "status": status,
        "n_item": n,
        "land": False,
        "reason": "evaluation_only_no_landing_table",
    }


def fund_eval_catalog() -> tuple[dict[str, Any], ...]:
    return tuple({"path": p, "land": False} for p in FUND_ENDPOINT_PATHS)


__all__ = [
    "FUND_ENDPOINT_PATHS",
    "FUND_LANDING_TABLES",
    "evaluate_fund_result",
    "fund_eval_catalog",
    "item_count",
]
