"""dossier — 股票档案聚合服务 (form 维度解读器, 前端档案视图的后端桥接)。

owner=docs/stock_dossier_master_design.md (P2) + backend/services/technical_states/。
设计: 实现维度解读器协议的 form 维度 — interpret(单股多TF形态解读)/screen(列符合形态的股票)/series(趋势线非K线)。
后续维度(板块/资金/筹码)各加一个同协议模块, dossier 聚合层并列调用 (本文件先做 form 维度)。
PIT: 只用 ≤end 的 bar (load_kline end 截断); universe 排除股不进 screen 集 (assert_universe_clean)。
趋势线: 不画 K线蜡烛, 用 (date, close, 主态) 分段着色折线 + 降采样, 供前端轻量渲染。
"""
from __future__ import annotations

from datetime import date, timedelta

from services.data_access import get_data_access  # SERVE 读层 (PIT/口径/血缘统一; 替内联 raw 裸查)
from services.data_loaders import MARKET_DB, RAW_DB
from services.duck_adapter import connect as duck_connect
from services.technical_states.candles import candle_pattern
from services.technical_states.context import apply_context
from services.technical_states.limits import code_to_ts_code, compute_limit_flags, enrich_features
from services.technical_states.patterns import match_named_patterns
from services.technical_states.capital import capital_signals
from services.technical_states.chips import chip_signals
from services.technical_states.vol import volume_signals
from services.technical_states.rs import relative_strength
from services.technical_states.sector_context import sector_regime, concept_labels
from services.technical_states.fundamentals import (
    fundamental_signals, valuation_signals, analyst_expectation, forecast_signal,
)
from services.technical_states.events import lhb_signal, block_signal, unlock_signal
from services.technical_states.regime import market_regime
from services.technical_states import (
    apply_coupling,
    classify_multi_timeframe,
    classify_series,
    compute,
    list_tunables,
    load_config,
    with_overrides,
)

# 主态 → 趋势线着色语义 (前端用; bull绿/bear红/中性灰)
_STATE_COLOR = {
    "上升通道": "up", "放量突破": "up", "缩量上涨": "up", "缩量回踩": "up",
    "下跌通道": "down", "高位滞涨": "down",
    "低位横盘": "flat",
}


def _effective_cfg(overrides: dict | None):
    """应用前端参数 override (经边界耦合同步) → effective config + 同步说明。"""
    cfg = load_config()
    if not overrides:
        return cfg, []
    synced, notes = apply_coupling(overrides, cfg)
    return with_overrides(cfg, synced), notes


def load_one(code: str, end: str | None = None,
             start: str = "2015-01-01") -> dict | None:  # rule-compliance: ok evidence=serving 档案视图K线加载窗口起始日, 非策略参数(暖机120+足够多TF)
    """单股 OHLCV (PIT: ≤end); 走中央 adapter 只读 market.duckdb。返回 {date,open,high,low,close,volume} 平行数组。"""
    rows = get_data_access().get("kline_qfq", codes=[code], start=start, as_of=end).rows  # ≤end PIT, ASC by date
    if not rows:
        return None
    return {"date": [r["date"] for r in rows], "open": [r["open"] for r in rows], "high": [r["high"] for r in rows],
            "low": [r["low"] for r in rows], "close": [r["close"] for r in rows], "volume": [r["volume"] for r in rows]}


def load_limits(code: str) -> dict:
    """stk_limit (up/down_limit, A股涨跌停真相源) → {date_iso: (up_limit, down_limit)}。2022+ 才有。"""
    rows = get_data_access().get("stk_limit", codes=[code]).rows   # SERVE 读层 (trade_date 归一 ISO)
    return {r["trade_date"]: (r["up_limit"], r["down_limit"]) for r in rows}


def _iso(td) -> str:
    """tushare YYYYMMDD → ISO date (复用; DRY)。"""
    td = str(td)
    return f"{td[:4]}-{td[4:6]}-{td[6:8]}" if (len(td) == 8 and "-" not in td) else td


def load_capital(code: str) -> tuple[dict, dict]:
    """**东财 moneyflow_dc 单一供应商** (维度③, 与项目 概念=东财 同源 口径自洽; flow-vendor=membership-vendor 红线)。
    → (money_by_date, turnover_by_date)。money_by_date={date:{net_amount(主力大单净, 万元), net_amount_rate(占成交额%), pct_change(涨跌%)}}。
    东财数据 2023-09 起 (前无资金, 档案描述近期为主)。明盘=net_amount; 量价背离=net_amount方向 vs pct_change (暗盘伪维度已砍, 见 capital.py 裁决)。
    """
    da = get_data_access()
    dc = da.get("moneyflow_dc", codes=[code]).rows        # SERVE 读层 (trade_date 已归一 ISO)
    tr = da.get("valuation", codes=[code]).rows
    money = {r["trade_date"]: {"net_amount": r["net_amount"], "net_amount_rate": r["net_amount_rate"],
                               "pct_change": r["pct_change"]} for r in dc}
    return (money, {r["trade_date"]: r["turnover_rate"] for r in tr})


