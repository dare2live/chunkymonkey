"""市场/财务数据加载器 — feature_panel 物化的输入层 (SERVE 单一读路 -> in-memory PIT 序列)。

owner=analysis/data_module_toplevel_design_20260622.md §1.5 (seed 不变量4 单概念单真相源/单一读路)。
2026-06-23 清洗迁移 (seed 现阶段核心): 3 loader 从直连 duck_connect(L0 raw/L1 market) 改走
  services.data_access.DataAccess.get() —— SERVE 单一清洗执行点。口径归一 (code->6位 / asof_col date->ISO)
  由 SERVE cleaner 统一做, loader 不再各自手转 (消第二清洗点, 守不变量1 统一主键+PIT锚 / 不变量4 单一读路)。
  total_flow(8 档买卖额求和) / roe_dt 选列 = 加工, 在 loader 从 SERVE 服务的原始列算 (纯函数, 不再 DB 现算)。
缘起 (A0 地基止血, 2026-06-19): 加载器原散在已删 experiment_* 脚本 (build_feature_panel BROKEN, import 悬空),
  移进 services 复用/可测。本次再把直连 raw 升级为走 SERVE (seed 清洗单一读路)。

分层契约: build 一次性经 SERVE 读 (写锁隔离在写侧 feature_store 独立库, daily_update 写 smartmoney 不争);
  探索/实验绝不直读 L0, 只读物化后 fact_feature_panel (moth feature-layer-l2-bypass-ratchet)。
PIT: 加载器取序列, PIT 由 services.formula_engine.features 因子函数保证 (feat[i] 只用 <=i);
  SERVE 默认锚 (conn=None 时 as_of=latest_closed) 闭合"无界返全史"fail-silent (不变量1 PIT 锚)。
  资金流盘后锚 trade_date (决策侧 JOIN t-1); 财报 as-of 锚 = ann_date (披露日) 非 end_date (期末)。
"""
from __future__ import annotations

from collections import defaultdict

from services.data_access import get_data_access  # SERVE 单一读路 (替代直连 duck_connect)
from services.universe import is_active_a_share    # config 驱动硬真相源, 替代内联 BOARD_PREFIXES

QUALITY_METRIC = "roe_dt"              # 扣非 ROE (剔非经常损益, 质量更干净); fundamentals entity 列名
# total_flow 加工原料: moneyflow entity 服务的 8 档买卖额 (全单买卖额之和; 求和=加工层纯函数, 非 DB 现算)
_MF_BUY_SELL = (
    "buy_sm_amount", "buy_md_amount", "buy_lg_amount", "buy_elg_amount",
    "sell_sm_amount", "sell_md_amount", "sell_lg_amount", "sell_elg_amount",
)


def load_kline(start: str, end: str | None = None, limit_stocks: int = 0, conn=None) -> dict[str, dict]:
    """kline_qfq (SERVE entity, L1k PIT 前复权) -> {code: {date,close,high,low,open,volume,amount}} 按 code,date 升序。

    open/volume/amount 供 execution-aware 引擎; close/high/low 因子用。
    end -> as_of (kline asof_col=date, 上界即 PIT cutoff); limit_stocks -> SERVE.distinct_codes。
    code 已由 SERVE 归一 6 位 / date 已 ISO (单一清洗执行点)。conn 可注入 (测试)。
    """
    da = get_data_access()
    codes = da.distinct_codes("kline_qfq", limit=limit_stocks, conn=conn) if limit_stocks > 0 else None
    rows = da.get("kline_qfq", codes=codes, start=start, as_of=end, conn=conn).rows
    rows.sort(key=lambda r: (r["code"], r["date"]))   # 守原 ORDER BY code,date (get 不保证序)
    flds = ("date", "close", "high", "low", "open", "volume", "amount")
    by_code: dict[str, dict] = defaultdict(lambda: {k: [] for k in flds})
    for r in rows:
        d = by_code[r["code"]]
        for k in flds:
            d[k].append(r[k])
    return dict(by_code)


def load_moneyflow(start: str, conn=None) -> dict[str, dict[str, tuple[float, float]]]:
    """moneyflow (SERVE entity, L0 盘后) -> {code6: {YYYY-MM-DD: (net_mf_amount, total_flow)}}。

    net_mf_amount = tushare 厂商净主动流口径 (万元); total_flow = 8 档全单买卖额之和 (加工求和)。
    **警告 (reconcile wf_e6a0e9e8, 2026-06-21)**: net_mf_amount **不是** '大单+特大单(elg+lg)主力净额' —
      实测它与中小单档/价格动量镜像, 与大单主力档常反向。真主力净额用 services.technical_states.capital.mainforce_net。
    PIT 锚 trade_date (盘后, 决策侧 JOIN t-1); code/date 已由 SERVE 归一。conn 可注入 (测试)。
    """
    rows = get_data_access().get("moneyflow", start=start, conn=conn).rows
    out: dict[str, dict] = defaultdict(dict)
    for r in rows:
        net = r["net_mf_amount"]
        if net is None:
            continue
        # total_flow 加工 (纯函数): 复刻原 SQL 求和的 NULL 传播 (任一档 NULL -> 整和 NULL -> 0.0), 不改值
        flow = None if any(r[k] is None for k in _MF_BUY_SELL) else sum(r[k] for k in _MF_BUY_SELL)
        out[r["ts_code"]][r["trade_date"]] = (float(net), float(flow) if flow is not None else 0.0)
    return dict(out)


def load_quality_reports(metric: str = QUALITY_METRIC, conn=None) -> dict[str, list]:
    """fundamentals (SERVE entity, L0) -> {code6: [(ann_date_iso, end_date, value)]} 按 ann_date 升序。

    PIT 锚 = ann_date (披露日, SERVE 已转 ISO) 非 end_date (期末, 保留 YYYYMMDD 仅同股内 max 比) — 用 end_date = leakage 死。
    含 start 前历史报告 (as-of 需要); metric 须为 fundamentals entity 已声明列 (roe/roe_dt/roe_yearly 等)。conn 可注入。
    """
    if not metric.replace("_", "").isalnum():   # 防注入 (metric 应为列名常量)
        raise ValueError(f"非法 metric 列名: {metric!r}")
    rows = get_data_access().get("fundamentals", conn=conn).rows
    rows.sort(key=lambda r: (r["ts_code"], r["ann_date"]))   # 守原 ORDER BY ts_code,ann_date
    out: dict[str, list] = defaultdict(list)
    for r in rows:
        val = r.get(metric)
        if val is None or r["ann_date"] is None or r["end_date"] is None:
            continue
        out[r["ts_code"]].append((r["ann_date"], r["end_date"], float(val)))
    return dict(out)


def in_active_universe(code: str) -> bool:
    """物化时 universe 过滤 = config 驱动硬真相源 (services.universe), 不留内联前缀第二真相源。

    只做板块前缀过滤 (排北交所/三板); ST/退市是 PIT 时变量, 留选股/回测侧硬门。
    """
    return is_active_a_share(code)
