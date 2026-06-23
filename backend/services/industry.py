"""
industry.py — 行业解析单点实现 (2026-06-16 S3: 切回申万 SW2021 三级分类)

所有需要行业信息的地方统一通过本模块访问。
数据源: **dim_stock_dc_industry** (申万当前快照, serving; 2026-06-16 从通达信切回申万, 06-11 ANOVA 实测申万L2
  区分度最优)。列名 tdx_l1/l2/l3(+name) 保留为**位置别名** (level-1/2/3 行业), 值已是申万——仅为最小化消费方改动,
  真相源/迁移见 analysis/industry_migration_tdx_to_sw_20260615.md。通达信行业源已物删 (2026-06-23 切东财 §4.3)。
  **as-of/PIT** (回测) 走 tushare_raw.v_sw_industry_pit (in_date<=t AND (out_date IS NULL OR out_date>t)); 本模块只服务"当前"。

用法约定:
- 物化表构建 → load_industry_map() 批量装载
- 页面/服务层 → resolve_industry() 单点查询 (返回当前归属; ref_date 见其 docstring)
- SQL 拼接 → industry_join_clause / industry_select_clause / industry_complete_condition
"""

from typing import Optional
import logging

logger = logging.getLogger(__name__)

# 模块级缓存
_industry_cache: dict = None

# 行业口径: 申万 SW2021 (2026-06-16 S3). 列名 tdx_l* = 历史位置别名 (level-1/2/3), 值已是申万 (最小化消费方改动)。
INDUSTRY_LEVEL_COLUMNS = ("tdx_l1", "tdx_l2", "tdx_l3")
INDUSTRY_NAME_COLUMNS = ("tdx_l1_name", "tdx_l2_name", "tdx_l3_name")
INDUSTRY_TABLE = "dim_stock_dc_industry"


def _validate_industry_level(level: int) -> int:
    if level not in (1, 2, 3):
        raise ValueError(f"Unsupported industry level: {level}")
    return level


def _table_columns(conn, table_name: str) -> set[str]:
    try:
        rows = conn.execute(f"DESCRIBE {table_name}").fetchall()
    except Exception:
        return set()
    return {row["column_name"] if hasattr(row, "keys") else row[0] for row in rows}


# ─── 列名 / 表达式 helper ─────────────────────────────────────────────

def industry_level_db_column(level: int, *, snapshot: bool = False) -> str:
    """行业层级在当前物理表中的实际列名。

    snapshot=True → fact_setup_snapshot 上的 `snapshot_tdx_l{level}` 列。
    snapshot=False → dim_stock_dc_industry (INDUSTRY_TABLE) 上的 `tdx_l{level}` 位置别名列。
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
    """返回 stock_code 的**当前**申万行业 (dim_stock_dc_industry 当前快照)。

    ref_date: 当前实现**不按 ref_date 做 as-of** (serving 只需当前归属)。需 PIT/历史 as-of (回测) 的,
    改查 tushare_raw.v_sw_industry_pit (WHERE in_date<=t AND (out_date IS NULL OR out_date>t))——别误以为本函数 PIT。
    2026-06-16 S3: 此前 ref_date 被静默忽略 (latent bug, 旧通达信快照同样无 PIT); 现切申万快照并显式标注语义。
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