def load_cyq(code: str) -> dict:
    """cyq_perf 筹码分布/胜率 (维度④) → {date_iso: {winner_rate, cost_5/50/95pct, weight_avg}}。"""
    rows = get_data_access().get("cyq", codes=[code]).rows   # SERVE 读层 (trade_date 已归一 ISO)
    return {r["trade_date"]: {"winner_rate": r["winner_rate"], "cost_5pct": r["cost_5pct"],
            "cost_50pct": r["cost_50pct"], "cost_95pct": r["cost_95pct"], "weight_avg": r["weight_avg"]}
            for r in rows}


def load_top10_holders(code: str, as_of: str | None = None) -> dict | None:
    """L3 机构维度: 十大**流通**股东最近季 + 本季动向 (新进/增持/减持/退出)。机构跟随=用户最初设想策略基础。
    真相源 = **tushare top10_floatholders** (2026-06-22 切, 用户点名"切换到tushare源"; ann_date PIT 公告日);
    tdx F10 (smartmoney) 降备援 (§4.3 旧源热备不删, tushare 空时 fallback)。PIT: ann_date <= as_of 最近 end_date。
    """
    return _top10_tushare(code, as_of) or _top10_tdx(code, as_of)   # tushare 主源, tdx 备援


def _top10_tushare(code: str, as_of: str | None) -> dict | None:
    """tushare top10_floatholders: 最近 2 期 (current+prev) diff 出 新进/退出, hold_change 符号出 增持/减持。
    PIT (复审 HIGH): 同 end_date 可多次公告(修正), 明细必锁 ann_date=MAX(ann_date)<=t 版本, 防 ann_date>t 修正行泄漏。
    占比用 hold_float_ratio(占流通, 与'流通股东'语义+旧tdx一致) COALESCE hold_ratio(占总, float_ratio NULL 时兜底)。
    """
    rows = get_data_access().get("holders_top10", codes=[code], as_of=as_of).rows  # ann_date≤asof PIT
    if not rows:
        return None
    eds = sorted({r["end_date"] for r in rows}, reverse=True)[:2]      # 最近 2 期 end_date
    cur_ed, prev_ed = eds[0], (eds[1] if len(eds) > 1 else None)

    def _locked(ed):   # 该 end_date 在 t 已公告最新版本 (MAX ann_date≤asof), 排除未来修正行 (复审HIGH 版本锁)
        er = [r for r in rows if r["end_date"] == ed]
        if not er:
            return []
        top_ann = max(r["ann_date"] for r in er)
        return [r for r in er if r["ann_date"] == top_ann]

    cur_rows = sorted(_locked(cur_ed), key=lambda r: r["hold_amount"] or 0, reverse=True)  # 原 ORDER BY hold_amount DESC
    cur = [(r["holder_name"],
            r["hold_float_ratio"] if r["hold_float_ratio"] is not None else r["hold_ratio"],   # 占流通 COALESCE 占总
            r["hold_change"]) for r in cur_rows]
    prev_names = {r["holder_name"] for r in _locked(prev_ed)} if prev_ed else set()
    if not cur:
        return None
    holders, cnt = [], {"新进": 0, "退出": 0, "增持": 0, "减持": 0}
    cur_names = {h[0] for h in cur}
    for i, (name, ratio, chg) in enumerate(cur, 1):
        # 动向走 tushare **现成 hold_change** (用户: 能获取的就不计算; 实测核证 NaN=新进无前值/0=持平/+=增持/-=减持),
        # 不再跨季 diff 算新进。NaN 兼容 None(DuckDB NULL) 与 float('nan')。
        chg = None if (chg is not None and isinstance(chg, float) and chg != chg) else chg
        if chg is None:
            status = "新进"; cnt["新进"] += 1     # tushare 对新进股东无前值 → hold_change 留空
        elif chg > 0:
            status = "增持"; cnt["增持"] += 1
        elif chg < 0:
            status = "减持"; cnt["减持"] += 1
        else:
            status = "持平"
        holders.append({"rank": i, "name": name, "ratio": ratio, "change": status, "change_num": chg})
    # 退出 = 上季 top10 本季离榜: tushare 当期快照**给不出**离榜股东行 → 唯一须跨季 diff 的状态 (其余全走 hold_change)
    if prev_ed:
        cnt["退出"] = len(prev_names - cur_names)
    net = (cnt["新进"] + cnt["增持"]) - (cnt["退出"] + cnt["减持"])
    return {"report_date": cur_ed, "holders": holders, "源": "tushare",
            "新进": cnt["新进"], "退出": cnt["退出"], "增持": cnt["增持"], "减持": cnt["减持"],
            "机构动向": "加仓" if net > 0 else "减仓" if net < 0 else "持平"}


