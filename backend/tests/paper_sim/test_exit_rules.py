"""Paper Sim v2 — exit_rules 触发优先级测试.

mart 表 contract: stop_pct 负数 (例 -0.05), trailing_pct **正数** (回撤幅度, 例 0.10).
"""
from __future__ import annotations

from services.paper_sim.exit_rules import ExitInputs, evaluate_exit


def _base(**kw) -> ExitInputs:
    base = dict(
        stock_code="000001", entry_price=10, entry_stage="2",
        optimal_hp=30, optimal_stop_pct=-0.05, optimal_target_pct=0.20,
        optimal_trailing_pct=0.10,                # 正数 = 回撤 10%
        days_held=5, current_close=10, current_high=10,
        peak_since_entry=10, trailing_armed=False, today_stage="2",
    )
    base.update(kw)
    return ExitInputs(**base)


def test_stop_hit_priority_1():
    inp = _base(current_close=9.4, days_held=40)
    d = evaluate_exit(inp)
    assert d.should_exit is True
    assert d.reason == "stop_hit"


def test_target_hit_arms_trailing_not_exit():
    """target hit → arm trailing, 不直接卖 (跟 portfolio_backtest 一致)."""
    inp = _base(current_close=12.0, current_high=12.0)
    d = evaluate_exit(inp)
    assert d.should_exit is False     # target 不直接 exit
    assert d.new_trailing_armed is True


def test_trailing_hit_only_after_armed():
    """没 arm trailing — 即便从高点回撤 50% 也不 trigger trailing."""
    inp = _base(current_close=5.0, peak_since_entry=11.0)
    d = evaluate_exit(inp)
    # 未 armed → stop 优先 (close 5 < entry 10 × 0.95) → stop_hit
    assert d.reason == "stop_hit"


def test_trailing_armed_then_drops_10pct():
    """target 已 hit (armed=True), high_since=12, close 跌到 10.8 (= 12 × 0.9) → trailing_hit."""
    inp = _base(current_close=10.8, current_high=10.9, peak_since_entry=12.0, trailing_armed=True)
    d = evaluate_exit(inp)
    assert d.should_exit is True
    assert d.reason == "trailing_hit"


def test_trailing_armed_not_trigger_if_close_above_threshold():
    """armed=True, close=11.5 > peak 12 × 0.9 = 10.8 → 不卖."""
    inp = _base(current_close=11.5, current_high=11.5, peak_since_entry=12.0, trailing_armed=True)
    d = evaluate_exit(inp)
    assert d.should_exit is False
    assert d.new_trailing_armed is True
    assert d.new_peak == 12.0   # peak 不变


def test_trailing_armed_peak_updates():
    """armed=True, close=12.5 (新高) → peak 上调, 不卖."""
    inp = _base(current_close=12.5, current_high=12.5, peak_since_entry=12.0, trailing_armed=True)
    d = evaluate_exit(inp)
    assert d.should_exit is False
    assert d.new_peak == 12.5   # peak 更新到新高


def test_hp_expired():
    inp = _base(days_held=30, current_close=10)
    d = evaluate_exit(inp)
    assert d.should_exit is True
    assert d.reason == "hp_expired"


def test_stage_deterioration():
    inp = _base(entry_stage="2", today_stage="4", current_close=9.9)
    d = evaluate_exit(inp)
    assert d.should_exit is True
    assert d.reason == "stage_deterioration"


def test_no_exit_safe_zone():
    """无 armed, close 在 stop 上 target 下 hp 未到 stage 没恶化."""
    inp = _base(current_close=10.5, days_held=10)
    d = evaluate_exit(inp)
    assert d.should_exit is False


def test_stage_deterioration_not_triggered_when_profitable():
    inp = _base(entry_stage="2", today_stage="4", current_close=10.5)
    d = evaluate_exit(inp)
    assert d.should_exit is False


def test_invalid_price():
    inp = _base(current_close=0)
    d = evaluate_exit(inp)
    assert d.should_exit is False
    assert d.reason == "invalid_price"


def test_stage_deterioration_only_for_stage_le_2_entries():
    inp = _base(entry_stage="3", today_stage="4", current_close=9.9)
    d = evaluate_exit(inp)
    assert d.should_exit is False
