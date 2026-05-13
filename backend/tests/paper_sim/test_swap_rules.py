"""Paper Sim v2 — swap_rules 核心达成率公式 + 能补差 + 排序测试.

这是用户审过的最关键模块, 测试要覆盖:
  - 达成率公式 (盈利进度/时间进度)
  - 3 AND 条件 (severe + close_gap + STRONG_BUY)
  - min_holding_days 门槛
  - max_swaps_per_day 上限
  - 排序: 最严重落后的优先 swap
  - edge cases: 目标价异常 / hp=0 / days_left=0
"""
from __future__ import annotations

import pytest

from services.paper_sim.config import load_config
from services.paper_sim.swap_rules import (
    HoldingState, Candidate, FulfillmentBreakdown,
    compute_fulfillment, candidate_can_close_gap,
    evaluate_swap, rank_swap_candidates,
)


@pytest.fixture
def swap_cfg():
    return load_config().swap


# ============ compute_fulfillment 公式 ============

def test_fulfillment_exactly_on_track():
    """时间走 50% 盈利也走 50% → 达成率 = 1.0 (完美按计划)."""
    h = HoldingState("000001", entry_price=10, sell_target=13,
                      optimal_hp=30, days_held=15, current_price=11.5,
                      score_at_entry=80)
    fb = compute_fulfillment(h)
    assert fb.pnl_progress == pytest.approx(0.5)
    assert fb.time_progress == pytest.approx(0.5)
    assert fb.fulfillment == pytest.approx(1.0)


def test_fulfillment_ahead_of_schedule():
    """时间 17% 盈利 67% → 达成率 ~ 4 (跑赢)."""
    h = HoldingState("000001", 10, 13, 30, 5, 12.0, 80)
    fb = compute_fulfillment(h)
    assert fb.pnl_progress == pytest.approx(2/3)
    assert fb.time_progress == pytest.approx(5/30)
    assert fb.fulfillment > 3.0


def test_fulfillment_severely_behind():
    """时间 83% 盈利 17% → 达成率 ~ 0.2 (严重落后, 触发 swap)."""
    h = HoldingState("000001", 10, 13, 30, 25, 10.5, 80)
    fb = compute_fulfillment(h)
    assert fb.pnl_progress == pytest.approx(0.5/3)
    assert fb.time_progress == pytest.approx(25/30)
    assert fb.fulfillment < 0.3
    assert fb.days_left == 5


def test_fulfillment_handles_zero_hp():
    h = HoldingState("000001", 10, 13, 0, 0, 11, 80)
    fb = compute_fulfillment(h)
    # optimal_hp ≤ 0 → time=1, fulfillment=pnl
    assert fb.time_progress == 1.0


def test_fulfillment_target_below_entry():
    """target < entry (数据异常) → 强 swap 信号."""
    h = HoldingState("000001", 10, 9, 30, 5, 10, 80)
    fb = compute_fulfillment(h)
    assert fb.fulfillment == 0.0
    assert fb.gap == 1.0


# ============ candidate_can_close_gap ============

def test_candidate_can_close_gap_short_hp_wins():
    """候选 Y hp=5 days, A 剩 5 days → Y 能贡献 1.0 进度. gap=0.66 → 能补."""
    fb = FulfillmentBreakdown(0.17, 0.83, 0.20, gap=0.66, days_left=5)
    y = Candidate("Y", "STRONG_BUY", score=85, expected_total_return=0.30, optimal_hp=5)
    assert candidate_can_close_gap(y, fb, gap_buffer=0.0035) is True


def test_candidate_cant_close_gap_long_hp_loses():
    """候选 Y hp=20 days, A 剩 5 days → Y 能贡献 0.25 进度. gap=0.66 → 不够."""
    fb = FulfillmentBreakdown(0.17, 0.83, 0.20, gap=0.66, days_left=5)
    y = Candidate("Y", "STRONG_BUY", score=85, expected_total_return=0.30, optimal_hp=20)
    assert candidate_can_close_gap(y, fb, gap_buffer=0.0035) is False


