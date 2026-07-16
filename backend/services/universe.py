"""Active stock universe — 第一性原理: K 线有交易 = 活跃, 没有 = 不活跃.

奥卡姆剃刀: 不需要 dim_all_ever_listed / 快照比对 / 多表 JOIN.
K 线就是真相源 — 交易所让它交易, K 线就有数据.

排除规则 (3 条, 仅此而已):
  1. 前缀不是 60/00/30/68 → 排除 (ETF/北交所/三板)
  2. 股票名含 ST/*ST → 排除 (涨跌停 ±5%, 规则不同)
  3. K 线最近 90 天无交易 → 排除 (退市/长期停牌)
"""
from __future__ import annotations


class UniverseDataError(RuntimeError):
    """Raised when a required universe truth source is unavailable."""


class UniverseContaminationError(RuntimeError):
    """Raised when a stock set contains excluded (non-whitelist) codes.

    2026-06-17 用户决议: universe 升到交易日历级硬真相源 — 排除股进任何
    验证/回测/GT = 硬错, 不是 warning。任何最终股票集必过 assert_universe_clean()。
    """


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

    Note: 不查交易状态; 调用方必须用 K 线 truth source 检查近期有交易.
    本函数只看前缀.
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


def _sql_like_any_prefix(column: str, prefixes: tuple[str, ...]) -> str:
    if not prefixes:
        return "FALSE"
    likes = []
    for prefix in prefixes:
        escaped = prefix.replace("'", "''")
        likes.append(f"{column} LIKE '{escaped}%'")
    return "(" + " OR ".join(likes) + ")"