def _top10_tdx(code: str, as_of: str | None = None) -> dict | None:
    """tdx F10 备援 (§4.3 旧源热备): smartmoney.fact_top10_holder_period (2017Q4+, change_status 文本进出标记)。"""
    from services.data_loaders import SMARTMONEY_DB
    from collections import Counter
    asof_norm = as_of.replace("-", "") if as_of else None    # 数据坑: report_date 格式不统一(带/不带横线), 规范化比较
    c = duck_connect(SMARTMONEY_DB, read_only=True)
    try:
        where_asof = " AND REPLACE(report_date,'-','') <= ?" if asof_norm else ""
        args = [code] + ([asof_norm] if asof_norm else [])
        mxrow = c.execute(f"SELECT report_date FROM fact_top10_holder_period "
                          f"WHERE stock_code = ? AND holder_set = 'free'{where_asof} "
                          f"ORDER BY REPLACE(report_date,'-','') DESC LIMIT 1", args).fetchone()
        if not mxrow:
            return None
        mx = mxrow[0]
        cur = c.execute("SELECT holder_rank, holder_name_norm, hold_ratio_float, change_status, hold_change_num "
                        "FROM fact_top10_holder_period WHERE stock_code = ? AND holder_set = 'free' "
                        "AND report_date = ? AND is_exit_row = false ORDER BY holder_rank", [code, mx]).fetchall()
        moves = [r[0] for r in c.execute("SELECT change_status FROM fact_top10_holder_period "
                 "WHERE stock_code = ? AND holder_set = 'free' AND report_date = ?", [code, mx]).fetchall()]
    finally:
        c.close()
    if not cur:
        return None
    cnt = Counter(m for m in moves if m)
    net = (cnt.get("新进", 0) + cnt.get("增持", 0)) - (cnt.get("退出", 0) + cnt.get("减持", 0))   # 机构净加减仓
    return {
        "report_date": mx, "源": "tdx(备援)",
        "holders": [{"rank": r[0], "name": r[1], "ratio": r[2], "change": r[3], "change_num": r[4]} for r in cur],
        "新进": cnt.get("新进", 0), "退出": cnt.get("退出", 0), "增持": cnt.get("增持", 0), "减持": cnt.get("减持", 0),
        "机构动向": "加仓" if net > 0 else "减仓" if net < 0 else "持平",
    }


def load_benchmark(ts_code: str) -> dict:
    """基准指数日线 (RS 用; 如 HS300 000300.SH) → {date_iso: close}。"""
    rows = get_data_access().get("index_daily", codes=[ts_code]).rows  # ts_passthrough 指数码直用; trade_date 归一 ISO
    return {r["trade_date"]: r["close"] for r in rows}


def load_sector_membership(code: str, as_of: str | None = None) -> dict:
    """L3 板块归属 (PIT, 口径红线 J6: 行业=申万 / 概念=东财):
    - 申万行业: v_sw_industry_pit (in_date<=t AND (out_date NULL OR out_date>t), as-of 取最近段); l1_code 直接=sw_daily ts_code。
    - 东财概念: dc_member 最近快照<=t (con_code=个股, ts_code=板块BKxxxx.DC); 概念名+热度 JOIN dc_index (dc_member.name=个股名非板块名, 坑)。
    返回 {sw_l1_code, sw_l1_name, sw_l2_name, concepts:[(code,name)], concept_hot:{code:pct_change%}}。
    """
    ts = code_to_ts_code(code)
    asof = as_of.replace("-", "") if as_of else None       # v_sw_industry_pit / dc_member trade_date 均 YYYYMMDD
    c = duck_connect(RAW_DB, read_only=True)
    try:
        sw_where, sw_args = "", [code]
        if asof:                                            # PIT: 归属区间含 t (in_date<=t<out_date)
            sw_where = " AND in_date <= ? AND (out_date IS NULL OR out_date > ?)"
            sw_args += [asof, asof]
        sw = c.execute(f"SELECT l1_code, l1_name, l2_name FROM v_sw_industry_pit "
                       f"WHERE stock_code = ?{sw_where} ORDER BY in_date DESC LIMIT 1", sw_args).fetchone()
        m_where = " AND trade_date <= ?" if asof else ""    # 东财概念: 最近快照 <= t (PIT)
        snap = c.execute(f"SELECT MAX(trade_date) FROM raw_tushare_dc_member WHERE con_code = ?{m_where}",
                         [ts] + ([asof] if asof else [])).fetchone()
        concepts, hot = [], {}
        if snap and snap[0]:
            rows = c.execute(
                "SELECT m.ts_code, i.name, i.pct_change FROM raw_tushare_dc_member m "
                "LEFT JOIN raw_tushare_dc_index i ON i.ts_code = m.ts_code AND i.trade_date = m.trade_date "
                "WHERE m.con_code = ? AND m.trade_date = ?", [ts, snap[0]]).fetchall()
            for bk, nm, pct in rows:
                concepts.append((bk, nm or bk))
                if pct is not None:
                    hot[bk] = pct
    finally:
        c.close()
    return {"sw_l1_code": sw[0] if sw else None, "sw_l1_name": sw[1] if sw else None,
            "sw_l2_name": sw[2] if sw else None, "concepts": concepts, "concept_hot": hot}


