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


# === 2026-05-23 SINGLE SOURCE OF TRUTH for batch task universe ===
# 用户 push '做一个专用的工具'. 所有 batch tasks 必须 调用 get_active_universe().

def get_active_universe(
    conn,
    *,
    include_st: bool = False,
    include_delisted: bool = False,
    market_conn=None,
) -> set[str]:
    """Single source of truth for batch task universe.

    Default: A-share main (60/00/30/68) + exclude ST/*ST + exclude 已退市 + (implicitly) exclude NEEQ/北交所/老三板/ETF (prefix not in KEEP).

    Args:
        conn: DuckDB connection (read-only OK)
        include_st: 含 ST/*ST (default False)
        include_delisted: 含 已退市 (default False, dim_all_ever_listed.is_active=0)

    Returns:
        Set of stock_code strings.

    Usage:
        with connect(db, read_only=True) as conn:
            universe = get_active_universe(conn)
    """
    prefixes_csv = ",".join(f"'{p}'" for p in ACTIVE_A_SHARE_PREFIXES)
    sql = f"SELECT stock_code FROM dim_active_a_stock WHERE SUBSTR(stock_code, 1, 2) IN ({prefixes_csv})"
    if not include_st:
        sql += " AND stock_name NOT LIKE 'ST%' AND stock_name NOT LIKE '*ST%'"
    stocks = {r[0] for r in conn.execute(sql).fetchall()}
    if not include_delisted:
        # 退市判定: 用 K 线实际交易记录, 不用 dim_all_ever_listed 快照
        # 原因: dim_all_ever_listed 靠快照比对, 数据源一次 sync 失败就误标退市
        #       (2026-05-26 发现 573 只活跃股被误标 is_active=0)
        # 正确逻辑: K 线最近 60 个交易日无数据 = 真退市/长期停牌
        try:
            import duckdb
            from pathlib import Path
            mkt = market_conn
            should_close = False
            if mkt is None:
                market_db = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
                if market_db.exists():
                    mkt = duckdb.connect(str(market_db), read_only=True)
                    should_close = True
            if mkt is not None:
                try:
                    recent_traded = {r[0] for r in mkt.execute(
                        "SELECT DISTINCT code FROM price_kline_tdxhub "
                        "WHERE freq='daily' AND CAST(date AS DATE) >= CURRENT_DATE - INTERVAL '90 days'"
                    ).fetchall()}
                finally:
                    if should_close:
                        mkt.close()
                if recent_traded:
                    truly_delisted = stocks - recent_traded
                    stocks -= truly_delisted
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "K-line based delisting check failed (%s) — filter not applied", e
            )
    return stocks


def get_limit_up_pct(stock_code: str) -> float:
    """按板块返回涨停幅度. 主板 10%, 创业板/科创板 20%.

    来源: dim_price_limit_rules + dim_market_segment.
    """
    if not stock_code or len(stock_code) < 3:
        return 0.10
    prefix3 = stock_code[:3]
    # rule-compliance: ok evidence=dim_price_limit_rules + dim_market_segment 2020-08-24 起
    if prefix3 in ("300", "301"):
        return 0.20  # 创业板
    if prefix3 == "688":
        return 0.20  # 科创板
    return 0.10  # 沪深主板 (60x/00x/001/002)


def build_limit_up_pct_map(stock_codes) -> dict[str, float]:
    """批量构建 {stock_code: limit_up_pct} 映射, 避免逐只查询."""
    return {code: get_limit_up_pct(code) for code in stock_codes}


def audit_strategy_universe_contamination(
    conn, *, table: str,
    model_id_col: str = "model_id",
    stock_code_col: str = "stock_code",
    model_id_filter: str | None = None,
) -> dict:
    """Audit existing strategy predictions for contamination by excluded stocks.

    Returns dict with per-category contamination counts + percentages.
    """
    where_filter = f"WHERE {model_id_col} = '{model_id_filter}'" if model_id_filter else ""
    and_or = "AND" if where_filter else "WHERE"

    total = conn.execute(f"SELECT COUNT(*), COUNT(DISTINCT {stock_code_col}) FROM {table} {where_filter}").fetchone()
    total_picks, unique_stocks = total[0], total[1]
    if not total_picks:
        return {"table": table, "model_id_filter": model_id_filter, "total_picks": 0}

    st = conn.execute(f"""
        SELECT COUNT(*), COUNT(DISTINCT t.{stock_code_col})
          FROM {table} t LEFT JOIN dim_active_a_stock d ON d.stock_code = t.{stock_code_col}
         {where_filter} {and_or} (d.stock_name LIKE 'ST%' OR d.stock_name LIKE '*ST%')
    """).fetchone()

    delisted = conn.execute(f"""
        SELECT COUNT(*), COUNT(DISTINCT t.{stock_code_col})
          FROM {table} t JOIN dim_all_ever_listed e ON e.stock_code = t.{stock_code_col}
         {where_filter} {and_or} e.is_active = 0
    """).fetchone()

    neeq = conn.execute(f"""
        SELECT COUNT(*), COUNT(DISTINCT {stock_code_col}) FROM {table}
         {where_filter} {and_or} (SUBSTR({stock_code_col}, 1, 1) = '8' OR SUBSTR({stock_code_col}, 1, 1) = '4')
    """).fetchone()

    etf = conn.execute(f"""
        SELECT COUNT(*), COUNT(DISTINCT {stock_code_col}) FROM {table}
         {where_filter} {and_or} (SUBSTR({stock_code_col}, 1, 2) IN ('15','51','56','58'))
    """).fetchone()

    return {
        "table": table, "model_id_filter": model_id_filter,
        "total_picks": total_picks, "unique_stocks": unique_stocks,
        "st_picks": st[0], "st_stocks": st[1], "st_pct": st[0] / total_picks * 100,
        "delisted_picks": delisted[0], "delisted_stocks": delisted[1],
        "delisted_pct": delisted[0] / total_picks * 100,
        "neeq_picks": neeq[0], "neeq_stocks": neeq[1], "neeq_pct": neeq[0] / total_picks * 100,
        "etf_picks": etf[0], "etf_stocks": etf[1], "etf_pct": etf[0] / total_picks * 100,
    }
