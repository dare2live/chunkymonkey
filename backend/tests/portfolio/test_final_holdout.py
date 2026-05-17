"""P3 Final Holdout Acceptance Gate 单测."""
from __future__ import annotations

from services.portfolio.final_holdout import (
    ANN_RET_TARGET,
    MAX_DD_TARGET,
    MONTHLY_WIN_RATE_TARGET,
    AcceptanceResult,
    FinalHoldoutMetrics,
    check_final_acceptance,
    format_acceptance_report,
)


def test_target_constants_match_plan_v3():
    """常量必须跟 PLAN_V3 §0.1 用户终极目标对齐."""
    assert ANN_RET_TARGET == 0.30
    assert MAX_DD_TARGET == -0.20
    assert MONTHLY_WIN_RATE_TARGET == 0.55


def test_perfect_pass():
    metrics = FinalHoldoutMetrics(
        ann_ret=0.45, max_dd=-0.17,
        excess_vs_hs300=0.38, monthly_win_rate=0.68,
        n_oos_months=6,
    )
    r = check_final_acceptance(metrics)
    assert r.passed is True
    assert r.failures == []


def test_ann_ret_fail():
    metrics = FinalHoldoutMetrics(
        ann_ret=0.20, max_dd=-0.10,
        excess_vs_hs300=0.10, monthly_win_rate=0.60,
    )
    r = check_final_acceptance(metrics)
    assert r.passed is False
    assert any("ann_ret" in f for f in r.failures)


def test_max_dd_fail():
    metrics = FinalHoldoutMetrics(
        ann_ret=0.40, max_dd=-0.30,
        excess_vs_hs300=0.20, monthly_win_rate=0.60,
    )
    r = check_final_acceptance(metrics)
    assert r.passed is False
    assert any("max_dd" in f for f in r.failures)


def test_excess_fail():
    """Strategy 35%, HS300 40% → excess -5%."""
    metrics = FinalHoldoutMetrics(
        ann_ret=0.35, max_dd=-0.15,
        excess_vs_hs300=-0.05, monthly_win_rate=0.60,
    )
    r = check_final_acceptance(metrics)
    assert r.passed is False
    assert any("excess" in f for f in r.failures)


def test_monthly_win_fail():
    metrics = FinalHoldoutMetrics(
        ann_ret=0.40, max_dd=-0.15,
        excess_vs_hs300=0.20, monthly_win_rate=0.45,
    )
    r = check_final_acceptance(metrics)
    assert r.passed is False
    assert any("monthly_win" in f for f in r.failures)


def test_all_four_fail_lists_each():
    metrics = FinalHoldoutMetrics(
        ann_ret=0.10, max_dd=-0.30,
        excess_vs_hs300=-0.05, monthly_win_rate=0.40,
    )
    r = check_final_acceptance(metrics)
    assert r.passed is False
    assert len(r.failures) == 4


def test_boundary_exact_match_passes():
    """Exactly at threshold should PASS (≥ not >)."""
    metrics = FinalHoldoutMetrics(
        ann_ret=0.30, max_dd=-0.20,
        excess_vs_hs300=0.001, monthly_win_rate=0.55,
    )
    r = check_final_acceptance(metrics)
    assert r.passed is True


def test_excess_zero_fails():
    """excess > 0 not ≥ 0; exact 0 should fail."""
    metrics = FinalHoldoutMetrics(
        ann_ret=0.30, max_dd=-0.20,
        excess_vs_hs300=0.0, monthly_win_rate=0.55,
    )
    r = check_final_acceptance(metrics)
    assert r.passed is False
    assert any("excess" in f for f in r.failures)


def test_format_pass_report():
    metrics = FinalHoldoutMetrics(
        ann_ret=0.45, max_dd=-0.17,
        excess_vs_hs300=0.38, monthly_win_rate=0.68,
        model_version="lgbm_v1", feature_version="p0a_v1",
        seed=42, n_oos_months=6,
    )
    r = check_final_acceptance(metrics)
    report = format_acceptance_report(metrics, r)
    assert "PASS" in report
    assert "✓" in report
    assert "lgbm_v1" in report


def test_format_fail_report_lists_failures():
    metrics = FinalHoldoutMetrics(
        ann_ret=0.20, max_dd=-0.30,
        excess_vs_hs300=-0.05, monthly_win_rate=0.45,
    )
    r = check_final_acceptance(metrics)
    report = format_acceptance_report(metrics, r)
    assert "FAIL" in report
    assert "回 alpha 根因" in report
    for f in r.failures:
        assert any(part in report for part in f.split())  # 部分关键字出现


def test_ann_ret_sanity_cap_blocks_corrupt_label_ann():
    """Codex Q8.7 FIX: ann_ret > 50% sanity cap (防 corrupt label leakage).
    
    反例: lgbm_v3_honest_20d P3 ann_ret=21843% (volume unit bug).
    """
    from services.portfolio.final_holdout import (
        FinalHoldoutMetrics, check_final_acceptance, ANN_RET_SANITY_CAP
    )
    # 假设 P3 跑出 ann_ret=2.5 (250%) — 触发 sanity cap
    metrics = FinalHoldoutMetrics(
        ann_ret=2.5,
        max_dd=-0.15,
        excess_vs_hs300=2.4,
        monthly_win_rate=0.85,
        hs300_ann_ret=0.10,
        n_oos_months=6,
    )
    result = check_final_acceptance(metrics)
    assert not result.passed
    assert any("sanity cap" in f for f in result.failures)
    assert ANN_RET_SANITY_CAP == 0.50  # measured baseline (governance v1)