def load_sector_kline(sw_l1_code: str | None, end: str | None = None) -> dict:
    """申万一级行业指数日线 (sw_daily, ts_code=l1_code 直接对应) → {date_iso: close}。PIT: trade_date<=end。"""
    if not sw_l1_code:
        return {}
    rows = get_data_access().get("sw_daily", codes=[sw_l1_code], as_of=end).rows  # ts_passthrough 行业码; trade_date≤end 归一ISO
    return {r["trade_date"]: r["close"] for r in rows}


def load_fundamentals(code: str, as_of: str | None = None) -> dict | None:
    """L3 基本面 (PIT 锚 ann_date 公告日, 防财报 leakage): fina_indicator 最近已公告财报指标。
    取 ann_date <= as_of 的最近一期 (ann_date DESC, end_date DESC); roe_yearly 年化(跨报告期可比)。
    """
    keys = ["end_date", "roe", "roe_yearly", "netprofit_yoy", "or_yoy", "grossprofit_margin", "debt_to_assets"]
    rows = get_data_access().get("fundamentals", codes=[code], as_of=as_of).rows  # ann_date≤asof PIT (锚公告日)
    if not rows:
        return None
    best = max(rows, key=lambda r: (r["ann_date"], r["end_date"]))   # 原 ORDER BY ann_date DESC, end_date DESC LIMIT 1
    return {k: best[k] for k in keys}


def load_forecast(code: str, as_of: str | None = None) -> dict | None:
    """L3 业绩预告 (前瞻 PEAD, PIT 锚 ann_date 公告日): 取 ann_date<=t 最近一次预告 (修订=新ann_date胜, PIT干净)。
    raw_tushare_forecast 已在库 (零拉取)。**同日多版本冲突处理 (复审 LOW, measured-not-estimated)**:
    实测 18 股-期 同 (ts,end,ann) 携冲突 type (如 002141 同日 略减-36% vs 预增+636%), ORDER BY 无决胜键 →
    取行非确定性 (表重建即翻方向)。修: 最新 (ann_date,end_date) 组若多 type 自相矛盾 → 不伪造方向, 标"存疑"。
    """
    keys = ["type", "p_change_min", "p_change_max", "end_date", "ann_date"]
    rows = get_data_access().get("forecast", codes=[code], as_of=as_of).rows  # ann_date≤asof PIT (锚公告日)
    if not rows:
        return None
    top_ann = max(r["ann_date"] for r in rows)                   # 最新公告日 (原 ORDER BY ann_date DESC)
    top_end = max(r["end_date"] for r in rows if r["ann_date"] == top_ann)   # 该公告日最新报告期组
    group = [r for r in rows if r["ann_date"] == top_ann and r["end_date"] == top_end]
    if len({r["type"] for r in group}) > 1:                      # evidence: 实测18股-期同日多type矛盾 → 上游自相矛盾不伪造方向(中性)
        return {"type": "存疑(同日多版本)", "p_change_min": None, "p_change_max": None,
                "end_date": top_end, "ann_date": top_ann}
    g0 = group[0]
    return {k: g0[k] for k in keys}


def load_valuation(code: str, as_of: str | None = None) -> tuple[dict | None, list, list]:
    """L3 估值 (PIT 锚 trade_date): 最新 daily_basic + pe_ttm/pb ≤t 历史序列 (供自身分位)。
    返回 (cur dict, pe_hist, pb_hist)。daily_basic 估值字段 (pe/pb/市值) 走 L3; 换手/量比 已在 L2 capital。
    """
    rows = get_data_access().get("valuation", codes=[code], as_of=as_of).rows  # trade_date≤asof SERVE; ASC
    if not rows:
        return None, [], []
    last = rows[-1]
    cur = {"trade_date": last["trade_date"], "pe_ttm": last["pe_ttm"], "pb": last["pb"],
           "ps_ttm": last["ps_ttm"], "dv_ttm": last["dv_ttm"], "total_mv": last["total_mv"]}
    return cur, [r["pe_ttm"] for r in rows], [r["pb"] for r in rows]


def load_analyst_reports(code: str, as_of: str | None = None, months: int = 6) -> list[tuple]:
    """L3 分析师预期 (PIT 锚 report_date 发布日): 近 months 月券商研报 [(report_date, rating, tp)] (均 ≤t)。"""
    asof_iso = as_of or date.today().isoformat()
    cutoff_iso = (date.fromisoformat(asof_iso) - timedelta(days=months * 31)).isoformat()
    rows = get_data_access().get("report_rc", codes=[code], start=cutoff_iso, as_of=asof_iso).rows
    rows.reverse()   # ASC → 原显示序 DESC (analyst_expectation order-independent: 丢弃report_date, count+median)
    # tp 单位实测 = 0.0001元 (tp/10000 = 目标价元; mythos§8 字段单位必实测) — 留 loader (待移 entity clean transform)
    return [(r["report_date"], r["rating"], (r["tp"] / 10000.0 if r["tp"] else r["tp"])) for r in rows]


