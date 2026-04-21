"""
industry.py — 行业解析单点实现 (申万官方三级分类)

所有需要行业信息的地方统一通过本模块访问。
当前数据源: dim_stock_sw_industry (Phase 2 从 TDX 切换到申万,
覆盖率从 51% 提升到 100% L3)。

Phase 3 系列清理后的口径:
  - 物理列与源表一致: sw_l1/sw_l2/sw_l3 (sw_industry_client 落库)
  - 派生表 (mart_current_relationship / dim_stock_industry_context_latest
    / fact_stock_industry_context) 物理列同步 sw_l*
  - resolver dict key 仍保留 tdx_l{n} 作为 API 契约兼容
    (Phase 3C 美容时可再迁移)
  - fact_setup_snapshot 已 Phase 3B-3 退役

用法约定:
- 物化表构建 → load_industry_map() 批量装载
- 页面/服务层 → resolve_industry() 单点查询
- SQL 拼接 → industry_join_clause / industry_select_clause / industry_complete_condition
"""

from typing import Optional
import logging

logger = logging.getLogger(__name__)

# 模块级缓存
_industry_cache: dict = None

# 当前行业口径 (申万官方)
# 注意：物理列以 sw_ 开头，但 industry_level_alias 仍返回 tdx_l{n} 兜底外部兼容
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


# ─── 列名 / 表达式 helper ─────────────────────────────────────────────

def industry_level_db_column(level: int, *, snapshot: bool = False) -> str:
    """行业层级在物理表中的实际列名。

    snapshot 分支已于 Phase 3B-3 随 fact_setup_snapshot 一起退役；
    仅保留参数签名兼容调用方（现统一返回 sw_l{level}）。
    """
    _validate_industry_level(level)
    return f"sw_l{level}"


def industry_level_alias(level: int) -> str:
    """读模型/JSON 输出字段名 (保留 tdx_l{n} 作为历史兼容别名,
    下游 dict key 不需修改)。"""
    return f"tdx_l{_validate_industry_level(level)}"


def industry_name_db_column(level: int) -> str:
    """对应 level 的物理 name 列名 (sw_l{n}_name)。"""
    _validate_industry_level(level)
    return f"sw_l{level}_name"


def industry_name_alias(level: int) -> str:
    """name 列在下游 dict 中的 key (保留 tdx_l{n}_name 兼容)。"""
    return f"tdx_l{_validate_industry_level(level)}_name"


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
    """从行业 dict 中读取 tdx_l{level}, 空则返回空串。"""
    if not industry:
        return ""
    key = industry_level_alias(level)
    return industry.get(key) or ""


def with_industry_aliases(industry: Optional[dict]) -> dict:
    """返回带齐全三级 tdx_l* 键的 dict (TDX 单一口径, 无 sw 别名)。"""
    result = dict(industry or {})
    for level in (1, 2, 3):
        key = industry_level_alias(level)
        result.setdefault(key, result.get(key))
    return result


def attach_industry_aliases(target: dict, industry: Optional[dict]) -> dict:
    """把 tdx_l* 三级代码写入 target。"""
    normalized = with_industry_aliases(industry)
    for level in (1, 2, 3):
        key = industry_level_alias(level)
        target[key] = normalized.get(key)
    return target


# ─── 关联片段 ────────────────────────────────────────────────────────

def industry_join_clause(
    stock_expr: str, *, alias: str = "industry_dim", join_type: str = "LEFT"
) -> str:
    mode = join_type.strip().upper()
    if mode not in {"LEFT", "INNER"}:
        raise ValueError(f"Unsupported industry join type: {join_type}")
    return f"{mode} JOIN {INDUSTRY_TABLE} {alias} ON {stock_expr} = {alias}.stock_code"


def industry_select_clause(*, alias: str = "industry_dim", prefix: str = "") -> str:
    """SELECT 片段：物理 sw_l{n} 列输出为 tdx_l{n} 别名 (下游兼容)。"""
    parts = []
    for level in (1, 2, 3):
        parts.append(
            f"{alias}.{industry_level_db_column(level)} "
            f"AS {prefix}{industry_level_alias(level)}"
        )
    for level in (1, 2, 3):
        parts.append(
            f"{alias}.{industry_name_db_column(level)} "
            f"AS {prefix}{industry_name_alias(level)}"
        )
    return ", ".join(parts)


def industry_complete_condition(*, alias: str = "industry_dim") -> str:
    """定义行业"完整"的最低口径：必须有 L1 和 L2 即可，L3 为 TDX 可选字段。

    原因：TDX 的 tdxhy.cfg 对 2660+ 股票只提供二级分类（如 银行 T1001、软件服务 T1205
    这类本身就属于末级行业，没有更细的子分类）。若硬性要求 L3 非空，会产生约
    2239 条永久性"假缺口"，触发 market_gap_queue 将它们标为 blocked，并让
    build_industry_stat 永久提示"26 家机构行业层级未补齐"。L1+L2 已具有足够
    粒度（~400 个二级行业），能满足机构行业统计所需的聚合口径。L3 保留在 dim
    表中供需要更细分类的场景按需使用。"""
    return " AND ".join(
        industry_level_nonempty_condition(level, alias=alias) for level in (1, 2)
    )


# ─── 批量/单点查询 ──────────────────────────────────────────────────

def load_industry_map(conn) -> dict[str, dict]:
    """批量加载股票 → 行业 dict (key 保留 tdx_l{n} 兼容下游)。"""
    global _industry_cache
    cols = _table_columns(conn, INDUSTRY_TABLE)
    if "stock_code" not in cols:
        _industry_cache = {}
        return {}
    rows = conn.execute(
        f"SELECT stock_code, "
        f"       sw_l1 AS tdx_l1, sw_l2 AS tdx_l2, sw_l3 AS tdx_l3, "
        f"       sw_l1_name AS tdx_l1_name, sw_l2_name AS tdx_l2_name, sw_l3_name AS tdx_l3_name "
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
    logger.debug(f"[Industry] Loaded industry map: {len(result)} stocks (SW 申万)")
    return result


def resolve_industry(conn, stock_code: str, ref_date=None) -> dict:
    global _industry_cache
    if _industry_cache is not None and stock_code in _industry_cache:
        return _industry_cache[stock_code]
    row = conn.execute(
        f"SELECT sw_l1 AS tdx_l1, sw_l2 AS tdx_l2, sw_l3 AS tdx_l3, "
        f"       sw_l1_name AS tdx_l1_name, sw_l2_name AS tdx_l2_name, sw_l3_name AS tdx_l3_name "
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
