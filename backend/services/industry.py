"""
industry.py — 行业解析单点实现 (Phase 2: 通达信 TDX 三级分类)

所有需要行业信息的地方统一通过本模块访问,
唯一数据源: dim_stock_tdx_industry (申万 dim_stock_industry 已于 Phase 2 退役)。

用法约定:
- 物化表构建 → load_industry_map() 批量装载
- 页面/服务层 → resolve_industry() 单点查询
- SQL 拼接 → industry_join_clause / industry_select_clause / industry_complete_condition
- 历史事件行业快照 (fact_institution_event_industry_snapshot) 暂保留 sw_level* 列,
  通过 event_industry_* helper 访问, 后续单独迁移。
"""

from typing import Optional
import logging

logger = logging.getLogger(__name__)

# 模块级缓存
_industry_cache: dict = None

# 当前行业口径 (通达信)
INDUSTRY_LEVEL_COLUMNS = ("tdx_l1", "tdx_l2", "tdx_l3")
INDUSTRY_NAME_COLUMNS = ("tdx_l1_name", "tdx_l2_name", "tdx_l3_name")
INDUSTRY_TABLE = "dim_stock_tdx_industry"

# 事件快照表仍为历史申万口径 (Phase 3 再迁移)
EVENT_INDUSTRY_TABLE = "fact_institution_event_industry_snapshot"
EVENT_INDUSTRY_LEVEL_COLUMNS = ("sw_level1", "sw_level2", "sw_level3")


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
    """行业层级在当前物理表中的实际列名。

    snapshot=True → fact_setup_snapshot 上的 `snapshot_tdx_l{level}` 列。
    snapshot=False → dim_stock_tdx_industry 上的 `tdx_l{level}` 列。
    """
    _validate_industry_level(level)
    prefix = "snapshot_" if snapshot else ""
    return f"{prefix}tdx_l{level}"


def industry_level_alias(level: int) -> str:
    """读模型/JSON 输出字段名 (保持与物理列一致)。"""
    return f"tdx_l{_validate_industry_level(level)}"


def industry_level_expr(level: int, *, alias: str = "industry_dim", snapshot: bool = False) -> str:
    return f"{alias}.{industry_level_db_column(level, snapshot=snapshot)}"


def industry_level_select(
    level: int,
    *,
    alias: str = "industry_dim",
    result_alias: Optional[str] = None,
    snapshot: bool = False,
) -> str:
    expr = industry_level_expr(level, alias=alias, snapshot=snapshot)
    return f"{expr} AS {result_alias}" if result_alias else expr


def industry_level_nonempty_condition(
    level: int, *, alias: str = "industry_dim", snapshot: bool = False
) -> str:
    expr = industry_level_expr(level, alias=alias, snapshot=snapshot)
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
    cols = INDUSTRY_LEVEL_COLUMNS + INDUSTRY_NAME_COLUMNS
    return ", ".join(f"{alias}.{col} AS {prefix}{col}" for col in cols)


def industry_complete_condition(*, alias: str = "industry_dim") -> str:
    return " AND ".join(
        industry_level_nonempty_condition(level, alias=alias) for level in (1, 2, 3)
    )


# ─── 事件快照 (历史申万口径, 待迁移) ─────────────────────────────────

def event_industry_join_clause(
    event_alias: str, *, alias: str = "industry_dim", join_type: str = "LEFT"
) -> str:
    mode = join_type.strip().upper()
    if mode not in {"LEFT", "INNER"}:
        raise ValueError(f"Unsupported industry join type: {join_type}")
    return (
        f"{mode} JOIN {EVENT_INDUSTRY_TABLE} {alias} "
        f"ON {event_alias}.institution_id = {alias}.institution_id "
        f"AND {event_alias}.stock_code = {alias}.stock_code "
        f"AND {event_alias}.report_date = {alias}.report_date"
    )


def event_industry_select_clause(*, alias: str = "industry_dim", prefix: str = "") -> str:
    """事件快照表 (sw_level1/2/3) SELECT 片段, 结果列名按 tdx_l* 别名对齐。"""
    parts = []
    for level in (1, 2, 3):
        src = f"{alias}.{EVENT_INDUSTRY_LEVEL_COLUMNS[level - 1]}"
        dst = f"{prefix}{industry_level_alias(level)}"
        parts.append(f"{src} AS {dst}")
    return ", ".join(parts)


# ─── 批量/单点查询 ──────────────────────────────────────────────────

def load_industry_map(conn) -> dict[str, dict]:
    global _industry_cache
    cols = _table_columns(conn, INDUSTRY_TABLE)
    if "stock_code" not in cols:
        _industry_cache = {}
        return {}
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


def resolve_industry(conn, stock_code: str, ref_date=None) -> dict:
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