def load_lhb(code: str, as_of: str | None = None, days: int = 60) -> tuple[list, list]:
    """L3⑤ 龙虎榜 (PIT 锚 trade_date): 近 days 日上榜明细 + 机构席位净买 (均 ≤t)。
    返回 (lhb_rows=[(trade_date, net_amount, reason)], inst_rows=[(trade_date, net_buy)] 机构专用席位)。
    """
    asof_iso = as_of or date.today().isoformat()
    cutoff_iso = (date.fromisoformat(asof_iso) - timedelta(days=days)).isoformat()
    da = get_data_access()
    lhb = da.get("top_list", codes=[code], start=cutoff_iso, as_of=asof_iso).rows
    lhb.reverse()    # ASC → 原显示序 DESC (last_reason=lhb[0]; lhb_signal sum order-independent, 取最新日reason)
    inst = da.get("top_inst", codes=[code], start=cutoff_iso, as_of=asof_iso).rows
    inst = [r for r in inst if r.get("exalter") and "机构" in str(r["exalter"])]   # 机构专用席位 (原 LIKE '%机构%')
    return ([(r["trade_date"], r["net_amount"], r["reason"]) for r in lhb],
            [(r["trade_date"], r["net_buy"]) for r in inst])


def load_block_trade(code: str, as_of: str | None = None, days: int = 60) -> list:
    """L3⑤ 大宗交易 (PIT 锚 trade_date): 近 days 日大宗 [(trade_date, price, amount)] (≤t)。"""
    asof_iso = as_of or date.today().isoformat()
    cutoff_iso = (date.fromisoformat(asof_iso) - timedelta(days=days)).isoformat()
    rows = get_data_access().get("block_trade", codes=[code], start=cutoff_iso, as_of=asof_iso).rows  # trade_date 归一 ISO
    rows.reverse()   # SERVE 返 ASC → 原显示序 DESC (block_signal order-independent: mean+sum)
    return [(r["trade_date"], r["price"], r["amount"]) for r in rows]


def load_share_float(code: str, as_of: str | None = None) -> list:
    """L3⑤ 解禁 (前瞻事件, PIT 锚 ann_date 公告日 — 公告≤t 时未来 float_date 已知, 0 泄露):
    返回 [(float_date, float_ratio)] (ann_date<=as_of)。unlock_signal 过滤未来 horizon 内。
    """
    rows = get_data_access().get("share_float", codes=[code],
                                 as_of=as_of or date.today().isoformat()).rows  # ann_date≤asof PIT
    rows.sort(key=lambda r: str(r["float_date"]))            # 原 ORDER BY float_date (float_date 保持 raw)
    return [(r["float_date"], r["float_ratio"]) for r in rows]


def load_market_regime(as_of: str | None = None, index_code: str = "000300.SH") -> dict | None:
    """L3⑥ 市场 regime (横切非单股, PIT 锚 trade_date): 大盘指数 close 序列 + 每日涨跌停情绪 (≤t)。
    返回 market_regime(...) 在 as_of 当日的 regime dict, 或 None。所有股共享 (大盘环境)。
    """
    asof = as_of.replace("-", "") if as_of else None
    c = duck_connect(RAW_DB, read_only=True)
    try:
        iw = "ts_code = ?" + (" AND trade_date <= ?" if asof else "")
        idx = c.execute(f"SELECT trade_date, close FROM raw_tushare_index_daily WHERE {iw} ORDER BY trade_date",
                        [index_code] + ([asof] if asof else [])).fetchall()
        sw = "trade_date <= ?" if asof else "1=1"
        # COUNT(DISTINCT ts_code) 防 limit_list_d 精确重复行 (实测 23116 重复, 个股最多插14次) 膨胀涨停家数 (复审 HIGH)
        senti = c.execute(f'SELECT trade_date, "limit", COUNT(DISTINCT ts_code) FROM raw_tushare_limit_list_d '
                          f'WHERE {sw} GROUP BY trade_date, "limit"', ([asof] if asof else [])).fetchall()
    finally:
        c.close()
    if not idx:
        return None
    idx_dates = [_iso(r[0]) for r in idx]
    idx_close = [r[1] for r in idx]
    sentiment: dict = {}                                  # {iso_date: {up,down,zha}}
    for td, lim, n in senti:
        d = sentiment.setdefault(_iso(td), {})
        d["up" if lim == "U" else "down" if lim == "D" else "zha"] = n
    cfg = load_config()
    reg = market_regime(idx_dates, idx_close, sentiment, cfg=cfg)
    return reg.get(idx_dates[-1]) if reg else None


