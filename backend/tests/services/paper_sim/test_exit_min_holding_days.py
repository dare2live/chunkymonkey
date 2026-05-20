"""Path A2 (2026-05-20): min_holding_days_before_exit anti-churn 强力版 单测.

设计:
- 用户选项 A — 强制 ANY single-position exit (hp_expired / stop_hit / trailing_hit /
  stage_deterioration) 都需 days_held >= min_holding_days_before_exit.
- 跟既有 min_forced_hp 区别: min_forced_hp 只约束 hp_expired; 此字段约束 4 类全部.
- 不影响 hard_stop_portfolio_dd (portfolio-level 在 driver.py L279-302 独立分支,
  完全绕过 evaluate_exit), 详 test_portfolio_dd_bypasses_day_gate_by_design.
- 不影响 swap (alpha-uplift 重 allocate, 走 swap.min_holding_days_before_swap).

evidence: 2026-05-20 baseline (champion_baseline) 88 trades 全走 single-position exit,
swap=0 是 churn 根因, min_forced_hp 仅约束 hp_expired 不够 → 用户选选项 A.
"""
from __future__ import annotations

import pytest

from services.paper_sim.exit_rules import ExitInputs, evaluate_exit


def _base(**kw) -> ExitInputs:
    """跟 backend/tests/paper_sim/test_exit_rules.py::_base 同 schema, 加 min_holding_days_before_exit 默认 0."""
    base = dict(
        stock_code="000001", entry_price=10, entry_stage="2",
        optimal_hp=30, optimal_stop_pct=-0.05, optimal_target_pct=0.20,
        optimal_trailing_pct=0.10,                # 正数 = 回撤 10%
        days_held=5, current_close=10, current_high=10,
        peak_since_entry=10, trailing_armed=False, today_stage="2",
        min_forced_hp=0,
        min_holding_days_before_exit=0,
    )
    base.update(kw)
    return ExitInputs(**base)


# ──────────────── 1. stop_hit 受 day-gate 约束 ────────────────

def test_stop_hit_blocked_when_days_held_lt_min_holding():
    """day_held=3 + stop 触发 + min_holding=5 → skip exit, 继续持仓."""
    inp = _base(current_close=9.4, days_held=3, min_holding_days_before_exit=5)
    d = evaluate_exit(inp)
    assert d.should_exit is False, "stop_hit 应被 day-gate 阻 (3 < 5)"
    assert d.reason != "stop_hit"


def test_stop_hit_allowed_when_days_held_ge_min_holding():
    """day_held=6 + stop 触发 + min_holding=5 → allow exit."""
    inp = _base(current_close=9.4, days_held=6, min_holding_days_before_exit=5)
    d = evaluate_exit(inp)
    assert d.should_exit is True
    assert d.reason == "stop_hit"


def test_stop_hit_allowed_at_exact_boundary():
    """day_held=5 + min_holding=5 → days_held >= min, allow (边界 inclusive)."""
    inp = _base(current_close=9.4, days_held=5, min_holding_days_before_exit=5)
    d = evaluate_exit(inp)
    assert d.should_exit is True
    assert d.reason == "stop_hit"


# ──────────────── 2. trailing_hit 受 day-gate 约束 ────────────────

def test_trailing_hit_blocked_when_days_held_lt_min_holding():
    """trailing armed + close 10.8 < peak 12 × 0.9 + day_held=2 + min_holding=5 → skip."""
    inp = _base(current_close=10.8, current_high=10.9, peak_since_entry=12.0,
                trailing_armed=True, days_held=2, min_holding_days_before_exit=5)
    d = evaluate_exit(inp)
    assert d.should_exit is False, "trailing_hit 应被 day-gate 阻"
    # peak 跨日跟踪 stateful, 必须 update 给 driver 持久化 (不阻 arm/peak 是设计意图)
    assert d.new_trailing_armed is True
    assert d.new_peak == 12.0


def test_trailing_hit_allowed_when_days_held_ge_min_holding():
    """trailing armed + close 10.8 + day_held=10 + min_holding=5 → allow."""
    inp = _base(current_close=10.8, current_high=10.9, peak_since_entry=12.0,
                trailing_armed=True, days_held=10, min_holding_days_before_exit=5)
    d = evaluate_exit(inp)
    assert d.should_exit is True
    assert d.reason == "trailing_hit"


# ──────────────── 3. hp_expired 受 day-gate 约束 ────────────────

def test_hp_expired_blocked_when_days_held_lt_min_holding():
    """optimal_hp=2 (短 hp), day_held=3 (≥hp 触发), min_holding=5 → skip."""
    inp = _base(optimal_hp=2, days_held=3, min_holding_days_before_exit=5,
                current_close=10.0)
    d = evaluate_exit(inp)
    assert d.should_exit is False, "hp_expired 应被 day-gate 阻"


def test_hp_expired_allowed_when_days_held_ge_min_holding():
    """optimal_hp=2, day_held=6, min_holding=5 → allow (跟正常 hp 一致)."""
    inp = _base(optimal_hp=2, days_held=6, min_holding_days_before_exit=5,
                current_close=10.0)
    d = evaluate_exit(inp)
    assert d.should_exit is True
    assert d.reason == "hp_expired"


