"""Phase δ D2 — 决策结果 (mart_decision_outcome) 计算。

对每个 BUY 决策, 算后续 5/10/30 日 forward return + max drawdown + outcome 分类。

输入: kline_lookahead_fn(stock_code, base_date, n_days) → list[close]
输出: dict 含 fwd_ret_5/10/30, fwd_max_dd_30, outcome_5/10/30
"""
from __future__ import annotations


WIN_THRESHOLD  = 0.02   # +2% 视为 win
LOSS_THRESHOLD = -0.02  # -2% 视为 loss


def classify_outcome(ret: float | None) -> str:
    """ret → win/loss/flat/active."""
    if ret is None:
        return "active"
    if ret >= WIN_THRESHOLD:
        return "win"
    if ret <= LOSS_THRESHOLD:
        return "loss"
    return "flat"


def compute_forward_returns(
    entry_price: float,
    future_closes: list[float | None],
) -> dict:
    """从 entry_price + future closes (按日期递增) 算 fwd_ret_N + max_dd。

    Args:
        entry_price: 入场价
        future_closes: 从 D+1 开始的连续 close 列表 (None 表示停牌/无数据)

    Returns:
        {fwd_ret_5d, fwd_ret_10d, fwd_ret_30d, fwd_max_dd_30d}
    """
    if entry_price <= 0:
        return {"fwd_ret_5d": None, "fwd_ret_10d": None,
                "fwd_ret_30d": None, "fwd_max_dd_30d": None}

    def _ret_at(n_days: int) -> float | None:
        idx = n_days - 1  # D+1 是 index 0
        if idx >= len(future_closes):
            return None
        close = future_closes[idx]
        if close is None or close <= 0:
            return None
        return close / entry_price - 1

    # 30 日内 max drawdown (从 entry_price 算起)
    valid_closes = [c for c in future_closes[:30] if c is not None and c > 0]
    if valid_closes:
        peak = entry_price
        max_dd = 0.0
        for c in valid_closes:
            peak = max(peak, c)
            dd = (c - peak) / peak if peak > 0 else 0.0
            max_dd = min(max_dd, dd)
        fwd_max_dd_30d = max_dd
    else:
        fwd_max_dd_30d = None

    return {
        "fwd_ret_5d":  _ret_at(5),
        "fwd_ret_10d": _ret_at(10),
        "fwd_ret_30d": _ret_at(30),
        "fwd_max_dd_30d": fwd_max_dd_30d,
    }


def build_decision_outcome(
    *,
    decision_date: str,
    stock_code: str,
    entry_price: float,
    rank_in_date: int | None,
    pred_score: float | None,
    primary_formula_id: str | None,
    industry_l1: str | None,
    future_closes: list[float | None],
    model_id: str = "paper_v1",
) -> dict:
    """组装 mart_decision_outcome 一行。"""
    fwd = compute_forward_returns(entry_price, future_closes)
    return {
        "decision_date":     decision_date,
        "stock_code":        stock_code,
        "model_id":          model_id,
        "decision_type":     "BUY",
        "rank_in_date":      rank_in_date,
        "pred_score":        pred_score,
        "primary_formula_id": primary_formula_id,
        "industry_l1":       industry_l1,
        "entry_price":       entry_price,
        "fwd_ret_5d":        fwd["fwd_ret_5d"],
        "fwd_ret_10d":       fwd["fwd_ret_10d"],
        "fwd_ret_30d":       fwd["fwd_ret_30d"],
        "fwd_max_dd_30d":    fwd["fwd_max_dd_30d"],
        "outcome_5d":        classify_outcome(fwd["fwd_ret_5d"]),
        "outcome_10d":       classify_outcome(fwd["fwd_ret_10d"]),
        "outcome_30d":       classify_outcome(fwd["fwd_ret_30d"]),
    }
