"""市场/财务数据加载器 — feature_panel 物化的输入层 (L0 raw / L1 market -> in-memory PIT 序列)。

owner=analysis/feature_layer_and_test_plan_20260615.md (L2_feature 输入) + docs/data_management_framework.md。
缘起 (A0 地基止血, 2026-06-19): 加载器原散在已删 experiment_* 脚本 (build_feature_panel BROKEN, import 悬空)。
  移进 services 复用/可测, 消除 builder->experiment 倒挂。逐字复刻已删 loader 逻辑 (git 33f6b430^),
  仅升级一处: 内联 BOARD_PREFIXES -> services.universe.is_active_a_share (config 驱动, 不留第二真相源前缀)。

分层契约 (L2-bypass lesson, CLAUDE §4.5): 物化 build 是 lesson 允许的唯一 L0-read 点
  —— build 一次性读 L0 raw / L1k market, 写 feature_store 独立库 (写锁隔离在写侧, daily_update 写 smartmoney 不争);
  探索/实验绝不直读 L0, 只读物化后的 fact_feature_panel (moth feature-layer-l2-bypass-ratchet)。
PIT: 加载器只取原始序列, PIT 由 services.formula_engine.features 的因子函数保证 (feat[i] 只用 <=i)。
  资金流盘后锚 trade_date (决策侧 JOIN t-1); 财报 as-of 锚 = ann_date (披露日) 非 end_date (期末)。
"""
from __future__ import annotations

from collections import defaultdict

from services.database_manifest import get_database_manifest
from services.duck_adapter import connect as duck_connect
from services.universe import is_active_a_share  # config 驱动硬真相源, 替代内联 BOARD_PREFIXES

_MANIFEST = get_database_manifest()
MARKET_DB = str(_MANIFEST.path_for("market"))       # L1k: price_kline_qfq_tushare (manifest 路由=单一真相源, 不 hardcode .duckdb)
RAW_DB = str(_MANIFEST.path_for("tushare_raw"))     # L0: raw_tushare_moneyflow / raw_tushare_fina_indicator
QUALITY_METRIC = "roe_dt"              # 扣非 ROE (剔非经常损益, 质量更干净); tushare fina_indicator 列名


def load_kline(start: str, end: str | None = None, limit_stocks: int = 0, conn=None) -> dict[str, dict]:
    """price_kline_qfq_tushare (L1k market, PIT 前复权) -> {code: {date,close,high,low,open,volume,amount}} 按 code,date 升序。

    open/volume/amount 供 execution-aware 引擎 (T+1 open 入场 + 涨跌停一字板判定 + 容量诊断); close/high/low 因子用。
    conn: 可注入 (测试用 :memory:); 缺省只读开 market.duckdb。
    """
    where = f"date >= '{start}'" + (f" AND date <= '{end}'" if end else "")
    own = conn is None
    c = conn or duck_connect(MARKET_DB, read_only=True)
    try:
        if limit_stocks > 0:
            codes = [r[0] for r in c.execute(
                f"SELECT DISTINCT code FROM price_kline_qfq_tushare WHERE {where} ORDER BY code LIMIT {limit_stocks}"
            ).fetchall()]
            where += " AND code IN ('" + "','".join(codes) + "')"
        rows = c.execute(
            f"SELECT code, date, close, high, low, open, volume, amount FROM price_kline_qfq_tushare "
            f"WHERE {where} ORDER BY code, date"
        ).fetchall()
    finally:
        if own:
            c.close()
    by_code: dict[str, dict] = defaultdict(
        lambda: {k: [] for k in ("date", "close", "high", "low", "open", "volume", "amount")})
    for code, date, close, high, low, open_, volume, amount in rows:
        d = by_code[code]
        d["date"].append(date)
        d["close"].append(close)
        d["high"].append(high)
        d["low"].append(low)
        d["open"].append(open_)
        d["volume"].append(volume)
        d["amount"].append(amount)
    return dict(by_code)


