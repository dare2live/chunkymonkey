"""Active stock universe — 第一性原理: K 线有交易 = 活跃, 没有 = 不活跃.

奥卡姆剃刀: 不需要 dim_all_ever_listed / 快照比对 / 多表 JOIN.
K 线就是真相源 — 交易所让它交易, K 线就有数据.

排除规则 (3 条, 仅此而已):
  1. 前缀不是 60/00/30/68 → 排除 (ETF/北交所/三板)
  2. 股票名含 ST/*ST → 排除 (涨跌停 ±5%, 规则不同)
  3. K 线最近 90 天无交易 → 排除 (退市/长期停牌)
"""
from __future__ import annotations

def _load_universe_config() -> dict:
    import yaml
    from pathlib import Path
    cfg_path = Path(__file__).resolve().parent.parent / "config" / "universe_rules.yaml"
    if cfg_path.exists():
        with open(cfg_path) as f:
            return yaml.safe_load(f) or {}
    return {}

_UNIVERSE_CFG = _load_universe_config()
ACTIVE_A_SHARE_PREFIXES: tuple[str, ...] = tuple(_UNIVERSE_CFG.get("include", {}).get("board_prefixes", ["60", "00", "30", "68"]))
ST_NAME_PREFIXES: tuple[str, ...] = tuple(_UNIVERSE_CFG.get("exclude", {}).get("st_name_patterns", ["ST", "*ST"]))
DELISTED_NO_TRADE_DAYS: int = _UNIVERSE_CFG.get("exclude", {}).get("delisted", {}).get("no_trade_days", 90)  # from yaml: universe_rules.yaml


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
    market_conn=None,
) -> set[str]:
    """哪些股票在交易? K 线是真相源.

    三条规则:
      1. 前缀 60/00/30/68 (A 股主板/创业板/科创板)
      2. stock_name 不含 ST/*ST
      3. K 线最近 90 天有交易 (退市/长期停牌排除)

    不依赖 dim_all_ever_listed (快照不可靠, 2026-05-26 误标 573 只).
    """
    prefixes_csv = ",".join(f"'{p}'" for p in ACTIVE_A_SHARE_PREFIXES)
    sql = f"SELECT stock_code, stock_name FROM dim_active_a_stock WHERE SUBSTR(stock_code, 1, 2) IN ({prefixes_csv})"
    rows = conn.execute(sql).fetchall()

    stocks = set()
    for code, name in rows:
        if not include_st and name and (name.startswith("ST") or name.startswith("*ST")):
            continue
        stocks.add(code)

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
            stocks &= recent_traded
    return stocks


_LIMIT_PCT_MAP = _UNIVERSE_CFG.get("limit_up_pct", {"60": 0.10, "00": 0.10, "30": 0.20, "68": 0.20})


def get_limit_up_pct(stock_code: str) -> float:
    """按板块返回涨停幅度. 从 universe_rules.yaml 读取."""
    if not stock_code or len(stock_code) < 2:
        return 0.10
    prefix = stock_code[:2]
    return float(_LIMIT_PCT_MAP.get(prefix, 0.10))


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
