from typing import Optional

"""
industry.py — 行业解析单点实现

所有需要行业信息的地方统一通过本模块访问，避免业务代码散落直连行业表。

用法约定：
- 当前股票行业口径（页面/物化表）→ load_industry_map() / resolve_industry()
- 历史机构事件行业口径（回测/研究）→ event_industry_* helper，读取事件快照表
- 对外读模型同时暴露 legacy `sw_level*` 与中性 `industry_level*` 别名，避免把 TDX 行业继续伪装成申万口径。
"""

import logging

logger = logging.getLogger(__name__)

# 模块级缓存，避免同一进程内重复全表扫描
_industry_cache: dict = None
INDUSTRY_LEVEL_COLUMNS = ("sw_level1", "sw_level2", "sw_level3")
INDUSTRY_ALIAS_COLUMNS = (
    ("sw_level1", "industry_level1"),
    ("sw_level2", "industry_level2"),
    ("sw_level3", "industry_level3"),
)


def _validate_industry_level(level: int) -> int:
    if level not in (1, 2, 3):
        raise ValueError(f"Unsupported industry level: {level}")
    return level


def industry_level_alias(level: int) -> str:
    """中性行业层级字段名。"""
    return f"industry_level{_validate_industry_level(level)}"


def industry_level_value(industry: Optional[dict], level: int) -> str:
    """从行业对象中优先读取 neutral 键，再回退 legacy 键。"""
    if not industry:
        return ""
    neutral_key = industry_level_alias(level)
    legacy_key = industry_level_db_column(level)
    return industry.get(neutral_key) or industry.get(legacy_key) or ""


def industry_level_db_column(level: int, *, snapshot: bool = False) -> str:
    """行业层级在当前物理表中的实际列名。"""
    prefix = "snapshot_" if snapshot else ""
    return f"{prefix}sw_level{_validate_industry_level(level)}"


def industry_level_expr(level: int, *, alias: str = "industry_dim", snapshot: bool = False) -> str:
    """行业层级 SQL 表达式。"""
    return f"{alias}.{industry_level_db_column(level, snapshot=snapshot)}"


def industry_level_select(
    level: int,
    *,
    alias: str = "industry_dim",
    result_alias: Optional[str] = None,
    snapshot: bool = False,
) -> str:
    """行业层级 SELECT 片段。"""
    expr = industry_level_expr(level, alias=alias, snapshot=snapshot)
    return f"{expr} AS {result_alias}" if result_alias else expr


def industry_level_nonempty_condition(level: int, *, alias: str = "industry_dim", snapshot: bool = False) -> str:
    """行业层级非空条件。"""
    expr = industry_level_expr(level, alias=alias, snapshot=snapshot)
    return f"{expr} IS NOT NULL AND {expr} != ''"


def with_industry_aliases(industry: Optional[dict]) -> dict:
    """补齐 legacy / neutral 两套行业键，读模型可平滑过渡。"""
    result = dict(industry or {})
    for legacy_key, neutral_key in INDUSTRY_ALIAS_COLUMNS:
        value = result.get(neutral_key)
        if value in (None, ""):
            value = result.get(legacy_key)
        result[legacy_key] = value
        result[neutral_key] = value

    if "industry_code" in result or "sw_code" in result:
        code = result.get("industry_code")
        if code in (None, ""):
            code = result.get("sw_code")
        result["sw_code"] = code
        result["industry_code"] = code
    return result


def attach_industry_aliases(target: dict, industry: Optional[dict]) -> dict:
    """将行业层级同时写入 legacy / neutral 键。"""
    normalized = with_industry_aliases(industry)
    for legacy_key, neutral_key in INDUSTRY_ALIAS_COLUMNS:
        target[legacy_key] = normalized.get(legacy_key)
        target[neutral_key] = normalized.get(neutral_key)
    if "industry_code" in normalized or "sw_code" in normalized:
        target["sw_code"] = normalized.get("sw_code")
        target["industry_code"] = normalized.get("industry_code")
    return target


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


