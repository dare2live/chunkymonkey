"""dossier — 股票档案聚合服务 (form 维度解读器, 前端档案视图的后端桥接)。

owner=docs/stock_dossier_master_design.md (P2) + backend/services/technical_states/。
设计: 实现维度解读器协议的 form 维度 — interpret(单股多TF形态解读)/screen(列符合形态的股票)/series(趋势线非K线)。
后续维度(板块/资金/筹码)各加一个同协议模块, dossier 聚合层并列调用 (本文件先做 form 维度)。
PIT: 只用 ≤end 的 bar (load_kline end 截断); universe 排除股不进 screen 集 (assert_universe_clean)。
趋势线: 不画 K线蜡烛, 用 (date, close, 主态) 分段着色折线 + 降采样, 供前端轻量渲染。
"""
from __future__ import annotations

from datetime import date, timedelta

from services.data_loaders import MARKET_DB, RAW_DB
from services.duck_adapter import connect as duck_connect
from services.technical_states.candles import candle_pattern
from services.technical_states.context import apply_context
from services.technical_states.limits import code_to_ts_code, compute_limit_flags, enrich_features
from services.technical_states.patterns import match_named_patterns
from services.technical_states.capital import capital_signals
from services.technical_states.chips import chip_signals
from services.technical_states.rs import relative_strength
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
    where = f"code = ? AND date >= '{start}'" + (f" AND date <= '{end}'" if end else "")
    c = duck_connect(MARKET_DB, read_only=True)
    try:
        rows = c.execute(
            f"SELECT date, open, high, low, close, volume FROM price_kline_qfq_tushare "
            f"WHERE {where} ORDER BY date", [code]).fetchall()
    finally:
        c.close()
    if not rows:
        return None
    return {"date": [str(r[0]) for r in rows], "open": [r[1] for r in rows], "high": [r[2] for r in rows],
            "low": [r[3] for r in rows], "close": [r[4] for r in rows], "volume": [r[5] for r in rows]}


def load_limits(code: str) -> dict:
    """stk_limit (up/down_limit, A股涨跌停真相源) → {date_iso: (up_limit, down_limit)}。2022+ 才有。"""
    c = duck_connect(RAW_DB, read_only=True)
    try:
        rows = c.execute(
            "SELECT trade_date, up_limit, down_limit FROM raw_tushare_stk_limit WHERE ts_code = ? ORDER BY trade_date",
            [code_to_ts_code(code)]).fetchall()
    finally:
        c.close()
    out = {}
    for td, ul, dl in rows:
        td = str(td)
        iso = f"{td[:4]}-{td[4:6]}-{td[6:8]}" if (len(td) == 8 and "-" not in td) else td
        out[iso] = (ul, dl)
    return out


def _iso(td) -> str:
    """tushare YYYYMMDD → ISO date (复用; DRY)。"""
    td = str(td)
    return f"{td[:4]}-{td[4:6]}-{td[6:8]}" if (len(td) == 8 and "-" not in td) else td


def load_capital(code: str) -> tuple[dict, dict]:
    """**东财 moneyflow_dc 单一供应商** (维度③, 与项目 概念=东财 同源 口径自洽; flow-vendor=membership-vendor 红线)。
    → (money_by_date, turnover_by_date)。money_by_date={date:{net_amount(主力大单净, 万元), net_amount_rate(占成交额%), pct_change(涨跌%)}}。
    东财数据 2023-09 起 (前无资金, 档案描述近期为主)。明盘=net_amount; 量价背离=net_amount方向 vs pct_change (暗盘伪维度已砍, 见 capital.py 裁决)。
    """
    ts = code_to_ts_code(code)
    c = duck_connect(RAW_DB, read_only=True)
    try:
        dc = c.execute(
            "SELECT trade_date, net_amount, net_amount_rate, pct_change "
            "FROM raw_tushare_moneyflow_dc WHERE ts_code = ? ORDER BY trade_date", [ts]).fetchall()
        tr = c.execute("SELECT trade_date, turnover_rate FROM raw_tushare_daily_basic WHERE ts_code = ? ORDER BY trade_date", [ts]).fetchall()
    finally:
        c.close()
    money = {_iso(r[0]): {"net_amount": r[1], "net_amount_rate": r[2], "pct_change": r[3]} for r in dc}
    return (money, {_iso(td): v for td, v in tr})