def load_moneyflow(start: str, conn=None) -> dict[str, dict[str, tuple[float, float]]]:
    """raw_tushare_moneyflow (L0, 盘后) -> {code6: {YYYY-MM-DD: (net_mf_amount, total_flow)}}。

    net_mf_amount = tushare 厂商**净主动流口径** (≈net_mf_vol×当日VWAP/10, 万元); total_flow = 全单买卖额之和 (万元)。
    **警告 (reconcile wf_e6a0e9e8 裁决, 2026-06-21)**: net_mf_amount **不是**'大单+特大单(elg+lg)主力净额' — 实测它数学上
      跟中小单档/价格动量 (corr(net_mf,lg+elg)与corr(net_mf,md+sm)完全镜像, 与大单主力档常反向)。真主力净额请用
      services.technical_states.capital.mainforce_net (=elg+lg净, =东财dc.net_amount同构念)。此处保留 net_mf 仅作'净主动流/动量代理'。
    PIT 锚 trade_date (盘后更新); 截面特征消费侧决策 JOIN t-1。conn 可注入 (测试)。
    """
    sd = start.replace("-", "")
    own = conn is None
    c = conn or duck_connect(RAW_DB, read_only=True)
    try:
        rows = c.execute(
            "SELECT ts_code, trade_date, net_mf_amount, "
            "(buy_sm_amount+buy_md_amount+buy_lg_amount+buy_elg_amount"
            "+sell_sm_amount+sell_md_amount+sell_lg_amount+sell_elg_amount) AS total_flow "
            "FROM raw_tushare_moneyflow WHERE trade_date >= ? AND net_mf_amount IS NOT NULL", [sd]
        ).fetchall()
    finally:
        if own:
            c.close()
    out: dict[str, dict] = defaultdict(dict)
    for ts, td, net, flow in rows:
        code = ts.split(".")[0]
        d = f"{td[:4]}-{td[4:6]}-{td[6:8]}"
        out[code][d] = (float(net), float(flow) if flow is not None else 0.0)
    return dict(out)


def load_quality_reports(metric: str = QUALITY_METRIC, conn=None) -> dict[str, list]:
    """raw_tushare_fina_indicator (L0) -> {code6: [(ann_date_iso, end_date, value)]} 按 ann_date 升序。

    PIT 锚 = ann_date (披露日, 转 ISO) 非 end_date (期末) — 用 end_date = 漏未来已披露 = leakage 死 (mythos §3a/§8)。
    含 start 前历史报告 (as-of 需要)。end_date 保留 YYYYMMDD (仅 max() 比同股内部, 不与 ISO 决策日比)。conn 可注入。
    """
    if not metric.replace("_", "").isalnum():   # 防 SQL 注入 (metric 应为列名常量, 非外部输入)
        raise ValueError(f"非法 metric 列名: {metric!r}")
    own = conn is None
    c = conn or duck_connect(RAW_DB, read_only=True)
    try:
        rows = c.execute(
            f"SELECT ts_code, ann_date, end_date, {metric} FROM raw_tushare_fina_indicator "
            f"WHERE {metric} IS NOT NULL AND ann_date IS NOT NULL AND end_date IS NOT NULL "
            "ORDER BY ts_code, ann_date"
        ).fetchall()
    finally:
        if own:
            c.close()
    out: dict[str, list] = defaultdict(list)
    for ts, ann, end, val in rows:
        a = f"{ann[:4]}-{ann[4:6]}-{ann[6:8]}"   # ann_date YYYYMMDD -> ISO (与 kline 决策日同格式可比)
        out[ts.split(".")[0]].append((a, end, float(val)))
    return dict(out)


def in_active_universe(code: str) -> bool:
    """物化时 universe 过滤 = config 驱动硬真相源 (services.universe), 不留内联前缀第二真相源。

    只做板块前缀过滤 (排北交所/三板); ST/退市是 PIT 时变量, 留选股/回测侧硬门 (含历史不可一刀切删股)。
    """
    return is_active_a_share(code)
