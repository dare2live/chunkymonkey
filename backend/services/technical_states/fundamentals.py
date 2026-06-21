"""technical_states.fundamentals — 基本面/估值/分析师预期 (档案 L3 属性背景, ④)。

真相源 (PIT 锚见各 loader; dossier 服务层 load):
  raw_tushare_fina_indicator (财务指标: roe/增长yoy/利润率/负债) — 锚 **ann_date 公告日** (非 end_date 报告期,
    防财报 leakage 红线: 时刻 t 只知已公告财报, mythos §3/§8 公告类 PIT 锚 ann_date)。
  raw_tushare_daily_basic (估值: pe_ttm/pb/ps_ttm/dv_ttm/市值) — 锚 trade_date (当日盘后即知, 无前瞻)。
  raw_tushare_report_rc (券商研报: rating 评级/tp 目标价) — 锚 report_date (研报发布日)。
三层定稿: daily_basic 按字段拆 — 换手/量比→L2(capital), pe/pb/ps/市值→L3 估值 (防估值背书误当形态确认=leakage)。
诚实标记: A股研报正向偏 (买入/增持占 60%+, 卖出极罕见 → 评级占比判别力弱, 辅以目标价上行空间);
  imp_dg 字段全 NULL → 不做评级调整 (measured-not-estimated: 测不出不伪造)。roe 跨报告期不可比 → 用 roe_yearly(年化)。
描述性档案维度, 非端到端AI/无条件截面 alpha (撞 R1 墙); 基本面×机构跟随=stage-conditional 留选股层。纯函数。
"""
from __future__ import annotations


def fundamental_signals(fina: dict | None, *, cfg=None) -> dict | None:
    """基本面: roe_yearly(盈利能力) + netprofit_yoy/or_yoy(成长性) + 毛利率/负债率(质量)。
    fina=最新 ann_date 财报指标 dict。综合评: 优质(盈利优良且不下滑) / 一般 / 偏弱(盈利弱或下滑)。
    """
    if not fina:
        return None
    c = (cfg or {}).get("基本面") or {}
    roe_good = c.get("ROE优秀门", 15.0)      # 年化 ROE% > 此 = 优秀
    roe_ok = c.get("ROE良好门", 8.0)
    growth_high = c.get("高成长门", 30.0)    # 归母净利同比% > 此 = 高成长
    roe = fina.get("roe_yearly")
    if roe is None:
        roe = fina.get("roe")                # 年化缺则用 YTD roe (注: 报告期不可比, 仅 fallback)
    npy, ory = fina.get("netprofit_yoy"), fina.get("or_yoy")
    profit = ("优秀" if (roe is not None and roe >= roe_good)
              else "良好" if (roe is not None and roe >= roe_ok)
              else "偏弱" if roe is not None else "未知")
    growth = ("高成长" if (npy is not None and npy >= growth_high)
              else "成长" if (npy is not None and npy > 0)
              else "下滑" if npy is not None else "未知")
    if profit in ("优秀", "良好") and growth != "下滑":
        verdict = "优质"
    elif profit == "偏弱" or growth == "下滑":
        verdict = "偏弱"
    else:
        verdict = "一般"
    return {"报告期": fina.get("end_date"), "ROE": round(roe, 2) if roe is not None else None,
            "盈利能力": profit, "净利增长": round(npy, 1) if npy is not None else None,
            "营收增长": round(ory, 1) if ory is not None else None, "成长性": growth,
            "毛利率": round(fina["grossprofit_margin"], 1) if fina.get("grossprofit_margin") is not None else None,
            "负债率": round(fina["debt_to_assets"], 1) if fina.get("debt_to_assets") is not None else None,
            "基本面评": verdict}


def _pctile(hist, cur, min_n: int = 60) -> float | None:
    """cur 在 hist (剔 None/<=0 的无效估值) 中的历史分位 (0-100%); 样本 < min_n 返回 None。"""
    vals = [v for v in hist if v is not None and v > 0]
    if cur is None or cur <= 0 or len(vals) < min_n:
        return None
    return round(100.0 * sum(1 for v in vals if v <= cur) / len(vals), 1)


