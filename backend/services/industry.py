"""
industry.py — 行业解析单点实现 (Phase 2: 通达信 TDX 三级分类)

所有需要行业信息的地方统一通过本模块访问,
唯一数据源: dim_stock_tdx_industry (申万 dim_stock_industry 已于 Phase 2 退役)。

用法约定:
- 物化表构建 → load_industry_map() 批量装载
- 页面/服务层 → resolve_industry() 单点查询
- SQL 拼接 → industry_join_clause / industry_select_clause / industry_complete_condition
"""

import logging

logger = logging.getLogger(__name__)

# 模块级缓存,避免同一进程内重复全表扫描
_industry_cache: dict = None

# 行业三级代码列名 (从申万 sw_level1/2/3 迁移到通达信 tdx_l1/l2/l3)
INDUSTRY_LEVEL_COLUMNS = ("tdx_l1", "tdx_l2", "tdx_l3")

# 行业三级中文名列 (通达信独有,申万旧数据没有中文名直接列)
INDUSTRY_NAME_COLUMNS = ("tdx_l1_name", "tdx_l2_name", "tdx_l3_name")

# 行业维表名 (唯一真相源)
INDUSTRY_TABLE = "dim_stock_tdx_industry"


def load_industry_map(conn) -> dict[str, dict]:
    """
    批量加载行业映射,用于物化表构建 (避免 N+1 查询)。

    Returns:
        {stock_code: {"tdx_l1": ..., "tdx_l2": ..., "tdx_l3": ...,
                      "tdx_l1_name": ..., "tdx_l2_name": ..., "tdx_l3_name": ...}}
    """
    global _industry_cache
    rows = conn.execute(
        f"SELECT stock_code, tdx_l1, tdx_l2, tdx_l3, "
        f"       tdx_l1_name, tdx_l2_name, tdx_l3_name "
        f"FROM {INDUSTRY_TABLE}"
    ).fetchall()
    result = {}
    for r in rows:
        result[r["stock_code"]] = {
            "tdx_l1": r["tdx_l1"],
            "tdx_l2": r["tdx_l2"],
            "tdx_l3": r["tdx_l3"],
            "tdx_l1_name": r["tdx_l1_name"],
            "tdx_l2_name": r["tdx_l2_name"],
            "tdx_l3_name": r["tdx_l3_name"],
        }
    _industry_cache = result
    logger.debug(f"[Industry] Loaded industry map: {len(result)} stocks (TDX)")
    return result


def industry_join_clause(stock_expr: str, *, alias: str = "industry_dim", join_type: str = "LEFT") -> str:
    """生成统一的行业关联 SQL 片段,避免业务代码散落直连表名。"""
    mode = join_type.strip().upper()
    if mode not in {"LEFT", "INNER"}:
        raise ValueError(f"Unsupported industry join type: {join_type}")
    return f"{mode} JOIN {INDUSTRY_TABLE} {alias} ON {stock_expr} = {alias}.stock_code"


def industry_select_clause(*, alias: str = "industry_dim", prefix: str = "") -> str:
    """生成统一的行业字段 SELECT 片段 (三级代码 + 三级中文名)。"""
    cols = INDUSTRY_LEVEL_COLUMNS + INDUSTRY_NAME_COLUMNS
    return ", ".join(f"{alias}.{col} AS {prefix}{col}" for col in cols)


def industry_complete_condition(*, alias: str = "industry_dim") -> str:
    """生成三级行业完整条件 (三级代码都不为 NULL / 空串)。"""
    return " AND ".join(
        f"{alias}.{col} IS NOT NULL AND {alias}.{col} != ''" for col in INDUSTRY_LEVEL_COLUMNS
    )


def count_industry_rows(conn) -> int:
    """统一读取行业维表行数。"""
    row = conn.execute(f"SELECT COUNT(*) FROM {INDUSTRY_TABLE}").fetchone()
    return (row[0] if row else 0) or 0


def summarize_industry_coverage(conn, stock_scope_sql: str, *, stock_code_column: str = "stock_code") -> dict:
    """
    统计给定股票集合的三级行业覆盖情况。

    stock_scope_sql 必须返回一列股票代码, 列名默认为 stock_code。
    """
    alias = "industry_dim"
    row = conn.execute(
        f"""
        WITH stock_scope AS (
            {stock_scope_sql}
        )
        SELECT
            COUNT(*) AS total_codes,
            SUM(CASE WHEN {alias}.tdx_l1 IS NOT NULL AND {alias}.tdx_l1 != '' THEN 1 ELSE 0 END) AS level1_codes,
            SUM(CASE WHEN {alias}.tdx_l2 IS NOT NULL AND {alias}.tdx_l2 != '' THEN 1 ELSE 0 END) AS level2_codes,
            SUM(CASE WHEN {alias}.tdx_l3 IS NOT NULL AND {alias}.tdx_l3 != '' THEN 1 ELSE 0 END) AS level3_codes,
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
    单点查询股票行业分类 (通达信三级)。

    Args:
        conn: 数据库连接
        stock_code: 股票代码
        ref_date: 预留参数, 未来支持按时点查历史行业。当前忽略。

    Returns:
        {"tdx_l1": ..., "tdx_l2": ..., "tdx_l3": ...,
         "tdx_l1_name": ..., "tdx_l2_name": ..., "tdx_l3_name": ...} or None
    """
    global _industry_cache
    if _industry_cache is not None and stock_code in _industry_cache:
        return _industry_cache[stock_code]

    row = conn.execute(
        f"SELECT tdx_l1, tdx_l2, tdx_l3, tdx_l1_name, tdx_l2_name, tdx_l3_name "
        f"FROM {INDUSTRY_TABLE} WHERE stock_code=?",
        (stock_code,),
    ).fetchone()
    if row:
        return {
            "tdx_l1": row["tdx_l1"],
            "tdx_l2": row["tdx_l2"],
            "tdx_l3": row["tdx_l3"],
            "tdx_l1_name": row["tdx_l1_name"],
            "tdx_l2_name": row["tdx_l2_name"],
            "tdx_l3_name": row["tdx_l3_name"],
        }
    return None


def invalidate_cache():
    """清除缓存, 下次调用 load_industry_map/resolve_industry 时重新加载。"""
    global _industry_cache
    _industry_cache = None
