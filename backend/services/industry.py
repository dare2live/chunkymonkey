"""行业解析单点实现。

数据源 dim_stock_sw_industry 由 services/sw_industry_client.py 落库，
申万官方三级分类，L3 覆盖率 100%。

用法：
- 物化表构建 → load_industry_map()
- 页面/服务层 → resolve_industry()
- SQL 拼接 → industry_join_clause / industry_select_clause / industry_complete_condition
"""

from typing import Optional
import logging

logger = logging.getLogger(__name__)

_industry_cache: dict = None

INDUSTRY_LEVEL_COLUMNS = ("sw_l1", "sw_l2", "sw_l3")
INDUSTRY_NAME_COLUMNS = ("sw_l1_name", "sw_l2_name", "sw_l3_name")
INDUSTRY_TABLE = "dim_stock_sw_industry"


def _validate_industry_level(level: int) -> int:
    if level not in (1, 2, 3):
        raise ValueError(f"Unsupported industry level: {level}")
    return level


def _table_columns(conn, table_name: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except Exception:
        return set()
    result = set()
    for row in rows:
        if hasattr(row, "keys"):
            result.add(row["name"])
        else:
            result.add(row[1])
    return result


def industry_level_db_column(level: int) -> str:
    _validate_industry_level(level)
    return f"sw_l{level}"


def industry_level_alias(level: int) -> str:
    return f"sw_l{_validate_industry_level(level)}"


def industry_name_db_column(level: int) -> str:
    _validate_industry_level(level)
    return f"sw_l{level}_name"


def industry_name_alias(level: int) -> str:
    return f"sw_l{_validate_industry_level(level)}_name"


def industry_level_expr(level: int, *, alias: str = "industry_dim") -> str:
    return f"{alias}.{industry_level_db_column(level)}"


def industry_level_select(
    level: int,
    *,
    alias: str = "industry_dim",
    result_alias: Optional[str] = None,
) -> str:
    expr = industry_level_expr(level, alias=alias)
    return f"{expr} AS {result_alias}" if result_alias else expr


def industry_level_nonempty_condition(
    level: int, *, alias: str = "industry_dim"
) -> str:
    expr = industry_level_expr(level, alias=alias)
    return f"{expr} IS NOT NULL AND {expr} != ''"


def industry_level_value(industry: Optional[dict], level: int) -> str:
    if not industry:
        return ""
    return industry.get(industry_level_alias(level)) or ""


def with_industry_aliases(industry: Optional[dict]) -> dict:
    result = dict(industry or {})
    for level in (1, 2, 3):
        key = industry_level_alias(level)
        result.setdefault(key, result.get(key))
    return result


def attach_industry_aliases(target: dict, industry: Optional[dict]) -> dict:
    normalized = with_industry_aliases(industry)
    for level in (1, 2, 3):
        key = industry_level_alias(level)
        target[key] = normalized.get(key)
    return target


def industry_join_clause(
    stock_expr: str, *, alias: str = "industry_dim", join_type: str = "LEFT"
) -> str:
    mode = join_type.strip().upper()
    if mode not in {"LEFT", "INNER"}:
        raise ValueError(f"Unsupported industry join type: {join_type}")
    return f"{mode} JOIN {INDUSTRY_TABLE} {alias} ON {stock_expr} = {alias}.stock_code"


def industry_select_clause(*, alias: str = "industry_dim", prefix: str = "") -> str:
    parts = []
    for level in (1, 2, 3):
        col = industry_level_db_column(level)
        parts.append(f"{alias}.{col} AS {prefix}{col}")
    for level in (1, 2, 3):
        col = industry_name_db_column(level)
        parts.append(f"{alias}.{col} AS {prefix}{col}")
    return ", ".join(parts)


def industry_complete_condition(*, alias: str = "industry_dim") -> str:
    """L1+L2 齐全视为完整；L3 约 2200 股票官方未提供，不作要求。"""
    return " AND ".join(
        industry_level_nonempty_condition(level, alias=alias) for level in (1, 2)
    )


def load_industry_map(conn) -> dict[str, dict]:
    global _industry_cache
    cols = _table_columns(conn, INDUSTRY_TABLE)
    if "stock_code" not in cols:
        _industry_cache = {}
        return {}
    rows = conn.execute(
        f"SELECT stock_code, sw_l1, sw_l2, sw_l3, sw_l1_name, sw_l2_name, sw_l3_name "
        f"FROM {INDUSTRY_TABLE}"
    ).fetchall()
    result = {
        r["stock_code"]: {
            "sw_l1": r["sw_l1"],
            "sw_l2": r["sw_l2"],
            "sw_l3": r["sw_l3"],
            "sw_l1_name": r["sw_l1_name"],
            "sw_l2_name": r["sw_l2_name"],
            "sw_l3_name": r["sw_l3_name"],
        }
        for r in rows
    }
    _industry_cache = result
    return result


def resolve_industry(conn, stock_code: str, ref_date=None) -> dict:
    global _industry_cache
    if _industry_cache is not None and stock_code in _industry_cache:
        return _industry_cache[stock_code]
    row = conn.execute(
        f"SELECT sw_l1, sw_l2, sw_l3, sw_l1_name, sw_l2_name, sw_l3_name "
        f"FROM {INDUSTRY_TABLE} WHERE stock_code=?",
        (stock_code,),
    ).fetchone()
    if row:
        return {
            "sw_l1": row["sw_l1"],
            "sw_l2": row["sw_l2"],
            "sw_l3": row["sw_l3"],
            "sw_l1_name": row["sw_l1_name"],
            "sw_l2_name": row["sw_l2_name"],
            "sw_l3_name": row["sw_l3_name"],
        }
    return None


def count_industry_rows(conn) -> int:
    row = conn.execute(f"SELECT COUNT(*) FROM {INDUSTRY_TABLE}").fetchone()
    return (row[0] if row else 0) or 0


def summarize_industry_coverage(
    conn, stock_scope_sql: str, *, stock_code_column: str = "stock_code"
) -> dict:
    alias = "industry_dim"
    row = conn.execute(
        f"""
        WITH stock_scope AS (
            {stock_scope_sql}
        )
        SELECT
            COUNT(*) AS total_codes,
            SUM(CASE WHEN {industry_level_nonempty_condition(1, alias=alias)} THEN 1 ELSE 0 END) AS level1_codes,
            SUM(CASE WHEN {industry_level_nonempty_condition(2, alias=alias)} THEN 1 ELSE 0 END) AS level2_codes,
            SUM(CASE WHEN {industry_level_nonempty_condition(3, alias=alias)} THEN 1 ELSE 0 END) AS level3_codes,
            SUM(CASE WHEN {industry_complete_condition(alias=alias)} THEN 1 ELSE 0 END) AS complete_codes
        FROM stock_scope scope
        {industry_join_clause(f"scope.{stock_code_column}", alias=alias, join_type="LEFT")}
        """
    ).fetchone()
    if not row:
        return {
            "total_codes": 0,
            "level1_codes": 0,
            "level2_codes": 0,
            "level3_codes": 0,
            "complete_codes": 0,
        }
    return {
        "total_codes": row["total_codes"] or 0,
        "level1_codes": row["level1_codes"] or 0,
        "level2_codes": row["level2_codes"] or 0,
        "level3_codes": row["level3_codes"] or 0,
        "complete_codes": row["complete_codes"] or 0,
    }


def invalidate_cache():
    global _industry_cache
    _industry_cache = None