def test_candidate_zero_hp_rejected():
    fb = FulfillmentBreakdown(0.17, 0.83, 0.20, 0.66, 5)
    y = Candidate("Y", "STRONG_BUY", 85, 0.30, optimal_hp=0)
    assert candidate_can_close_gap(y, fb, 0.0035) is False


# ============ evaluate_swap 全规则 ============

def test_swap_blocked_when_disabled(swap_cfg):
    from dataclasses import replace
    cfg = replace(swap_cfg, enabled=False)
    h = HoldingState("000001", 10, 13, 30, 25, 10.5, 80)
    y = Candidate("Y", "STRONG_BUY", 85, 0.30, 5)
    d = evaluate_swap(h, y, cfg)
    assert d.should_swap is False
    assert "disabled" in d.reason


def test_swap_blocked_by_min_holding_days(swap_cfg):
    """min_holding_days_before_swap=1 — 当天买当天不能 swap."""
    h = HoldingState("000001", 10, 13, 30, 0, 10.5, 80)  # days_held=0
    y = Candidate("Y", "STRONG_BUY", 85, 0.30, 5)
    d = evaluate_swap(h, y, swap_cfg)
    assert d.should_swap is False
    assert "min_holding_days" in d.reason


def test_swap_blocked_by_non_strong_buy(swap_cfg):
    h = HoldingState("000001", 10, 13, 30, 25, 10.5, 80)
    y = Candidate("Y", "BUY", 75, 0.20, 5)   # BUY not STRONG_BUY
    d = evaluate_swap(h, y, swap_cfg)
    assert d.should_swap is False
    assert "candidate_tier" in d.reason


def test_swap_blocked_when_holding_on_track(swap_cfg):
    """达成率 = 1.0 (B) → 不严重 → 不 swap."""
    h = HoldingState("000001", 10, 13, 30, 15, 11.5, 80)
    y = Candidate("Y", "STRONG_BUY", 95, 0.40, 5)
    d = evaluate_swap(h, y, swap_cfg)
    assert d.should_swap is False
    assert "fulfillment" in d.reason


def test_swap_fires_when_severe_and_candidate_can_close_gap(swap_cfg):
    """A 严重落后 + Y STRONG_BUY + Y hp=5 能补 5 天 → swap."""
    h = HoldingState("000001", 10, 13, 30, 25, 10.5, 80)
    y = Candidate("Y", "STRONG_BUY", 90, 0.30, optimal_hp=5)
    d = evaluate_swap(h, y, swap_cfg)
    assert d.should_swap is True
    assert d.fulfillment is not None and d.fulfillment < 0.5
    assert d.candidate_contribution is not None
    # Y 5 天 hp, A 剩 5 天 → 贡献 1.0
    assert d.candidate_contribution == pytest.approx(1.0)


def test_swap_blocked_when_candidate_cant_close_gap(swap_cfg):
    """A 严重落后 但 Y hp=60 太长, 5 天补不上 gap=0.66 → 不 swap."""
    h = HoldingState("000001", 10, 13, 30, 25, 10.5, 80)
    y = Candidate("Y", "STRONG_BUY", 95, 0.20, optimal_hp=60)
    d = evaluate_swap(h, y, swap_cfg)
    assert d.should_swap is False
    assert "candidate_cant_close_gap" in d.reason


def test_swap_uplift_estimate_positive_on_good_swap(swap_cfg):
    h = HoldingState("000001", 10, 13, 30, 25, 10.5, 80)
    y = Candidate("Y", "STRONG_BUY", 90, 0.30, 5)
    d = evaluate_swap(h, y, swap_cfg)
    assert d.should_swap is True
    # uplift ≈ Y_contribute_ret - A_maintain - buffer; 应该 > 0
    assert d.swap_uplift_estimate is not None
    assert d.swap_uplift_estimate > 0   # 这次 swap 有净收益


