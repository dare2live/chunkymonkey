"""Read helpers for the current qfq analysis/serving surface in market.duckdb."""
from __future__ import annotations

from typing import Iterable, Optional

from services.market_schema import ANALYSIS_KLINE_QFQ_VIEW_DDL
from services.source_policy import get_capability_policy


KLINE_DAILY_QFQ_POLICY = get_capability_policy("kline_daily")
ANALYSIS_KLINE_QFQ_RELATION = KLINE_DAILY_QFQ_POLICY.analysis_relation or "market.v_price_kline_qfq"
DEFAULT_KLINE_DAILY_QFQ_COLUMNS = (
    "code", "date", "open", "high", "low", "close", "volume", "amount", "factor",
)


def get_analysis_kline_qfq_relation(schema: Optional[str] = None) -> str:
    """Resolve the current daily qfq analysis relation for a connection."""
    name = ANALYSIS_KLINE_QFQ_RELATION.rsplit(".", 1)[-1]
    return f"{schema}.{name}" if schema else name


def analysis_kline_daily_qfq_sql(
    *,
    relation: str | None = None,
    columns: Iterable[str] = DEFAULT_KLINE_DAILY_QFQ_COLUMNS,
    include_source_lineage: bool = False,
) -> str:
    """Return the qfq SELECT used by analytical jobs; never an execution-price contract."""
    relation = relation or ANALYSIS_KLINE_QFQ_RELATION
    allowed = {
        "code", "date", "open", "high", "low", "close", "volume", "amount", "factor",
        "freq", "adjust",
    }
    selected = []
    for column in columns:
        if column not in allowed:
            raise ValueError(f"unsupported qfq analysis column: {column}")
        selected.append(column)
    if include_source_lineage:
        selected.extend([
            "COALESCE(source_name, 'unknown') AS source_name",
            "COALESCE(source_tier, 99)::SMALLINT AS source_tier",
            "COALESCE(is_fallback, FALSE) AS is_fallback",
        ])
    select_sql = ", ".join(selected)
    return (
        f"SELECT {select_sql}\n"
        f"FROM {relation}\n"
        "WHERE freq='daily' AND adjust='qfq'"
    )


__all__ = [
    "ANALYSIS_KLINE_QFQ_RELATION",
    "ANALYSIS_KLINE_QFQ_VIEW_DDL",
    "DEFAULT_KLINE_DAILY_QFQ_COLUMNS",
    "KLINE_DAILY_QFQ_POLICY",
    "analysis_kline_daily_qfq_sql",
    "get_analysis_kline_qfq_relation",
]
