"""technical_states.candles — 单日 K 线构件 (审查 keep: prior_trend 消歧 + A股一字板特判)。

单根 K 线解剖 (开高低收几何) → 命名构件。位置消歧: 锤子线(下跌末)与上吊线(上涨末)几何同形,
靠前序趋势区分。按裁决修正:
- H3 联动: 一字板跌停分支经 limits 双侧修复后真实可达 (旧 limits 只涨停侧 → 此处死代码);
- low: prior_trend 缺省 (None) → 返回几何中性名 (长下影线/长上影线), 不再隐性押注空头方向;
- medium: cfg 必传, 删内置默认 (双真相源禁止); docstring 幻影返回值 '普通' 已删 (默认=纺锤线)。
定位 = 描述构件/可解释标签, 非独立 alpha 信号。纯函数, 无 DB, 阈值全 config。
"""
from __future__ import annotations


def candle_pattern(o, h, l, c, prior_trend: str | None = None,
                   is_up_limit: bool = False, is_down_limit: bool = False,
                   is_one_word: bool = False, *, cfg: dict) -> str:
    """单根 K 线构件名。prior_trend ∈ {升, 平, 跌, None}; is_* = limits flags (A股特判)。

    返回: 一字板涨停/一字板跌停/一字线/大阳线/大阴线/十字星/锤子线/上吊线/倒锤线/流星线/
    长下影线/长上影线/纺锤线/未知。
    """
    p = cfg["单日形态"]                                   # 缺节 → KeyError fail loud (无内置默认)
    if any(x is None for x in (o, h, l, c)):
        return "未知"
    # A股一字板特判 (开高低收近重合 + 触板) — 排除假十字星; H3 后跌停侧可达
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
    if body_pct < p["十字实体上限"]:
        return "十字星"
    if dn_shadow > k * body and up_shadow < body:
        if prior_trend == "跌":
            return "锤子线"
        return "上吊线" if prior_trend == "升" else "长下影线"   # 无前序趋势 → 几何中性名
    if up_shadow > k * body and dn_shadow < body:
        if prior_trend == "跌":
            return "倒锤线"
        return "流星线" if prior_trend == "升" else "长上影线"
    if body_pct > p["长实体下限"]:
        return "大阳线" if c > o else "大阴线"
    return "纺锤线"
