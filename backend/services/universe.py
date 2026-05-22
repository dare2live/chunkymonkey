"""Active stock universe filter — hardcoded A-share + exclude 老三板/北交所/退市/ETF/ST.

用户原话: "可以硬编码排除退市、新三板老三板的股票" + 2026-05-22 23:50 "排除 ST 北交所了吗"

PLAN_V3 v3.2 P-1.2 接受用户硬编码 universe (而非通用 survivorship-unbiased):
- A 股个人散户 5 仓位 paper_sim 场景, 不实际交易退市股/三板/ST 股
- 排除后 P-1.2 spot check should_be_in 重新定义, 应 PASS
- 生存者偏差仍存在 (已显式接受), 但 alpha 训练 / 选股 / 实盘模拟一致

KEEP prefixes (v3.2 P-1 起始 universe):
- 60 沪主板 / 00 深主板 + 中小板 / 30 创业板 / 68 科创板

未在本 universe 内 (after prefix filter):
- ETF (15 / 51 / 56 / 58): 跟个股选股逻辑不同
- 港股通 / 老三板 / 北交所 (8/4): 流动性 / 规则不同

额外 ST/*ST filter (2026-05-22 audit 发现 V4 top-10 picks 中 19.31% 是 ST/*ST):
- ST/*ST 跌停 ±5% (vs normal ±10%), 流动性差, 退市风险
- 实盘 unrealistic, paper_sim 假设 normal trading mechanism
- 通过 `dim_active_a_stock.stock_name LIKE 'ST%'/'*ST%'` 排除
- Caveat: 仅当前 ST status, 不是 PIT historical (历史 ST→去 ST 或反向 仍 leak)
"""
from __future__ import annotations

# 用户硬编码 KEEP universe (CLAUDE.md 项目特定补充允许的"硬编码"豁免):
# 60 沪主板 / 00 深主板 / 30 创业板 / 68 科创板.
# rule-compliance: ok evidence=user-硬编码-A股个人散户5仓位场景
ACTIVE_A_SHARE_PREFIXES: tuple[str, ...] = ("60", "00", "30", "68")
# rule-compliance: ok evidence=2026-05-22 audit V4 top-10 picks 19.31% 是 ST/*ST 必排除
ST_NAME_PREFIXES: tuple[str, ...] = ("ST", "*ST")


def is_active_a_share(stock_code: str) -> bool:
    """Stock code 是否属于活跃 A 股个人散户 universe (60/00/30/68 前缀).

    Note: 不查 delisted 状态 (那需要 DB lookup); 调用方需另外用 SQL JOIN
    `dim_all_ever_listed.is_active=1` 过滤. 本函数只看前缀.
    """
    if not stock_code or len(stock_code) < 2:
        return False
    return stock_code[:2] in ACTIVE_A_SHARE_PREFIXES


def is_st_stock(stock_name: str) -> bool:
    """Check if stock_name indicates ST/*ST status.

    2026-05-22 audit: V4 top-10 picks 中 19.31% 是 ST/*ST (834/4320).
    """
    if not stock_name:
        return False
    return any(stock_name.startswith(p) for p in ST_NAME_PREFIXES)


def filter_active_a_share(stock_codes) -> list[str]:
    """过滤 stock_code 列表, 只留活跃 A 股 universe (前缀过滤, 不查 delisted)."""
    return [c for c in stock_codes if is_active_a_share(c)]


def sql_where_active_a_share(column: str = "stock_code") -> str:
    """生成 SQL WHERE 子句 (前缀过滤). 调用方可叠加 delisted 过滤.

    Example:
        sql = f"SELECT * FROM xxx WHERE {sql_where_active_a_share()}"
        # 输出: WHERE SUBSTR(stock_code, 1, 2) IN ('60','00','30','68')
    """
    prefixes = ",".join(f"'{p}'" for p in ACTIVE_A_SHARE_PREFIXES)
    return f"SUBSTR({column}, 1, 2) IN ({prefixes})"


def sql_where_no_st(stock_name_column: str = "stock_name") -> str:
    """SQL WHERE 子句排除 ST/*ST stock names.

    Example:
        sql = f"... LEFT JOIN dim_active_a_stock d ON ... WHERE {sql_where_no_st('d.stock_name')}"
        # 输出: (d.stock_name IS NULL OR d.stock_name NOT LIKE 'ST%' AND d.stock_name NOT LIKE '*ST%')
    """
    return (
        f"({stock_name_column} IS NULL OR "
        f"({stock_name_column} NOT LIKE 'ST%' AND {stock_name_column} NOT LIKE '*ST%'))"
    )
