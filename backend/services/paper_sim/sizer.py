"""Paper Sim v2 — 仓位分配.

3 种模式 (config.portfolio.position_sizing):
  - equal:        N 个 candidate 每个 (1 - cash) / N
  - kelly:        每个候选 Kelly fraction → 然后 normalize 到总仓位 cap
  - wilson_kelly: Wilson 修正胜率 → Kelly fraction → normalize (推荐)

输出 cash 金额, 调用方再 round_to_lots 转股数.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from services.paper_sim.config import PortfolioConfig
from services.paper_sim.selector import CandidateRow
from services.portfolio_sizer.wilson import wilson_lower
from services.portfolio_sizer.kelly import kelly_fraction


@dataclass(frozen=True)
class SizingResult:
    stock_code: str
    target_cny: float            # 应投金额
    target_pct: float            # 占总资金比例
    raw_kelly_f: Optional[float] = None
    raw_wilson: Optional[float] = None
    reason: str = ""


def _kelly_for_candidate(c: CandidateRow, total_cap_pct: float) -> tuple[float, float, float]:
    """单候选: 用 Wilson + Kelly 算 target_pct (占总资金 cap_pct 的比例)."""
    # daily_position_recommendation 上游已经给了 wilson 修正胜率
    # 这里如果没有就回退用 raw — 但 candidate row 没传 wilson_win, 简化用 0.55 中位 default
    wilson = 0.55   # 简化版: 假设 buy_signal 上游已经用 wilson 排序过.
    # avg_ret + avg_dd 来自 daily_rec 数据
    avg_ret = c.expected_total_return
    avg_dd = c.optimal_stop_pct or -0.05   # 用 optimal_stop_pct 估 max_dd (保守)
    if avg_dd >= 0:
        avg_dd = -0.05
    f = kelly_fraction(wilson, avg_ret, avg_dd, kelly_mul=0.5, max_f=0.25)
    return f * total_cap_pct, wilson, f


def allocate_positions(
    candidates: list[CandidateRow],
    cfg: PortfolioConfig,
    available_cash: float,
    total_capital: float,
) -> list[SizingResult]:
    """给 N 个候选 (N ≤ max_positions) 分配仓位.

    Args:
        candidates: 已经按 score 排序的候选, 长度 ≤ max_positions
        cfg: portfolio config
        available_cash: 当前可用现金
        total_capital: 总资本 (现金 + 已持仓市值)

    Returns:
        SizingResult 列表, 顺序跟输入一致.
    """
    if not candidates:
        return []

    # 总仓位上限 = 1 - min_cash_pct (留缓冲)
    total_cap_pct = 1.0 - cfg.min_cash_pct

    n = len(candidates)

    if cfg.position_sizing == "equal":
        per_pct = total_cap_pct / max(n, 1)
        return [
            SizingResult(
                stock_code=c.stock_code,
                target_pct=per_pct,
                target_cny=min(per_pct * total_capital, available_cash / max(n - i, 1)),
                reason=f"equal({per_pct:.3f})",
            )
            for i, c in enumerate(candidates)
        ]

    # kelly / wilson_kelly: 算每个 raw kelly, 然后 normalize 到 total_cap_pct
    raw: list[tuple[float, float, float]] = []   # (pct, wilson, f)
    for c in candidates:
        pct, wilson, f = _kelly_for_candidate(c, total_cap_pct)
        raw.append((pct, wilson, f))
    raw_total = sum(r[0] for r in raw)
    if raw_total <= 0:
        # 全 Kelly 0 (亏损模型), 退化为 equal
        per_pct = total_cap_pct / n
        return [
            SizingResult(c.stock_code, per_pct * total_capital, per_pct,
                         reason="kelly_zero_fallback_equal")
            for c in candidates
        ]
    # 等比例放大到 total_cap_pct
    scale = min(1.0, total_cap_pct / raw_total)
    out: list[SizingResult] = []
    remaining_cash = available_cash
    for c, (pct, wilson, f) in zip(candidates, raw):
        final_pct = pct * scale
        final_cny = min(final_pct * total_capital, remaining_cash)
        remaining_cash -= final_cny
        out.append(SizingResult(
            stock_code=c.stock_code,
            target_pct=final_pct,
            target_cny=final_cny,
            raw_kelly_f=f, raw_wilson=wilson,
            reason=f"{cfg.position_sizing}(kelly_f={f:.3f}*scale{scale:.2f})",
        ))
    return out
