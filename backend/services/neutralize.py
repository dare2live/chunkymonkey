"""中性化 — P1.6 (2026-04-28).

把"裸 alpha"剥离行业/市值红利, 得到"纯 alpha":
- neutralize_by_group(scores, group): 减组均值 (行业中性化)
- neutralize_by_quintile(scores, x, n_bins=5): x 分箱后组内减均值 (市值中性化)
- neutralize(scores, industry, market_cap): 串联 industry → market_cap

数据流:
- 输入: pd.Series (index=stock_code, value=raw_score)
- 输出: pd.Series (index=stock_code, value=neutral_score)

简化版 (不引入 statsmodels OLS):
- 行业中性化 = group_by(industry).demean
- 市值中性化 = pd.qcut(market_cap, 5).groupby.demean
- 同时去做 = 先行业 demean, 再市值 demean

后续可改 OLS regression (alpha ~ industry_dummies + log(market_cap)).
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("cm-api.neutralize")


def neutralize_by_group(scores, groups):
    """按 groups 分组, 减组均值. scores/groups 都是 pd.Series, index 一致."""
    import pandas as pd
    if not isinstance(scores, pd.Series):
        scores = pd.Series(scores)
    if not isinstance(groups, pd.Series):
        groups = pd.Series(groups)
    aligned = pd.DataFrame({"score": scores, "group": groups}).dropna(subset=["score"])
    if aligned.empty:
        return pd.Series(dtype=float)
    group_mean = aligned.groupby("group")["score"].transform("mean")
    return aligned["score"] - group_mean


def neutralize_by_quintile(scores, x, n_bins: int = 5):
    """按 x 分 n_bins 个分位组, 组内减均值."""
    import pandas as pd
    if not isinstance(scores, pd.Series):
        scores = pd.Series(scores)
    if not isinstance(x, pd.Series):
        x = pd.Series(x)
    aligned = pd.DataFrame({"score": scores, "x": x}).dropna()
    if aligned.empty:
        return pd.Series(dtype=float)
    try:
        # qcut 失败 (重复值多) → cut 等距
        bins = pd.qcut(aligned["x"], q=n_bins, labels=False, duplicates="drop")
    except Exception:
        bins = pd.cut(aligned["x"], bins=n_bins, labels=False)
    aligned["bin"] = bins
    group_mean = aligned.groupby("bin")["score"].transform("mean")
    return aligned["score"] - group_mean


def neutralize(scores, industry=None, market_cap=None, market_cap_bins: int = 5):
    """组合中性化: 先行业 demean, 再市值 quintile demean. 任一缺失则跳过该步."""
    import pandas as pd
    if not isinstance(scores, pd.Series):
        scores = pd.Series(scores)
    out = scores.copy()
    if industry is not None:
        out = neutralize_by_group(out, industry).reindex(out.index, fill_value=None)
    if market_cap is not None:
        out = neutralize_by_quintile(out, market_cap, n_bins=market_cap_bins).reindex(out.index, fill_value=None)
    return out


def standardize(scores):
    """z-score 标准化. (x - mean) / std."""
    import pandas as pd
    if not isinstance(scores, pd.Series):
        scores = pd.Series(scores)
    s = scores.dropna()
    if s.empty:
        return scores
    sd = s.std(ddof=1)
    if not sd or sd == 0:
        return scores - s.mean()
    return (scores - s.mean()) / sd