def _multi_tf(ohlcv: dict, cfg: dict, limit_data: dict | None = None) -> dict:
    """OHLCV → 多TF 分类 (读 config 三框窗口)。limit_data 给则 enrich 日线(A股涨停修正, D3)。"""
    d = ohlcv
    tf = cfg["timeframes"]
    feats = {name: compute(d["date"], d["open"], d["high"], d["low"], d["close"], d["volume"],
                           timeframe=name, resample_rule=spec.get("resample"),
                           warmup=spec.get("warmup", 120), windows=spec.get("windows"))
             for name, spec in tf.items()}
    if limit_data:                                          # D3: 涨停日修正日线特征(防放量突破误判)
        ul = [limit_data.get(dt, (None, None))[0] for dt in d["date"]]
        dl = [limit_data.get(dt, (None, None))[1] for dt in d["date"]]
        flags = compute_limit_flags(d["date"], d["open"], d["high"], d["low"], d["close"], ul, dl,
                                    eps=(cfg.get("涨停") or {}).get("贴板容差", 0.003))
        enrich_features(feats["daily"], flags, cfg)
    mtf = classify_multi_timeframe(feats["daily"], feats["weekly"], feats["monthly"], cfg)
    return mtf, feats


def trend_series(ohlcv: dict, daily_cls: dict, max_points: int = 240) -> list[dict]:
    """趋势线 (非K线): (date, close, 着色态) 降采样到 ≤max_points; 着色按当日主态 up/down/flat。"""
    dates, closes = ohlcv["date"], ohlcv["close"]
    n = len(dates)
    step = max(1, n // max_points)
    out = []
    for i in range(0, n, step):
        d = dates[i]
        cls = daily_cls.get(d)
        dom = (cls.get("refined_dominant") or cls["dominant"]) if cls else None   # D4 上下文 refine
        out.append({"date": d, "close": closes[i], "color": _STATE_COLOR.get(dom, "flat"), "state": dom})
    return out


def _desc(cfg: dict, state: str | None) -> str:
    if not state:
        return "过渡态 (无清晰主态)"
    return cfg["状态"].get(state, {}).get("描述", state)


def interpret_stock(code: str, end: str | None = None, overrides: dict | None = None) -> dict | None:
    """单股档案 (form 维度): 多TF 当下解读 + 趋势线 + 可调参数。end=None 取最新; overrides=前端调参。"""
    ohlcv = load_one(code, end)
    if not ohlcv:
        return None
    cfg, notes = _effective_cfg(overrides)
    mtf, feats = _multi_tf(ohlcv, cfg, limit_data=load_limits(code))   # D3 A股涨停修正
    daily_cls = classify_series(feats["daily"], cfg)
    apply_context(daily_cls, feats["daily"], cfg)                      # D4 上下文层(缩量回踩复活+prior_trend)
    if not mtf:
        return {"code": code, "error": "数据不足 (暖机后无有效 bar)", "trend": []}
    last_date = max(mtf)
    last = mtf[last_date]
    dlast = daily_cls.get(last_date, {})
    tf_read = {}
    for name in ("daily", "weekly", "monthly"):
        st = last.get(name)
        if name == "daily":
            st = dlast.get("refined_dominant") or st                   # 日线用上下文 refine
        tf_read[name] = {"state": st, "desc": _desc(cfg, st),
                         "sub": last.get("daily_sub") if name == "daily" else None}
    i = len(ohlcv["date"]) - 1                                         # D5 当日单日K线形态
    lf = feats["daily"].get(last_date, {})
    today_candle = candle_pattern(ohlcv["open"][i], ohlcv["high"][i], ohlcv["low"][i], ohlcv["close"][i],
                                  prior_trend=dlast.get("prior_trend"),
                                  is_up_limit=bool(lf.get("is_up_limit")), is_down_limit=bool(lf.get("is_down_limit")),
                                  is_one_word=bool(lf.get("is_one_word")), cfg=cfg)
    refined_seq = [(d, daily_cls[d].get("refined_dominant")) for d in sorted(daily_cls)]   # D5b 命名形态
    named = match_named_patterns(refined_seq, cfg)
    recent_patterns = [{"date": d, **p} for d in sorted(named)[-5:] for p in named[d]]     # 近期完成的命名形态
    rs_cfg = cfg.get("RS") or {}                                                            # RS 相对强度 (vs 大盘)
    bench = load_benchmark(rs_cfg.get("基准", "000300.SH"))                                  # HS300 (L2 RS + L3 板块regime 共用)
    rs = relative_strength(ohlcv["date"], ohlcv["close"], bench,
                           window=rs_cfg.get("窗口", 20), band=rs_cfg.get("零轴死区", 0.005))
    rs_now = rs.get(last_date)
    money, turnover = load_capital(code)                                                    # 维度③ 资金+换手
    cap = capital_signals(ohlcv["date"], money, turnover, cfg=cfg)
    from services.technical_states.capital import capital_intent                             # 主力意图+量价背离(暗盘伪维度已砍)
    mingan = capital_intent(ohlcv["date"], money, cfg=cfg)
    mingan_now = mingan.get(last_date)
    close_by_date = {ohlcv["date"][j]: ohlcv["close"][j] for j in range(len(ohlcv["date"]))}
    chip = chip_signals(ohlcv["date"], load_cyq(code), close_by_date, cfg=cfg)              # 维度④ 筹码
    vol = volume_signals(ohlcv["date"], ohlcv["close"], ohlcv["volume"], cfg=cfg)           # L2 成交量/量能(量价配合)
    sec_mem = load_sector_membership(code, as_of=last_date)                                  # L3 板块/概念 (申万行业+东财概念, PIT)
    sec_kline = load_sector_kline(sec_mem["sw_l1_code"], end=last_date)                      # 申万一级行业指数日线 {iso:close}
    sec_dates = sorted(sec_kline)
    sec_reg = sector_regime(sec_dates, [sec_kline[d] for d in sec_dates], bench, cfg=cfg)    # L3 板块regime (vs HS300, 风口在不在)
    svs = relative_strength(ohlcv["date"], ohlcv["close"], sec_kline,                        # L2 个股vs板块 (领涨/落后, bench=行业指数)
                            window=rs_cfg.get("窗口", 20), band=rs_cfg.get("零轴死区", 0.005), bench_label="板块")
    sector = {"sw_l1_name": sec_mem["sw_l1_name"], "sw_l2_name": sec_mem["sw_l2_name"],
              "regime": sec_reg.get(last_date), "stock_vs_sector": svs.get(last_date),
              **concept_labels(sec_mem["concepts"], sec_mem["concept_hot"], cfg=cfg)}
    fund = fundamental_signals(load_fundamentals(code, as_of=last_date), cfg=cfg)             # L3④ 基本面 (ann_date PIT)
    forecast = forecast_signal(load_forecast(code, as_of=last_date), cfg=cfg)                 # L3④ 业绩预告前瞻 (ann_date PIT, PEAD 鱼头催化)
    cur_val, pe_hist, pb_hist = load_valuation(code, as_of=last_date)                         # L3④ 估值 (trade_date PIT)
    valuation = valuation_signals(cur_val, pe_hist, pb_hist, cfg=cfg)
    last_close = ohlcv["close"][-1] if ohlcv["close"] else None
    analyst = analyst_expectation(load_analyst_reports(code, as_of=last_date,                 # L3④ 分析师预期 (report_date PIT)
                                                       months=int((cfg.get("预期") or {}).get("研报窗口月", 6))),
                                  last_close, cfg=cfg)
    ev_days = int((cfg.get("事件") or {}).get("事件窗口日", 60))                              # L3⑤ 事件催化
    lhb_rows, inst_rows = load_lhb(code, as_of=last_date, days=ev_days)
    events = {"lhb": lhb_signal(lhb_rows, inst_rows, cfg=cfg),                                # 龙虎榜 (trade_date PIT)
              "block": block_signal(load_block_trade(code, as_of=last_date, days=ev_days),    # 大宗交易 (trade_date PIT)
                                    close_by_date, cfg=cfg),
              "unlock": unlock_signal(load_share_float(code, as_of=last_date), last_date, cfg=cfg)}  # 解禁 (ann_date锚 float_date前瞻)
    regime = load_market_regime(as_of=last_date)                                              # L3⑥ 市场regime (横切, 所有股共享)
    return {
        "code": code, "as_of": last_date,
        "timeframes": tf_read,
        "mtf_aligned": last.get("mtf_aligned"),
        "prior_trend": dlast.get("prior_trend"),                       # D4 前序趋势 (位置消歧上下文)
        "today_candle": today_candle,                                  # D5 当日单日K线形态
        "recent_patterns": recent_patterns,                            # D5b 近期命名形态(老鸭头等)
        "rs": rs_now,                                                  # RS 相对强度 (强于/弱于大盘, 超额KPI)
        "capital": cap.get(last_date),                                 # 维度③ 资金流向+换手率
        "mingan": mingan_now,                                          # 主力意图+量价背离(暗盘伪维度已砍, 见capital.py裁决)
        "chips": chip.get(last_date),                                  # 维度④ 筹码分布+胜率
        "vol": vol.get(last_date),                                     # L2 成交量/量能 (量比+量价配合)
        "sector": sector,                                              # L3 板块/概念 (申万行业regime+个股vs板块+东财概念热度)
        "fundamentals": fund,                                          # L3④ 基本面 (ROE/成长/毛利/负债, ann_date PIT)
        "forecast": forecast,                                          # L3④ 业绩预告前瞻 (PEAD 鱼头催化, ann_date PIT)
        "valuation": valuation,                                        # L3④ 估值 (PE/PB自身分位/股息/市值, trade_date PIT)
        "analyst": analyst,                                            # L3④ 分析师预期 (近6月研报评级/目标价上行空间, report_date PIT)
        "events": events,                                              # L3⑤ 事件催化 (龙虎榜/大宗trade_date PIT + 解禁float_date前瞻)
        "regime": regime,                                             # L3⑥ 市场regime (横切非单股: 大盘趋势+涨停情绪, stage-conditional最外层)
        "holders": load_top10_holders(code, as_of=last_date),          # L3 机构: 十大流通股东+动向(report_date带横线同last_date格式)
        "entropy": last.get("entropy"),
        "trend": trend_series(ohlcv, daily_cls),
        "tunables": list_tunables(cfg),
        "coupling_notes": notes,
    }


def _latest_daily_dominant(ohlcv: dict, cfg: dict):
    """单股最新日线主态 (供分布对比, 只算日线省算力)。"""
    tf = cfg["timeframes"]["daily"]
    feats = compute(ohlcv["date"], ohlcv["open"], ohlcv["high"], ohlcv["low"], ohlcv["close"], ohlcv["volume"],
                    timeframe="daily", resample_rule=tf.get("resample"), warmup=tf.get("warmup", 120),
                    windows=tf.get("windows"))
    if not feats:
        return None
    return classify_series(feats, cfg)[max(feats)]["dominant"]


def compare_distribution(overrides: dict, end: str | None = None, scan: int = 200) -> dict:
    """**全体分布对比 (盲点覆盖)**: 默认参数 vs 调整后参数, 扫 scan 只股, 每股日线特征算一次、双 config 分类,
    报每形态的股票数 默认→调整后 (Δ) + 翻转的股票 (移入/移出哪个形态)。
    用户调一个边界, 真实影响在全体层面(放宽阈值会否让形态涌入垃圾股), 非单股视觉能看出。
    """
    from collections import Counter
    cfg_def = load_config()
    cfg_mod, notes = _effective_cfg(overrides)
    codes = get_data_access().distinct_codes("kline_qfq", limit=scan)   # 扫描股票码清单 (SERVE 读层)
    # 全体分类只需最新主态 → 只载近窗 (warmup120+er40+缓冲), 不载2015+全史 (性能)
    recent_start = (date.today() - timedelta(days=600)).isoformat()
    def_c, mod_c = Counter(), Counter()
    flips = []
    n = 0
    for code in codes:
        ohlcv = load_one(code, end, start=recent_start)
        if not ohlcv:
            continue
        # 特征与 config 无关, 只算一次; 仅 state_scores 受 config 影响 → 双分类
        tf = cfg_def["timeframes"]["daily"]
        feats = compute(ohlcv["date"], ohlcv["open"], ohlcv["high"], ohlcv["low"], ohlcv["close"], ohlcv["volume"],
                        timeframe="daily", resample_rule=tf.get("resample"), warmup=tf.get("warmup", 120),
                        windows=tf.get("windows"))
        if not feats:
            continue
        last = max(feats)
        d_state = classify_series(feats, cfg_def)[last]["dominant"]
        m_state = classify_series(feats, cfg_mod)[last]["dominant"]
        def_c[d_state] += 1
        mod_c[m_state] += 1
        n += 1
        if d_state != m_state:
            flips.append({"code": code, "from": d_state, "to": m_state})
    states = list(cfg_def["状态"]) + [None]
    rows = [{"state": s or "过渡态", "default": def_c.get(s, 0), "modified": mod_c.get(s, 0),
             "delta": mod_c.get(s, 0) - def_c.get(s, 0)} for s in states]
    return {"scanned": n, "by_pattern": rows, "flips": flips[:60], "flip_count": len(flips),
            "coupling_notes": notes}


def screen_pattern(pattern: str, end: str | None = None, limit: int = 40,
                   overrides: dict | None = None, scan: int = 300) -> dict:
    """列出最新(≤end) 主态==pattern 的股票 + 各自 mini 趋势线。scan=扫描股票数上限(性能)。
    universe: price_kline_qfq_tushare 已过 universe 写入门 (排除股不在表), screen 集天然干净。
    """
    cfg, notes = _effective_cfg(overrides)
    codes = get_data_access().distinct_codes("kline_qfq", limit=scan)   # 扫描股票码清单 (SERVE 读层)
    recent_start = (date.today() - timedelta(days=600)).isoformat()   # 只需最新主态+mini趋势, 不载全史 (性能)
    hits = []
    for code in codes:
        ohlcv = load_one(code, end, start=recent_start)
        if not ohlcv:
            continue
        mtf, feats = _multi_tf(ohlcv, cfg)
        if not mtf:
            continue
        last = mtf[max(mtf)]
        if last.get("daily") == pattern:
            daily_cls = classify_series(feats["daily"], cfg)
            hits.append({"code": code, "sub": last.get("daily_sub"),
                         "mtf_aligned": last.get("mtf_aligned"),
                         "trend": trend_series(ohlcv, daily_cls, max_points=80)})
        if len(hits) >= limit:
            break
    return {"pattern": pattern, "desc": _desc(cfg, pattern), "scanned": len(codes),
            "count": len(hits), "stocks": hits, "coupling_notes": notes}
