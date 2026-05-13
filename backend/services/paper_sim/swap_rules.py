"""Paper Sim v2 — Swap 决策 (用户审过的"达成率"公式).

核心公式 (用户原话: "盈利目标和持仓周期的综合达成率"):

  盈利进度 P  = (current_price - entry_price) / (sell_target - entry_price)
                # 0 → 没开始; 1.0 → 已到目标; 1.5 → 超额
  时间进度 T  = days_held / optimal_hp
                # 0 → 刚买; 1.0 → 计划周期到; 1.5 → 超期
  达成率   F  = P / max(T, 0.1)
                # > 1 跑赢预期, < 1 跑输, < 0.5 严重落后, < 0 浮亏

时间维度 **绝不全局硬编码** — 用每只持仓买入时锁定的 Optuna optimal_hp.

Swap 真正触发要 3 AND 条件:
  1. 当前持仓达成率 < severe_threshold (默认 0.5)
  2. 候选 Y 在 A 剩余天数能贡献的进度 ≥ A 的落后 gap + tx_cost_buffer
     "能补上落后" — 用户原话
  3. 候选 Y tier == 'STRONG_BUY' (用户审过)
  + min_holding_days_before_swap 持仓天数门槛
  + max_swaps_per_day 单日 swap 上限 (anti-churn)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from services.paper_sim.config import SwapConfig


@dataclass(frozen=True)
class HoldingState:
    """持仓快照, 用于算达成率."""
    stock_code: str
    entry_price: float
    sell_target: float           # entry_price × (1 + optimal_target_pct)
    optimal_hp: int              # 买入时锁定的 Optuna optimal_hp
    days_held: int
    current_price: float
    score_at_entry: float        # 买入时的 综合 score
    current_score: Optional[float] = None   # 今日 score (可能 None 没出现在 daily_rec)


@dataclass(frozen=True)
class Candidate:
    """候选, 用于评估能不能补上落后."""
    stock_code: str
    tier: str                    # 'STRONG_BUY' / 'BUY' / 'WATCH'
    score: float
    expected_total_return: float # daily_position_recommendation 的 avg_ret / 预期收益
    optimal_hp: int              # 该候选自己的 Optuna optimal_hp


@dataclass(frozen=True)
class FulfillmentBreakdown:
    """达成率 + 落后差距 + 剩余 — 报告用."""
    pnl_progress: float
    time_progress: float
    fulfillment: float
    gap: float                   # T - P, 进度落后
    days_left: int               # max(optimal_hp - days_held, 0)


def compute_fulfillment(h: HoldingState) -> FulfillmentBreakdown:
    """对一只持仓算"达成率".

    edge cases:
      - sell_target ≤ entry_price (target 设置异常) → P 用 0, F 用 0 (强 swap 信号)
      - optimal_hp ≤ 0 (脏数据) → T 设 1, F = P
      - days_held = 0 → max(0, 0.1) 兜底防除零
    """
    if h.sell_target <= h.entry_price or h.entry_price <= 0:
        # 目标价不合法, 视为已严重落后, 鼓励 swap
        return FulfillmentBreakdown(
            pnl_progress=0.0, time_progress=1.0, fulfillment=0.0, gap=1.0, days_left=0
        )
    pnl = (h.current_price - h.entry_price) / (h.sell_target - h.entry_price)
    if h.optimal_hp <= 0:
        return FulfillmentBreakdown(pnl, 1.0, pnl, 1.0 - pnl, 0)
    time = h.days_held / h.optimal_hp
    fulfillment = pnl / max(time, 0.1)
    gap = max(0.0, time - pnl)   # 落后多少进度点
    days_left = max(h.optimal_hp - h.days_held, 0)
    return FulfillmentBreakdown(
        pnl_progress=pnl, time_progress=time,
        fulfillment=fulfillment, gap=gap, days_left=days_left,
    )


def candidate_can_close_gap(c: Candidate, fb: FulfillmentBreakdown, gap_buffer: float) -> bool:
    """候选 Y 在 A 剩余 days_left 天里, 能贡献的进度 ≥ A 的落后 gap + buffer?

    Y 单位时间进度密度 = 1 / Y.optimal_hp  (每天走 100/hp % 的进度)
    Y 在 days_left 天内能走 = days_left / Y.optimal_hp 的进度
    要求 ≥ gap + buffer.
    """
    if c.optimal_hp <= 0:
        return False
    if fb.days_left <= 0:
        return False  # A 已超期, swap 不如直接清掉 A 等新机会
    contribution = fb.days_left / c.optimal_hp
    return contribution >= fb.gap + gap_buffer


@dataclass(frozen=True)
class SwapDecision:
    should_swap: bool
    holding: Optional[HoldingState] = None
    candidate: Optional[Candidate] = None
    reason: str = ""
    fulfillment: Optional[float] = None
    gap: Optional[float] = None
    candidate_contribution: Optional[float] = None
    swap_uplift_estimate: float = 0.0   # 反事实净增益 (KPI B8 累加)


def evaluate_swap(
    holding: HoldingState,
    candidate: Candidate,
    cfg: SwapConfig,
) -> SwapDecision:
    """单 (持仓, 候选) 对的 swap 决策.

    返回 should_swap + 详细 reason. 调用方 (driver) 决定排优先级 (按 fulfillment
    严重程度排, 最严重的先 swap).
    """
    # 0. enabled
    if not cfg.enabled:
        return SwapDecision(False, holding, candidate, reason="swap_disabled")

    # 1. 持仓天数门槛 (A 股 T+1 + 用户配置 min_holding_days)
    if holding.days_held < cfg.min_holding_days_before_swap:
        return SwapDecision(
            False, holding, candidate,
            reason=f"min_holding_days({holding.days_held}<{cfg.min_holding_days_before_swap})"
        )

    # 2. 候选 tier 必须 STRONG_BUY
    if candidate.tier != "STRONG_BUY":
        return SwapDecision(
            False, holding, candidate,
            reason=f"candidate_tier({candidate.tier}!=STRONG_BUY)"
        )

    # 3. 达成率 < severe_threshold?
    fb = compute_fulfillment(holding)
    if fb.fulfillment >= cfg.severe_threshold:
        return SwapDecision(
            False, holding, candidate,
            reason=f"fulfillment({fb.fulfillment:.2f}>={cfg.severe_threshold})",
            fulfillment=fb.fulfillment, gap=fb.gap,
        )

    # 4. 候选能补上落后吗?
    if cfg.candidate_must_close_gap:
        if not candidate_can_close_gap(candidate, fb, cfg.gap_buffer_pct):
            contribution = fb.days_left / candidate.optimal_hp if candidate.optimal_hp > 0 else 0
            return SwapDecision(
                False, holding, candidate,
                reason=f"candidate_cant_close_gap(contribution={contribution:.2f}<gap+buffer={fb.gap+cfg.gap_buffer_pct:.2f})",
                fulfillment=fb.fulfillment, gap=fb.gap,
                candidate_contribution=contribution,
            )

    # 5. 通过 — 估算 uplift (用于 KPI B8 反事实)
    # 简化模型: 假设 A 留下来在剩余天数里只能补 fb.pnl_progress 比例的剩余目标 (= 维持当前速度);
    # 候选 Y 补 fb.days_left × (1/Y.optimal_hp) 的进度, 换算成 expected_return.
    # 反事实差额 ≈ (Y 贡献率 - A 维持率) × Y.expected_total_return - tx_cost_buffer
    contribution = fb.days_left / candidate.optimal_hp if candidate.optimal_hp > 0 else 0
    # A 维持率: 若 A 当前 P 已涨, 假设它继续以同速度跑; 若 fulfillment < 1 它继续低速
    a_maintain = max(0.0, fb.pnl_progress * (1 - fb.time_progress))   # 还能涨的部分
    y_contribution_to_ret = contribution * candidate.expected_total_return
    uplift = y_contribution_to_ret - a_maintain - cfg.gap_buffer_pct

    return SwapDecision(
        should_swap=True,
        holding=holding,
        candidate=candidate,
        reason=f"fulfillment={fb.fulfillment:.2f}<{cfg.severe_threshold}, gap={fb.gap:.2f}, Y_can_contribute={contribution:.2f}",
        fulfillment=fb.fulfillment,
        gap=fb.gap,
        candidate_contribution=contribution,
        swap_uplift_estimate=uplift,
    )


def rank_swap_candidates(
    holdings: list[HoldingState],
    candidates: list[Candidate],
    cfg: SwapConfig,
) -> list[SwapDecision]:
    """对所有 (持仓, 候选) 对算 swap, 返回 should_swap=True 的, 按 fulfillment 升序
    (最严重落后的优先 swap). 应用 max_swaps_per_day cap.
    """
    decisions: list[SwapDecision] = []
    # 避免重复用同一候选 — 同一只 stock 同一天只能被 swap_in 一次
    used_candidates: set[str] = set()
    # 持仓也只 swap_out 一次
    swapped_out: set[str] = set()

    # 先按持仓的 fulfillment 升序排 — 最严重落后的优先考虑换
    holdings_sorted = sorted(holdings, key=lambda h: compute_fulfillment(h).fulfillment)
    for h in holdings_sorted:
        if h.stock_code in swapped_out:
            continue
        if len(decisions) >= cfg.max_swaps_per_day:
            break
        # 候选按 score 降序
        for c in sorted(candidates, key=lambda x: -x.score):
            if c.stock_code in used_candidates or c.stock_code == h.stock_code:
                continue
            d = evaluate_swap(h, c, cfg)
            if d.should_swap:
                decisions.append(d)
                used_candidates.add(c.stock_code)
                swapped_out.add(h.stock_code)
                break
    return decisions
