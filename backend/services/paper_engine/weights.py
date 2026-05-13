"""Phase δ — rank → target_weight 派生。

mart_daily_recommendation 没有 weight_pct 列, paper engine 必须自己派生。

策略:
  - equal_weight: 1/N (N = top_k)
  - score_weighted: normalize(pred_score) (高分占比高)
  - rank_decay: 排名越高权重越大, exp(-rank/k)

返回: [{stock_code, target_weight}], sum=1.0 (剩余作为 cash 通过 constraint.cash_reserve 控制)
"""
from __future__ import annotations

import math
from typing import Any


def equal_weight(picks: list[dict], cash_reserve: float = 0.10) -> list[dict]:
    """top-K 等权: each weight = (1 - cash_reserve) / N。

    Args:
        picks: list of dict, 至少含 stock_code
        cash_reserve: 0-1, 保留现金占比 (默认 10%)
    """
    if not picks:
        return []
    n = len(picks)
    w = (1.0 - cash_reserve) / n
    return [{"stock_code": p["stock_code"], "target_weight": w} for p in picks]


def score_weighted(picks: list[dict], cash_reserve: float = 0.10) -> list[dict]:
    """按 pred_score 加权 (高分占比高)。"""
    if not picks:
        return []
    scores = [max(0.0, float(p.get("pred_score") or 0.0)) for p in picks]
    total = sum(scores)
    if total <= 0:
        return equal_weight(picks, cash_reserve)
    budget = 1.0 - cash_reserve
    return [
        {"stock_code": p["stock_code"], "target_weight": s / total * budget}
        for p, s in zip(picks, scores)
    ]


def rank_decay(picks: list[dict], cash_reserve: float = 0.10, halflife: int = 10) -> list[dict]:
    """排名衰减: w_i ∝ exp(-rank / halflife)。

    rank=1 权重最大, halflife=10 表示 rank=10 时权重折半。
    """
    if not picks:
        return []
    # 用 rank_in_date 或者 index+1 作排名
    ranks = [int(p.get("rank_in_date") or (i + 1)) for i, p in enumerate(picks)]
    raw = [math.exp(-r / halflife) for r in ranks]
    total = sum(raw)
    if total <= 0:
        return equal_weight(picks, cash_reserve)
    budget = 1.0 - cash_reserve
    return [
        {"stock_code": p["stock_code"], "target_weight": w / total * budget}
        for p, w in zip(picks, raw)
    ]


def derive_target_weights(
    picks: list[dict],
    method: str = "equal_weight",
    cash_reserve: float = 0.10,
) -> list[dict]:
    """主 entry. method ∈ {equal_weight, score_weighted, rank_decay}."""
    if method == "score_weighted":
        return score_weighted(picks, cash_reserve)
    if method == "rank_decay":
        return rank_decay(picks, cash_reserve)
    return equal_weight(picks, cash_reserve)
