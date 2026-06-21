"""technical_states.candles — 单日 K 线形态 (D5, 用户点名单日裸K线识别)。

owner=backend/services/technical_states/ + config/technical_states.yaml 单日形态 段。
单根 K 线解剖 (开高低收几何) → 命名形态。**位置消歧**(评审): 锤子线(下跌末看多)与上吊线(上涨末看空)
  几何同形, 全靠前序趋势区分 — 不带位置就是张冠李戴。**A股特判**(评审): 涨跌停一字板 开高低收近重合
  会被误判十字星(实为无交易停滞), 须特判排除。定位=短期组合构件/可解释标签, 非独立 alpha 信号(信息稀疏)。
纯函数 (无 DB)。阈值全 config。
"""
from __future__ import annotations

_DEFAULTS = {"十字实体上限": 0.1, "长实体下限": 0.7, "长影倍数": 2.0, "无波动幅度": 0.005}


def candle_pattern(o, h, l, c, prev_c=None, prior_trend=None,
                   is_up_limit=False, is_down_limit=False, is_one_word=False, cfg=None) -> str:
    """单根 K 线形态 (人话命名)。prior_trend(升/平/跌) 做位置消歧; is_*limit/one_word=A股特判。
    返回: 一字板涨停/一字板跌停/大阳线/大阴线/十字星/锤子线/上吊线/倒锤线/流星线/纺锤线/普通。
    """
    p = {**_DEFAULTS, **((cfg or {}).get("单日形态") or {})}
    if any(x is None for x in (o, h, l, c)):
        return "未知"
    # A股一字板特判 (开高低收近重合 + 触板) — 排除假十字星
    if is_one_word:
        return "一字板涨停" if is_up_limit else "一字板跌停" if is_down_limit else "一字线"
    rng = h - l
    if c <= 0 or rng <= c * p["无波动幅度"]:
        return "一字线"
    body = abs(c - o)
    body_pct = body / rng
    up_shadow = h - max(o, c)
    dn_shadow = min(o, c) - l
    k = p["长影倍数"]
    # 十字星 (实体极小)
    if body_pct < p["十字实体上限"]:
        return "十字星"
    # 单边长影 (位置消歧)
    if dn_shadow > k * body and up_shadow < body:
        return "锤子线" if prior_trend == "跌" else "上吊线"        # 同形, 前序定多空
    if up_shadow > k * body and dn_shadow < body:
        return "倒锤线" if prior_trend == "跌" else "流星线"
    # 长实体 (光头光脚大阳/大阴)
    if body_pct > p["长实体下限"]:
        return "大阳线" if c > o else "大阴线"
    return "纺锤线"