# ============ rank_swap_candidates 优先级 ============

def test_rank_picks_most_severe_holding_first(swap_cfg):
    """两个 holding 都严重落后 + Y 能补 → 选 fulfillment 最低的先 swap.

    用相同 days_held=20 (剩 10 天), 不同 pnl. Y hp=5 → contrib=2.0 (足够补).
    """
    h_bad = HoldingState("BAD", 10, 13, 30, 20, 10.2, 75)   # F = 0.07/0.67 ≈ 0.10
    h_mid = HoldingState("MID", 10, 13, 30, 20, 11.0, 75)   # F = 0.33/0.67 ≈ 0.50 边界
    y = Candidate("Y", "STRONG_BUY", 90, 0.30, optimal_hp=5)
    decisions = rank_swap_candidates([h_mid, h_bad], [y], swap_cfg)
    assert len(decisions) == 1
    # BAD 更严重 (F~0.1 < MID F~0.5), 先 swap BAD
    assert decisions[0].holding.stock_code == "BAD"


def test_rank_skips_unsavable_holding(swap_cfg):
    """BAD 太严重: days_left=2, Y hp=5 → contrib=0.4 不够补 gap=0.86. 跳过 BAD 换 MID."""
    h_bad = HoldingState("BAD", 10, 13, 30, 28, 10.2, 75)
    h_mid = HoldingState("MID", 10, 13, 30, 22, 11.0, 75)
    y = Candidate("Y", "STRONG_BUY", 90, 0.30, optimal_hp=5)
    decisions = rank_swap_candidates([h_mid, h_bad], [y], swap_cfg)
    # BAD 试过 (Y 补不上 0.4 < 0.86), 然后 MID (8/5=1.6 > gap 0.4) 成功
    assert len(decisions) == 1
    assert decisions[0].holding.stock_code == "MID"


def test_rank_respects_max_swaps_per_day(swap_cfg):
    """max_swaps_per_day=2 — 即便 3 个 holding 都该 swap, 也只换 2."""
    from dataclasses import replace
    cfg = replace(swap_cfg, max_swaps_per_day=2)
    holdings = [
        HoldingState(f"A{i}", 10, 13, 30, 25 + i, 10.0, 75) for i in range(3)
    ]
    candidates = [
        Candidate(f"Y{i}", "STRONG_BUY", 90 - i, 0.30, 5) for i in range(3)
    ]
    decisions = rank_swap_candidates(holdings, candidates, cfg)
    assert len(decisions) <= 2


def test_rank_doesnt_swap_same_candidate_twice(swap_cfg):
    """同一只 Y 不能同一天被 swap_in 多次."""
    holdings = [
        HoldingState("A1", 10, 13, 30, 25, 10.2, 75),
        HoldingState("A2", 10, 13, 30, 26, 10.3, 75),
    ]
    candidates = [Candidate("Y", "STRONG_BUY", 90, 0.30, 5)]   # 只 1 个候选
    decisions = rank_swap_candidates(holdings, candidates, swap_cfg)
    assert len(decisions) == 1   # 即便 2 个该 swap, 只能换 1 次 (Y 用过了)


def test_rank_does_not_swap_to_same_stock(swap_cfg):
    """A 不能 swap 自己."""
    h = HoldingState("000001", 10, 13, 30, 25, 10.5, 80)
    y_same = Candidate("000001", "STRONG_BUY", 95, 0.30, 5)   # 同 stock
    y_diff = Candidate("000002", "STRONG_BUY", 85, 0.30, 5)
    decisions = rank_swap_candidates([h], [y_same, y_diff], swap_cfg)
    # 即便 y_same score 更高, 应该选 y_diff
    assert len(decisions) == 1
    assert decisions[0].candidate.stock_code == "000002"