def load_cyq(code: str) -> dict:
    """cyq_perf 筹码分布/胜率 (维度④) → {date_iso: {winner_rate, cost_5/50/95pct, weight_avg}}。"""
    ts = code_to_ts_code(code)
    c = duck_connect(RAW_DB, read_only=True)
    try:
        rows = c.execute("SELECT trade_date, winner_rate, cost_5pct, cost_50pct, cost_95pct, weight_avg "
                         "FROM raw_tushare_cyq_perf WHERE ts_code = ? ORDER BY trade_date", [ts]).fetchall()
    finally:
        c.close()
    return {_iso(td): {"winner_rate": wr, "cost_5pct": c5, "cost_50pct": c50, "cost_95pct": c95, "weight_avg": wa}
            for td, wr, c5, c50, c95, wa in rows}


def load_top10_holders(code: str, as_of: str | None = None) -> dict | None:
    """L3 机构维度: 十大**流通**股东 (free) 最近季 + 本季动向 (新进/增持/减持/退出)。
    真相源: smartmoney.fact_top10_holder_period (tdx F10, 2017Q4+, 含持股占比/变化/进出标记)。
    PIT: 取 report_date <= as_of 的最近季 (季报披露滞后, 描述用最近季)。机构跟随=用户最初设想的跟随策略基础。
    """
    from services.data_loaders import SMARTMONEY_DB
    from collections import Counter
    asof_norm = as_of.replace("-", "") if as_of else None    # 数据坑: report_date 格式不统一(带/不带横线), 规范化 YYYYMMDD 比较
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
        "report_date": mx,
        "holders": [{"rank": r[0], "name": r[1], "ratio": r[2], "change": r[3], "change_num": r[4]} for r in cur],
        "新进": cnt.get("新进", 0), "退出": cnt.get("退出", 0), "增持": cnt.get("增持", 0), "减持": cnt.get("减持", 0),
        "机构动向": "加仓" if net > 0 else "减仓" if net < 0 else "持平",
    }


def load_benchmark(ts_code: str) -> dict:
    """基准指数日线 (RS 用; 如 HS300 000300.SH) → {date_iso: close}。"""
    c = duck_connect(RAW_DB, read_only=True)
    try:
        rows = c.execute("SELECT trade_date, close FROM raw_tushare_index_daily WHERE ts_code = ? ORDER BY trade_date",
                         [ts_code]).fetchall()
    finally:
        c.close()
    out = {}
    for td, cl in rows:
        td = str(td)
        iso = f"{td[:4]}-{td[4:6]}-{td[6:8]}" if (len(td) == 8 and "-" not in td) else td
        out[iso] = cl
    return out


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
    rs = relative_strength(ohlcv["date"], ohlcv["close"], load_benchmark(rs_cfg.get("基准", "000300.SH")),
                           window=rs_cfg.get("窗口", 20), band=rs_cfg.get("零轴死区", 0.005))
    rs_now = rs.get(last_date)
    money, turnover = load_capital(code)                                                    # 维度③ 资金+换手
    cap = capital_signals(ohlcv["date"], money, turnover, cfg=cfg)
    from services.technical_states.capital import capital_intent                             # 主力意图+量价背离(暗盘伪维度已砍)
    mingan = capital_intent(ohlcv["date"], money, cfg=cfg)
    mingan_now = mingan.get(last_date)
    close_by_date = {ohlcv["date"][j]: ohlcv["close"][j] for j in range(len(ohlcv["date"]))}
    chip = chip_signals(ohlcv["date"], load_cyq(code), close_by_date, cfg=cfg)              # 维度④ 筹码
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
    c = duck_connect(MARKET_DB, read_only=True)
    try:
        codes = [r[0] for r in c.execute(
            "SELECT DISTINCT code FROM price_kline_qfq_tushare ORDER BY code LIMIT ?", [scan]).fetchall()]
    finally:
        c.close()
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
    c = duck_connect(MARKET_DB, read_only=True)
    try:
        codes = [r[0] for r in c.execute(
            "SELECT DISTINCT code FROM price_kline_qfq_tushare ORDER BY code LIMIT ?", [scan]).fetchall()]
    finally:
        c.close()
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