def valuation_signals(cur: dict | None, pe_hist, pb_hist, *, cfg=None) -> dict | None:
    """估值: pe_ttm/pb 自身历史分位 (低估<低门/高估>高门) + 股息率 + 市值。
    cur=最新 daily_basic dict, *_hist=≤t 历史序列 (PIT)。估值状态以 PE 分位为主, PE 缺/亏损用 PB。
    """
    if not cur:
        return None
    c = (cfg or {}).get("估值") or {}
    low = c.get("低估分位门", 30.0)         # 自身历史分位 < 此 = 低估
    high = c.get("高估分位门", 70.0)
    min_n = int(c.get("分位最小样本", 60))   # yaml-back (§3): 分位最小样本门
    pe, pb = cur.get("pe_ttm"), cur.get("pb")
    pe_pct, pb_pct = _pctile(pe_hist, pe, min_n), _pctile(pb_hist, pb, min_n)
    pct = pe_pct if pe_pct is not None else pb_pct
    state = ("低估" if (pct is not None and pct < low)
             else "高估" if (pct is not None and pct > high)
             else "合理" if pct is not None else "未知")
    mv = cur.get("total_mv")                 # 万元
    return {"PE": round(pe, 1) if pe is not None else None, "PE分位": pe_pct,
            "PB": round(pb, 2) if pb is not None else None, "PB分位": pb_pct,
            "股息率": round(cur["dv_ttm"], 2) if cur.get("dv_ttm") is not None else None,
            "市值亿": round(mv / 1e4, 0) if mv is not None else None,    # 万元 → 亿元
            "估值状态": state}


# A股正向评级词 fallback (cfg 缺时用; 正式词表走 technical_states.yaml 预期.正向评级词, §3 数据化)。
# casefold 子串匹配 (Buy/BUY/Outperform 大写变体 + 繁体買進 均命中); 卖出/减持/中性/持有 不在此=非正向。
_POS_RATINGS = ("买入", "增持", "推荐", "强烈推荐", "强推", "审慎增持", "谨慎增持", "审慎推荐",
                "谨慎推荐", "跑赢行业", "优于大市", "强于大市", "买进", "強力買進", "買進",
                "buy", "outperform", "overweight", "add", "increase")


def analyst_expectation(reports, cur_price, *, cfg=None) -> dict | None:
    """分析师预期: 近 N 月研报 正向评级占比 + 目标价上行空间。reports=[(report_date, rating, tp)] (均 ≤t)。
    A股研报正向偏 → 评级倾向判别力弱 (辅以上行空间); tp 常为 0/缺 → graceful 跳过; imp_dg 全 NULL 不做评级调整。
    """
    if not reports:
        return None
    c = (cfg or {}).get("预期") or {}
    bull_thr = c.get("看好占比门", 0.7)      # 正向占比 >= 此 = 看好 (A股普遍正向, < 此=罕见分歧信号)
    band_lo = c.get("目标价合理带下", 0.1)   # yaml-back (§3): 目标价 sane band
    band_hi = c.get("目标价合理带上", 10.0)
    pos_words = [str(w).casefold() for w in (c.get("正向评级词") or _POS_RATINGS)]
    n = len(reports)
    pos = sum(1 for _, rt, _ in reports if rt and any(p in str(rt).casefold() for p in pos_words))  # casefold 抗大小写
    pos_ratio = pos / n if n else None
    # 目标价: 用中位数(抗数据错 outlier, 如 tp 单位异常行) + sane band 滤明显错值(现价倍数)
    tps = [tp for _, _, tp in reports if tp and tp > 0
           and (not cur_price or band_lo * cur_price <= tp <= band_hi * cur_price)]
    tps.sort()
    med_tp = (tps[len(tps) // 2] if len(tps) % 2 else (tps[len(tps) // 2 - 1] + tps[len(tps) // 2]) / 2.0) if tps else None
    upside = ((med_tp / cur_price - 1.0) * 100.0) if (med_tp and cur_price) else None
    tilt = ("看好" if (pos_ratio is not None and pos_ratio >= bull_thr)
            else "分歧" if pos_ratio is not None else "未知")
    return {"研报数": n, "正向占比": round(pos_ratio * 100, 0) if pos_ratio is not None else None,
            "目标价中位": round(med_tp, 1) if med_tp is not None else None,
            "上行空间": round(upside, 1) if upside is not None else None, "评级倾向": tilt}
