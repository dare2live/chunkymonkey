"""Paper Sim v2 — exit_rules 5 触发优先级测试."""
from __future__ import annotations

from services.paper_sim.exit_rules import ExitInputs, evaluate_exit


def _base(**kw) -> ExitInputs:
    base = dict(
        stock_code="000001", entry_price=10, entry_stage="2",
        optimal_hp=30, optimal_stop_pct=-0.05, optimal_target_pct=0.20,
        optimal_trailing_pct=-0.10, days_held=5, current_close=10,
        peak_since_entry=10, today_stage="2",
    )
    base.update(kw)
    return ExitInputs(**base)


def test_stop_hit_priority_1():
    """跌破 stop (-5%) → 触发, 即便其它条件也都触发."""
    inp = _base(current_close=9.4, peak_since_entry=12, days_held=40)   # also hp expired + trailing
    d = evaluate_exit(inp)
    assert d.should_exit is True
    assert d.reason == "stop_hit"


def test_target_hit_priority_2():
    """涨到 target (+20%) → 触发."""
    inp = _base(current_close=12.0)
    d = evaluate_exit(inp)
    assert d.should_exit is True
    assert d.reason == "target_hit"


def test_trailing_hit_priority_3():
    """从高点 11.5 回撤 10% 到 10.35 → 触发 (close 没破 stop, 没到 target)."""
    inp = _base(current_close=10.35, peak_since_entry=11.5)
    d = evaluate_exit(inp)
    assert d.should_exit is True
    assert d.reason == "trailing_hit"


def test_hp_expired_priority_4():
    """持仓 ≥ optimal_hp → 触发."""
    inp = _base(days_held=30, current_close=10)
    d = evaluate_exit(inp)
    assert d.should_exit is True
    assert d.reason == "hp_expired"


def test_stage_deterioration_priority_5():
    """买入时 stage=2, 今天 stage=4 + 当前亏损 → 触发."""
    inp = _base(entry_stage="2", today_stage="4", current_close=9.9)
    d = evaluate_exit(inp)
    assert d.should_exit is True
    assert d.reason == "stage_deterioration"


def test_no_exit_when_all_conditions_safe():
    """持仓内, 价格在 stop 之上 + target 之下 + trailing 之上 + hp 未到 + stage 没恶化."""
    inp = _base(current_close=10.5, peak_since_entry=10.8, days_held=10)
    d = evaluate_exit(inp)
    assert d.should_exit is False


def test_stage_deterioration_not_triggered_when_profitable():
    """虽然 stage 恶化到 4, 但当前盈利 → 不卖 (让它继续跑)."""
    inp = _base(entry_stage="2", today_stage="4", current_close=10.5)
    d = evaluate_exit(inp)
    assert d.should_exit is False


def test_stop_takes_precedence_over_target():
    """stop_pct=-0.05 和 target_pct=+0.20, 现价 = entry × 0.94 → stop first."""
    inp = _base(current_close=9.4)
    d = evaluate_exit(inp)
    assert d.reason == "stop_hit"


def test_invalid_price_returns_no_exit():
    inp = _base(current_close=0)
    d = evaluate_exit(inp)
    assert d.should_exit is False
    assert d.reason == "invalid_price"


def test_no_stop_pct_skips_stop_check():
    """没设 stop_pct → skip stop, 但其它仍判."""
    inp = _base(optimal_stop_pct=None, current_close=12.0)
    d = evaluate_exit(inp)
    assert d.reason == "target_hit"   # target 仍生效


def test_stage_deterioration_only_for_stage_le_2_entries():
    """买入 stage=3 → stage 恶化规则不适用 (它本来就不是底部建仓)."""
    inp = _base(entry_stage="3", today_stage="4", current_close=9.9)
    d = evaluate_exit(inp)
    assert d.should_exit is False
