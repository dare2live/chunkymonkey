"""Read helpers and canonical K-line SQL for market.duckdb."""
from __future__ import annotations

from typing import Iterable, Optional

from services.market_schema import CANONICAL_KLINE_QFQ_VIEW_DDL
from services.source_policy import get_capability_policy


KLINE_DAILY_QFQ_POLICY = get_capability_policy("kline_daily")
CANONICAL_KLINE_QFQ_RELATION = KLINE_DAILY_QFQ_POLICY.canonical_relation or "market.v_price_kline_qfq"
DEFAULT_KLINE_DAILY_QFQ_COLUMNS = (
    "code", "date", "open", "high", "low", "close", "volume", "amount", "factor",
)


def get_canonical_kline_qfq_relation(schema: Optional[str] = None) -> str:
    """Resolve the canonical daily qfq K-line relation for a connection."""
    name = CANONICAL_KLINE_QFQ_RELATION.rsplit(".", 1)[-1]
    return f"{schema}.{name}" if schema else name


def canonical_kline_daily_qfq_sql(
    *,
    relation: str | None = None,
    columns: Iterable[str] = DEFAULT_KLINE_DAILY_QFQ_COLUMNS,
    include_source_lineage: bool = False,
) -> str:
    """Return the canonical daily qfq K-line SELECT used by analytical jobs."""
    relation = relation or CANONICAL_KLINE_QFQ_RELATION
    allowed = {
        "code", "date", "open", "high", "low", "close", "volume", "amount", "factor",
        "freq", "adjust",
    }
    selected = []
    for column in columns:
        if column not in allowed:
            raise ValueError(f"unsupported canonical kline column: {column}")
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


_PRICE_FIELDS = {"open", "high", "low", "close", "volume", "amount", "factor"}


def _quote_price_field(field: str) -> str:
    if field not in _PRICE_FIELDS:
        raise ValueError(f"unsupported price field: {field}")
    return f'"{field}"'


def _relation_has_column(conn, relation: str, column: str) -> bool:
    # 2026-06-22 P0-9: relation 不可访问 (真相源缺失) 必须 raise — 不吞掉返 False, 否则
    # 复权因子真相源不可访问时 get_kline_range 静默退化 factor=1.0(不复权) → scoring/return_engine
    # 错价 (真金白银). 仅 "DESCRIBE 成功但确无该列" 才是合法 False。
    rows = conn.execute(f"DESCRIBE {relation}").fetchall()   # relation 不可访问→抛, 非吞
    for row in rows:
        try:
            name = row["column_name"]
        except Exception:
            name = row[0]
        if str(name).lower() == column.lower():
            return True
    return False


def get_kline(conn, code: str, date: str, freq: str = "daily", field: str = "open") -> Optional[float]:
    """单点价格查询：取指定日期的指定字段值"""
    col = _quote_price_field(field)
    relation = get_canonical_kline_qfq_relation() if freq == "daily" else "price_kline"
    row = conn.execute(
        f"SELECT {col} FROM {relation} "
        "WHERE code=? AND date=? AND freq=? AND adjust='qfq'",
        (code, date, freq),
    ).fetchone()
    if row:
        return row[0]
    if freq == "daily":
        row = conn.execute(
            "SELECT \"close\" FROM price_kline "
            "WHERE code=? AND date<=? AND freq='monthly' AND adjust='qfq' "
            "ORDER BY date DESC LIMIT 1",
            (code, date),
        ).fetchone()
        return row[0] if row else None
    return None


def get_kline_range(conn, code: str, start: str, end: str, freq: str = "daily") -> list[dict]:
    """区间查询：返回 [{date, open, high, low, close, volume, amount, factor}]"""
    relation = get_canonical_kline_qfq_relation() if freq == "daily" else "price_kline"
    has_factor = freq == "daily" and _relation_has_column(conn, relation, "factor")
    factor_expr = "COALESCE(factor, 1.0) AS factor" if has_factor else "1.0 AS factor"
    rows = conn.execute(
        f"SELECT date, open, high, low, close, volume, amount, {factor_expr} "
        f"FROM {relation} "
        "WHERE code=? AND freq=? AND adjust='qfq' AND date>=? AND date<=? "
        "ORDER BY date",
        (code, freq, start, end),
    ).fetchall()
    return [dict(r) for r in rows]


def get_xdxr_events(conn, code: str, start: Optional[str] = None, end: Optional[str] = None) -> list[dict]:
    """查询某只股票的除权除息 / 股本变动事件。"""
    where = ["code=?"]
    params: list = [code]
    if start:
        where.append("date>=?")
        params.append(start)
    if end:
        where.append("date<=?")
        params.append(end)

    rows = conn.execute(
        "SELECT code, date, category, name, fenhong, peigujia, songzhuangu, "
        " peigu, suogu, panqianliutong, panhouliutong, qianzongguben, "
        " houzongguben, fenshu, xingquanjia, source "
        f"FROM price_xdxr WHERE {' AND '.join(where)} "
        "ORDER BY date, category",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_sync_states(conn, freq: str = "daily") -> list[dict]:
    """查询所有股票的同步状态"""
    rows = conn.execute(
        "SELECT * FROM market_sync_state "
        "WHERE dataset='price_kline' AND freq=? AND adjust='qfq'",
        (freq,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_xdxr_sync_states(conn) -> list[dict]:
    """查询所有股票的 xdxr 同步状态。"""
    rows = conn.execute(
        "SELECT * FROM market_sync_state "
        "WHERE dataset='price_xdxr' AND freq='event' AND adjust='none'"
    ).fetchall()
    return [dict(r) for r in rows]


__all__ = [
    "CANONICAL_KLINE_QFQ_RELATION",
    "CANONICAL_KLINE_QFQ_VIEW_DDL",
    "DEFAULT_KLINE_DAILY_QFQ_COLUMNS",
    "KLINE_DAILY_QFQ_POLICY",
    "canonical_kline_daily_qfq_sql",
    "get_all_sync_states",
    "get_all_xdxr_sync_states",
    "get_canonical_kline_qfq_relation",
    "get_kline",
    "get_kline_range",
    "get_xdxr_events",
]