def _table_exists(conn, table_name: str) -> bool:
    return conn.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_name = ?
         LIMIT 1
        """,
        (table_name,),
    ).fetchone() is not None


def sql_where_no_st(stock_name_column: str = "stock_name") -> str:
    """SQL WHERE 子句排除 ST/*ST stock names.

    Example:
        sql = f"... LEFT JOIN dim_active_a_stock d ON ... WHERE {sql_where_no_st('d.stock_name')}"
        # 输出: (d.stock_name IS NULL OR NOT (...configured ST patterns...))
    """
    return f"({stock_name_column} IS NULL OR NOT {_sql_like_any_prefix(stock_name_column, ST_NAME_PREFIXES)})"


# === 2026-05-23 SINGLE SOURCE OF TRUTH for batch task universe ===
# 用户 push '做一个专用的工具'. 所有 batch tasks 必须 调用 get_active_universe().

def get_active_universe(
    conn=None,
    *,
    include_st: bool = False,
    market_conn=None,
) -> set[str]:
    """K 线有交易 + 前缀白名单 + 非 ST = universe. 就这三条."""
    mkt = market_conn
    should_close = False
    if mkt is None:
        try:
            from services.market_db import get_market_conn
            mkt = get_market_conn()
            should_close = True
        except Exception as exc:
            raise UniverseDataError("K-line market DB is required for active universe truth") from exc

    try:
        from services.market_read import get_analysis_kline_qfq_relation
        kline_relation = get_analysis_kline_qfq_relation()
        no_trade_days = int(DELISTED_NO_TRADE_DAYS)
        codes = {r[0] for r in mkt.execute(
            f"SELECT DISTINCT code FROM {kline_relation} "
            "WHERE freq='daily' "
            f"AND CAST(date AS DATE) >= CURRENT_DATE - INTERVAL '{no_trade_days} days'"  # rule-compliance: ok evidence=活跃liveness粗启发(近N日历日有K线=在交易), 日历天足够判退市/长停, 非PIT决策锚
        ).fetchall()}
    finally:
        if should_close:
            mkt.close()

    stocks = {c for c in codes if len(c) >= 2 and c[:2] in ACTIVE_A_SHARE_PREFIXES}

    # 2026-06-19 身份真相源交集: 只留 dim_active_a_stock (tushare stock_basic 真股清单) 内的码,
    #   剔除 K线里的指数 benchmark (沪深300=000300 等与 00 前缀共号段者直读 K线漏入 universe)。
    #   前缀仍作 defense-in-depth 预筛; conn=None (legacy include_st 路径) 回退纯前缀。
    # §9 拆库: identity/ST 读 reference dim (security_master active_codes/active_stock_name_map, auto-fallback);
    #   conn 守卫语义保留 (conn is not None 才 intersect; conn=None legacy 纯前缀)。
    if conn is not None:
        from services.security_master import active_codes
        identity = active_codes(conn)
        if identity:
            stocks &= identity

    if not include_st:
        if conn is None:
            raise UniverseDataError("smart DB connection is required for ST name mapping")
        from services.security_master import active_stock_name_map
        name_map = active_stock_name_map(conn=conn)
        if not name_map:
            raise UniverseDataError("dim_active_a_stock (reference) is required for ST name mapping")
        st_codes = {c for c, n in name_map.items() if is_st_stock(n)}
        stocks -= st_codes

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


# audit_strategy_universe_contamination() 2026-07-07 整段退役 (owner=PROJECT_INDEX.md dim_all_ever_listed
# 决策收口): 审计"策略预测表"(strategy predictions table)是否混入排除股, 但策略/serving/scoring 层已
# 于 2026-06-28 纯数据平台重建整体退役, 项目里已不存在这类预测表可审; 生产 0 调用方(仅
# backend/tests/test_universe.py 测试自身), 且其退市码集来源 dim_all_ever_listed 本身也已物删。

# =====================================================================
# 硬真相源门 (2026-06-17 用户决议: universe 升到交易日历级)
# 排除列表里的股票永不进任何验证/回测/GT/选股。任何最终股票集必过
# assert_universe_clean()。前缀级判定 (无 DB, 快), 报错带板块归类。
# =====================================================================

_EXCLUDED_BOARDS: dict[str, str] = dict(_UNIVERSE_CFG.get("exclude", {}).get("excluded_boards", {}))


def classify_exclusion(stock_code: str) -> str | None:
    """返回排除原因 (板块名); 若在白名单内返回 None.

    白名单 = include.board_prefixes (60/00/30/68)。补集按 excluded_boards
    taxonomy 归类 (北交所/三板/ETF), 兜底 '非白名单(前缀)'。
    """
    if not stock_code or len(stock_code) < 2:
        return "代码畸形"
    p2 = stock_code[:2]
    if p2 in ACTIVE_A_SHARE_PREFIXES:
        return None
    # 先查 2 位, 再查 1 位 taxonomy
    if p2 in _EXCLUDED_BOARDS:
        return _EXCLUDED_BOARDS[p2]
    if stock_code[:1] in _EXCLUDED_BOARDS:
        return _EXCLUDED_BOARDS[stock_code[:1]]
    return f"非白名单({p2}x)"


def assert_universe_clean(stock_codes, *, context: str = "") -> bool:
    """硬门: 若 stock_codes 含任何排除股, raise UniverseContaminationError.

    交易日历级真相源 — 就像非交易日不能下单, 排除股不能进 universe。
    GT/回测/实验/选股的最终股票集必调本函数。前缀级, 无 DB。
    """
    bad: dict[str, list[str]] = {}
    for code in stock_codes:
        reason = classify_exclusion(code)
        if reason is not None:
            bad.setdefault(reason, []).append(str(code))
    if bad:
        n_bad = sum(len(v) for v in bad.values())
        parts = "; ".join(f"{r}: {len(v)}只(如{v[:3]})" for r, v in sorted(bad.items()))
        ctx = f" @ {context}" if context else ""
        raise UniverseContaminationError(
            f"universe 污染{ctx}: {n_bad} 只排除股混入 — {parts}. "
            f"修: 股票集先过 services.universe.assert_universe_clean / get_active_universe."
        )
    return True


def load_st_calendar(raw_conn) -> dict[str, set[str]]:
    """PIT ST 日历: {code(6位): set(YYYYMMDD)} — 某股某日是否被 ST 标记的真相源.

    源: raw_tushare_stock_st (data_source.st_calendar)。用于历史 t 的 PIT ST 判定
    (旧 dim_active_a_stock 只有当前名字, 非 PIT)。单一计算点: GT/回测共用本函数,
    不各自内联 ST 查询。
    """
    if not _table_exists(raw_conn, "raw_tushare_stock_st"):
        raise UniverseDataError("raw_tushare_stock_st (PIT ST 真相源) 不存在")
    rows = raw_conn.execute(
        "SELECT DISTINCT SUBSTR(ts_code,1,6) AS code, REPLACE(trade_date,'-','') AS d "
        "FROM raw_tushare_stock_st"
    ).fetchall()
    cal: dict[str, set[str]] = {}
    for code, d in rows:
        cal.setdefault(code, set()).add(d)
    return cal


def is_st_on(stock_code: str, yyyymmdd: str, st_calendar: dict[str, set[str]]) -> bool:
    """PIT: stock_code 在 yyyymmdd (无横杠) 当日是否 ST."""
    return yyyymmdd in st_calendar.get(stock_code, ())
