"""Active stock universe filter — hardcoded A-share + exclude 老三板/北交所/退市/ETF.

用户原话: "可以硬编码排除退市、新三板老三板的股票".

PLAN_V3 v3.2 P-1.2 接受用户硬编码 universe (而非通用 survivorship-unbiased):
- A 股个人散户 5 仓位 paper_sim 场景, 不实际交易退市股/三板
- 排除后 P-1.2 spot check should_be_in 重新定义, 应 PASS
- 生存者偏差仍存在 (已显式接受), 但 alpha 训练 / 选股 / 实盘模拟一致

KEEP prefixes (v3.2 P-1 起始 universe):
- 60 沪主板 / 00 深主板 + 中小板 / 30 创业板 / 68 科创板

未在本 universe 内 (后续 phase 单独 enable, **不硬编码进排除清单**):
- ETF (15 / 51 / 56 / 58): 跟个股选股逻辑不同, 后续单独 `ACTIVE_ETF_PREFIXES` enable
- 港股通 / 老三板 / 北交所 等其他类: 后续按需引入

接入点 (待 P0a/b/c 时统一走此函数):
- audit_survivorship spot check expected universe
- P0a feature panel universe filter
- paper_sim selector candidate universe
- ML ranking training universe
"""
from __future__ import annotations

# 用户硬编码 KEEP universe (CLAUDE.md 项目特定补充允许的"硬编码"豁免):
# 60 沪主板 / 00 深主板 / 30 创业板 / 68 科创板.
# rule-compliance: ok evidence=user-硬编码-A股个人散户5仓位场景
ACTIVE_A_SHARE_PREFIXES: tuple[str, ...] = ("60", "00", "30", "68")


def is_active_a_share(stock_code: str) -> bool:
    """Stock code 是否属于活跃 A 股个人散户 universe (60/00/30/68 前缀).

    Note: 不查 delisted 状态 (那需要 DB lookup); 调用方需另外用 SQL JOIN
    `dim_all_ever_listed.is_active=1` 过滤. 本函数只看前缀.
    """
    if not stock_code or len(stock_code) < 2:
        return False
    return stock_code[:2] in ACTIVE_A_SHARE_PREFIXES


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