def load_industry_map(conn) -> dict[str, dict]:
    """
    批量加载行业映射，用于物化表构建（避免 N+1 查询）。

    Returns:
        {stock_code: {"sw_level1": ..., "sw_level2": ..., "sw_level3": ...,
                      "industry_level1": ..., "industry_level2": ..., "industry_level3": ...}}
    """
    global _industry_cache
    columns = _table_columns(conn, "dim_stock_industry")
    if "stock_code" not in columns:
        _industry_cache = {}
        return {}
    select_columns = ["stock_code"] + [col for col in INDUSTRY_LEVEL_COLUMNS if col in columns]
    rows = conn.execute(
        f"SELECT {', '.join(select_columns)} FROM dim_stock_industry"
    ).fetchall()
    result = {}
    for r in rows:
        result[r["stock_code"]] = with_industry_aliases({
            "sw_level1": (r["sw_level1"] if "sw_level1" in columns else None),
            "sw_level2": (r["sw_level2"] if "sw_level2" in columns else None),
            "sw_level3": (r["sw_level3"] if "sw_level3" in columns else None),
        })
    _industry_cache = result
    logger.debug(f"[Industry] Loaded industry map: {len(result)} stocks")
    return result


def industry_join_clause(stock_expr: str, *, alias: str = "industry_dim", join_type: str = "LEFT") -> str:
    """生成统一的行业关联 SQL 片段，避免业务代码散落直连表名。"""
    mode = join_type.strip().upper()
    if mode not in {"LEFT", "INNER"}:
        raise ValueError(f"Unsupported industry join type: {join_type}")
    return f"{mode} JOIN dim_stock_industry {alias} ON {stock_expr} = {alias}.stock_code"


def industry_select_clause(*, alias: str = "industry_dim", prefix: str = "") -> str:
    """生成统一的行业字段 SELECT 片段。"""
    return ", ".join(
        industry_level_select(level, alias=alias, result_alias=f"{prefix}{industry_level_db_column(level)}")
        for level in (1, 2, 3)
    )


def event_industry_join_clause(event_alias: str, *, alias: str = "industry_dim", join_type: str = "LEFT") -> str:
    """生成历史事件行业快照关联 SQL 片段。"""
    mode = join_type.strip().upper()
    if mode not in {"LEFT", "INNER"}:
        raise ValueError(f"Unsupported industry join type: {join_type}")
    return (
        f"{mode} JOIN fact_institution_event_industry_snapshot {alias} "
        f"ON {event_alias}.institution_id = {alias}.institution_id "
        f"AND {event_alias}.stock_code = {alias}.stock_code "
        f"AND {event_alias}.report_date = {alias}.report_date"
    )


def event_industry_select_clause(*, alias: str = "industry_dim", prefix: str = "") -> str:
    """生成事件行业快照字段 SELECT 片段。"""
    return industry_select_clause(alias=alias, prefix=prefix)


def industry_complete_condition(*, alias: str = "industry_dim") -> str:
    """生成三级行业完整条件。"""
    return " AND ".join(
        industry_level_nonempty_condition(level, alias=alias)
        for level in (1, 2, 3)
    )


def count_industry_rows(conn) -> int:
    """统一读取行业维表行数。"""
    row = conn.execute("SELECT COUNT(*) FROM dim_stock_industry").fetchone()
    return (row[0] if row else 0) or 0


def summarize_industry_coverage(conn, stock_scope_sql: str, *, stock_code_column: str = "stock_code") -> dict:
    """
    统计给定股票集合的行业覆盖情况。

    stock_scope_sql 必须返回一列股票代码，列名默认为 stock_code。
    """
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
            SUM(CASE
                WHEN {industry_complete_condition(alias=alias)}
                THEN 1 ELSE 0 END
            ) AS complete_codes
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
        "total_codes": (row["total_codes"] if row["total_codes"] is not None else 0),
        "level1_codes": (row["level1_codes"] if row["level1_codes"] is not None else 0),
        "level2_codes": (row["level2_codes"] if row["level2_codes"] is not None else 0),
        "level3_codes": (row["level3_codes"] if row["level3_codes"] is not None else 0),
        "complete_codes": (row["complete_codes"] if row["complete_codes"] is not None else 0),
    }


def resolve_industry(conn, stock_code: str, ref_date=None) -> dict:
    """
    单点查询股票行业分类。

    Args:
        conn: 数据库连接
        stock_code: 股票代码
        ref_date: 预留参数，未来支持按时点查历史行业。当前忽略。

    Returns:
        {"sw_level1": ..., "sw_level2": ..., "sw_level3": ...} or None
    """
    # 优先从缓存读
    global _industry_cache
    if _industry_cache is not None and stock_code in _industry_cache:
        return _industry_cache[stock_code]

    row = conn.execute(
        "SELECT sw_level1, sw_level2, sw_level3 "
        "FROM dim_stock_industry WHERE stock_code=?",
        (stock_code,)
    ).fetchone()
    if row:
        return with_industry_aliases({
            "sw_level1": row["sw_level1"],
            "sw_level2": row["sw_level2"],
            "sw_level3": row["sw_level3"],
        })
    return None