# ──────────────── 4. stage_deterioration 受 day-gate 约束 ────────────────

def test_stage_deterioration_blocked_when_days_held_lt_min_holding():
    """stage 由 2 恶化到 4 + 亏损 + day_held=2 + min_holding=5 → skip."""
    inp = _base(entry_stage="2", today_stage="4", current_close=9.9,
                days_held=2, min_holding_days_before_exit=5)
    d = evaluate_exit(inp)
    assert d.should_exit is False, "stage_deterioration 应被 day-gate 阻"


def test_stage_deterioration_allowed_when_days_held_ge_min_holding():
    """stage 恶化 + 亏损 + day_held=7 + min_holding=5 → allow."""
    inp = _base(entry_stage="2", today_stage="4", current_close=9.9,
                days_held=7, min_holding_days_before_exit=5)
    d = evaluate_exit(inp)
    assert d.should_exit is True
    assert d.reason == "stage_deterioration"


# ──────────────── 5. backward compat: min_holding=0 (默认) 不变 ────────────────

def test_min_holding_zero_preserves_existing_behavior_stop():
    """min_holding=0 (默认) + day_held=1 + stop 触发 → 跟现有 logic 一致, allow."""
    inp = _base(current_close=9.4, days_held=1, min_holding_days_before_exit=0)
    d = evaluate_exit(inp)
    assert d.should_exit is True
    assert d.reason == "stop_hit"


def test_min_holding_zero_preserves_existing_behavior_hp():
    """min_holding=0 + day_held=30 + optimal_hp=30 → hp_expired (跟现有 logic 一致)."""
    inp = _base(optimal_hp=30, days_held=30, min_holding_days_before_exit=0)
    d = evaluate_exit(inp)
    assert d.should_exit is True
    assert d.reason == "hp_expired"


# ──────────────── 6. trailing arm 不被 day-gate 阻 (state 更新需求) ────────────────

def test_trailing_arm_still_works_when_day_gated():
    """target 触发 + day_gate 阻 exit, 但 arm + peak update 仍要做 (stateful, 防跨日丢失).

    day_held=2, target 触发 → arm trailing, 但 exit (trailing/stop) 全被 gate 阻.
    """
    inp = _base(current_close=12.0, current_high=12.0, days_held=2,
                min_holding_days_before_exit=5, trailing_armed=False)
    d = evaluate_exit(inp)
    assert d.should_exit is False
    assert d.new_trailing_armed is True, "arm 是 state 更新, 不应被 day-gate 阻"


# ──────────────── 7. invalid_price 短路 (不依赖 day-gate) ────────────────

def test_invalid_price_short_circuits_before_day_gate():
    inp = _base(current_close=0, days_held=10, min_holding_days_before_exit=5)
    d = evaluate_exit(inp)
    assert d.should_exit is False
    assert d.reason == "invalid_price"


# ──────────────── 8. 文档化: portfolio_dd hard stop 不走 evaluate_exit ────────────────

def test_portfolio_dd_bypasses_day_gate_by_design():
    """文档化测试: hard_stop_portfolio_dd 在 driver.py L279-302 独立分支处理,
    完全绕过 evaluate_exit, 因此天然不受 min_holding_days_before_exit 约束.

    这是设计意图: portfolio-level 真实风控 (max_dd_hard_stop_pct=-25%) 触发时,
    全清不能延迟. day-gate 仅约束 single-position exit (stop/trailing/hp/stage).

    grep verify (实际代码路径):
      driver.py L279: if hard_stop_triggered: ... _close_position(... 'hard_stop_portfolio_dd' ...)
      driver.py L303-347: for p in open_positions: d = evaluate_exit(...)
    两条路径互斥 (hard_stop 走完后 open_positions 已全清, evaluate_exit 路径 idle).
    """
    # 仅作 sanity: evaluate_exit signature 内没 hard_stop reason
    # 让回归 PR 万一改 evaluate_exit 加入 portfolio_dd 时, 立刻 fail 提醒"该走 driver, 不走 evaluate"
    inp = _base(current_close=9.0, days_held=1, min_holding_days_before_exit=5)
    d = evaluate_exit(inp)
    assert d.reason != "hard_stop_portfolio_dd", (
        "evaluate_exit 不应处理 portfolio_dd — 它属 driver 独立分支, "
        "天然不受 day-gate 约束 (设计意图)"
    )


# ──────────────── 9. min_forced_hp + min_holding_days_before_exit 共存 ────────────────

def test_min_forced_hp_and_min_holding_coexist():
    """两个 anti-churn 字段共存. min_forced_hp 只管 hp_expired, min_holding 管全部.

    场景: optimal_hp=10, min_forced_hp=20, min_holding=5.
    day_held=15 + close 跌 stop:
      - stop 触发, days_held=15 >= min_holding=5 → allow stop_hit ✓
      - 验证 min_forced_hp=20 不影响 stop_hit (只影响 hp_expired)
    """
    inp = _base(optimal_hp=10, min_forced_hp=20, min_holding_days_before_exit=5,
                days_held=15, current_close=9.0)
    d = evaluate_exit(inp)
    assert d.should_exit is True
    assert d.reason == "stop_hit"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
