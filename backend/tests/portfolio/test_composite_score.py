"""P2 composite score 单测."""
from __future__ import annotations

from services.portfolio.composite_score import (
    CompositeWeights,
    StrategyRunMetrics,
    _hp_penalty,
    compute_composite_score,
    score_strategy_run,
)


def test_pure_return_no_penalties():
    """All weights=0 except ret → composite = ret_w * ann_ret."""
    w = CompositeWeights(ret_w=1.0, dd_w=0, hp_w=0, turnover_w=0, cost_w=0, capacity_w=0)
    score = compute_composite_score(ann_ret=0.30, max_dd=-0.50, weights=w)
    assert abs(score - 0.30) < 1e-9


def test_high_dd_lowers_score():
    """同 ann_ret, max_dd 越大 score 越低."""
    s_low_dd = compute_composite_score(ann_ret=0.30, max_dd=-0.05)
    s_high_dd = compute_composite_score(ann_ret=0.30, max_dd=-0.30)
    assert s_low_dd > s_high_dd


def test_high_turnover_lowers_score():
    s_low_to = compute_composite_score(ann_ret=0.30, max_dd=-0.20, turnover=1.0)
    s_high_to = compute_composite_score(ann_ret=0.30, max_dd=-0.20, turnover=8.0)
    assert s_low_to > s_high_to


def test_hp_penalty_linear():
    """linear mode: f(hp) = 1/hp; hp=1 → 1.0, hp=10 → 0.1."""
    w = CompositeWeights(hp_penalty_mode="linear")
    assert abs(_hp_penalty(1, w) - 1.0) < 1e-9
    assert abs(_hp_penalty(10, w) - 0.1) < 1e-9
    assert abs(_hp_penalty(0, w)) < 1e-9  # 边界
    assert abs(_hp_penalty(None, w)) < 1e-9


def test_hp_penalty_log():
    """log mode: 1/log(hp+e); hp=1 → 1/log(1+e) ≈ 0.76; hp=100 → 1/log(100+e) ≈ 0.22."""
    w = CompositeWeights(hp_penalty_mode="log")
    p1 = _hp_penalty(1, w)
    p100 = _hp_penalty(100, w)
    assert 0.70 < p1 < 0.85
    assert 0.15 < p100 < 0.30
    assert p1 > p100  # 短期 hp 更重 penalty


def test_hp_penalty_piecewise():
    """piecewise mode: <5d 重罚, >60d 轻罚, 中间中等."""
    w = CompositeWeights(
        hp_penalty_mode="piecewise",
        hp_piecewise_short_threshold=5, hp_piecewise_long_threshold=60,
        hp_piecewise_short_penalty=1.0, hp_piecewise_long_penalty=0.2,
    )
    assert _hp_penalty(2, w) == 1.0     # 短期
    assert _hp_penalty(30, w) == 0.5    # 中等
    assert _hp_penalty(90, w) == 0.2    # 长期


def test_score_strategy_run_wrapper():
    metrics = StrategyRunMetrics(
        ann_ret=0.30, max_dd=-0.18, avg_hp=15,
        turnover=2.0, tx_cost_pct=0.05, concentration=0.10,
    )
    s = score_strategy_run(metrics)
    # 默认 weights: ret 1.0, dd 1.0, hp 0.0, turnover 0.5, cost 1.0, capacity 0.5
    # = 0.30 - 0.18 - 0 - 1.0 - 0.05 - 0.05 = -0.98
    expected = 0.30 - 0.18 - 0 - 0.5 * 2.0 - 0.05 - 0.5 * 0.10
    assert abs(s - expected) < 1e-9


def test_high_ret_can_offset_some_dd():
    """ann_ret=0.50, max_dd=-0.20 vs ann_ret=0.30, max_dd=-0.10:
    高 ret 即使 dd 大也可能 score 更高."""
    high_ret = compute_composite_score(ann_ret=0.50, max_dd=-0.20)
    low_ret = compute_composite_score(ann_ret=0.30, max_dd=-0.10)
    # 0.50 - 0.20 = 0.30 vs 0.30 - 0.10 = 0.20
    assert high_ret > low_ret


def test_user_target_3_check():
    """用户终极目标 (PLAN_V3): ann≥30%, max_dd≥-20%. composite 应 > 0."""
    s = compute_composite_score(ann_ret=0.30, max_dd=-0.20, avg_hp=15)
    # 0.30 - 0.20 - 0 - 0 - 0 - 0 = 0.10
    assert s >= 0.05
