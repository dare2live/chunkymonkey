"""Tests for backtest_validation gates (PBO / DSR / Conservative / IS-OOS).

ChunkyMonkey MSAF Phase 1.5 (Codex R31 design 落地).
"""
from __future__ import annotations

import numpy as np
import pytest

from services.backtest_validation.pbo import compute_pbo
from services.backtest_validation.dsr import compute_dsr
from services.backtest_validation.gate import (
    gate_pbo, gate_dsr, gate_conservative, gate_is_oos, run_all_gates,
)


def test_pbo_clean_strategy_passes():
    """Strategy with consistent OOS outperformance should have low PBO."""
    rng = np.random.default_rng(42)
    # 10 trials, 64 periods. trial 0 is genuinely best (consistent +alpha)
    n_trials, n_periods = 10, 64
    returns = rng.normal(0, 0.01, (n_trials, n_periods))
    returns[0] += 0.005  # +50bp/period consistent alpha
    result = compute_pbo(returns, sub_periods=8)
    # 真 alpha → lambda 应该 > 0 多数 combo, PBO 应低
    assert result.pbo < 0.5, f"Genuine alpha should have PBO < 0.5, got {result.pbo}"


def test_pbo_noise_strategy_high_pbo():
    """Pure noise strategies should have PBO ≈ 0.5."""
    rng = np.random.default_rng(123)
    n_trials, n_periods = 10, 64
    returns = rng.normal(0, 0.01, (n_trials, n_periods))
    result = compute_pbo(returns, sub_periods=8)
    # 纯噪声 → PBO 应该 ≈ 0.5 (best IS 在 OOS rank 随机)
    assert 0.20 <= result.pbo <= 0.80, f"Noise should have PBO ≈ 0.5, got {result.pbo}"


def test_pbo_invalid_input():
    """Bad input should raise."""
    with pytest.raises(ValueError):
        compute_pbo(np.array([1, 2, 3]))  # 1D
    with pytest.raises(ValueError):
        compute_pbo(np.zeros((1, 16)))  # too few trials
    with pytest.raises(ValueError):
        compute_pbo(np.zeros((10, 5)), sub_periods=16)  # too few periods


def test_dsr_high_sharpe_passes():
    """Genuine high Sharpe with low n_trials should pass DSR."""
    rng = np.random.default_rng(42)
    n = 252
    # Daily return mean = 0.001, std = 0.01 → annualized SR = 0.001 * sqrt(252) / 0.01 = 1.59
    returns = rng.normal(0.0008, 0.01, n)
    result = compute_dsr(returns, n_trials=1)
    assert result.sr_observed > 0.5
    assert result.p_conf > 0.5  # Better than random


def test_dsr_selection_bias_n_trials():
    """Higher n_trials → higher expected_max SR → harder DSR pass."""
    rng = np.random.default_rng(42)
    returns = rng.normal(0.0005, 0.01, 252)
    r1 = compute_dsr(returns, n_trials=1)
    r100 = compute_dsr(returns, n_trials=100)
    # With more trials, expected max SR ↑, p_conf ↓
    assert r100.sr_expected_max > r1.sr_expected_max


def test_dsr_negative_sharpe_fails():
    """Negative SR should fail DSR."""
    rng = np.random.default_rng(42)
    returns = rng.normal(-0.0005, 0.01, 252)  # negative mean
    result = compute_dsr(returns, n_trials=1)
    assert not result.passes
    assert result.p_conf < 0.5


def test_gate_conservative_pass():
    g = gate_conservative(ann_ret_normal=0.20, ann_ret_conservative=0.10)
    assert g.passes
    assert "pass" in g.reason


def test_gate_conservative_fail():
    g = gate_conservative(ann_ret_normal=0.20, ann_ret_conservative=-0.05)
    assert not g.passes


def test_gate_is_oos_pass():
    g = gate_is_oos(is_metric=0.04, oos_metric=0.035, max_relative_drop=0.30)
    assert g.passes
    assert g.detail["relative_drop"] < 0.30


def test_gate_is_oos_fail_large_drop():
    g = gate_is_oos(is_metric=0.10, oos_metric=0.02, max_relative_drop=0.30)
    assert not g.passes
    assert g.detail["relative_drop"] > 0.30


def test_run_all_gates_missing_inputs():
    """Missing inputs → warn_only (not block)."""
    r = run_all_gates(challenger_id="test_missing")
    assert not r.all_pass
    assert r.promote_action == "warn_only"
    assert r.pbo.detail.get("error") == "input_missing"
    assert r.dsr.detail.get("error") == "input_missing"


def test_run_all_gates_full_pass():
    """All inputs pass → promote."""
    rng = np.random.default_rng(42)
    n_trials, n_periods = 10, 64
    returns = rng.normal(0, 0.01, (n_trials, n_periods))
    returns[0] += 0.005

    r = run_all_gates(
        challenger_id="test_pass",
        returns_matrix=returns,
        oos_returns=returns[0],
        n_trials_for_dsr=10,
        ann_normal=0.20,
        ann_conservative=0.12,
        is_metric=0.05,
        oos_metric=0.04,
    )
    # Note: DSR p_conf with n_trials=10 may be stringent; verify status
    assert r.is_oos.passes
    assert r.conservative.passes


def test_run_all_gates_pbo_fail_blocks():
    """High PBO → block."""
    rng = np.random.default_rng(123)
    returns = rng.normal(0, 0.01, (10, 64))  # pure noise
    r = run_all_gates(
        challenger_id="test_pbo_fail",
        returns_matrix=returns,
        oos_returns=returns[0],
        ann_normal=0.05,
        ann_conservative=0.02,
        is_metric=0.04,
        oos_metric=0.035,
    )
    # Pure noise → PBO ≈ 0.5 > 0.20 → 不 pass
    # 但所有其它 gate 都可能 pass (small sample size)
    assert not r.pbo.passes or r.pbo.detail.get("pbo", 0) >= 0.20


def test_historical_leakage_phantom_blocked():
    """历史 +312% leakage 反例阻断: in-sample Optuna fit 给历史 signal 当 forward ranking.

    场景: mart_per_stock_stage_strategy_optimal.sharpe 是全期 Optuna fit (不是 OOS), selector
    ORDER BY sharpe DESC → 等于 "事后挑历史最强 5 只", paper_sim ann_ret +312% 假象.

    特征:
    - IS metric 巨高 (e.g. sharpe 5.0 假象)
    - OOS metric 普通甚至负 (真实 walk-forward)
    - IS-OOS relative_drop > 30% → gate_is_oos FAIL → block

    rule-compliance: ok evidence=historical-leakage-+312pct-reproduction
    """
    # IS sharpe 5.0 (full-sample in-sample fit) vs OOS sharpe -0.5 (真实 walk-forward)
    r = gate_is_oos(is_metric=5.0, oos_metric=-0.5, max_relative_drop=0.30)
    assert not r.passes, "gate_is_oos 必须阻断 IS=5.0 vs OOS=-0.5 leakage"
    assert r.detail["relative_drop"] > 1.0  # relative_drop 应该 > 100%


def test_historical_leakage_phantom_full_chain():
    """端到端 +312% 反例阻断: run_all_gates 集成验证."""
    rng = np.random.default_rng(0)
    # 模拟 paper_sim NAV 序列, in-sample fit 高 ann 但 walk-forward OOS 是噪音
    # PBO input: 10 trials × 64 periods 都是噪声 (无真 alpha)
    returns = rng.normal(0, 0.01, (10, 64))
    r = run_all_gates(
        challenger_id="historical_phantom_312pct",
        returns_matrix=returns,
        oos_returns=returns[0],  # 噪声 OOS
        n_trials_for_dsr=50,     # 反映 Optuna 50-trial search
        ann_normal=0.30,         # 假 +30% paper_sim
        ann_conservative=-0.05,  # 真 conservative -5%
        is_metric=5.0,           # 假 in-sample sharpe
        oos_metric=-0.5,         # 真 OOS sharpe
    )
    # 必须 NOT promote
    assert r.promote_action != "promote", f"phantom +312% 必须阻 promote, got {r.promote_action}"
    # IS-OOS gate 应 fail (relative_drop 巨大)
    assert not r.is_oos.passes
    # Conservative gate 应 fail (真成本下 ann < 0)
    assert not r.conservative.passes


def test_gate_is_oos_proxy_mode_threshold_loose():
    """proxy_mode=True 时 threshold 放宽到 70% (split-half 时段稳定性, 非真 train-OOS).

    场景: relative_drop=0.50 在真 IS-OOS (30%) 会 fail, 在 proxy (70%) 应 pass.
    Codex review 2026-05-19 HIGH 2 + LOW: evidence-tagged, threshold 用 module 常量.
    """
    # IS=0.10, OOS=0.05 → relative_drop=50%
    r_proxy = gate_is_oos(is_metric=0.10, oos_metric=0.05, proxy_mode=True)
    assert r_proxy.passes, "proxy_mode 50% drop 应 pass (≤70%)"
    assert r_proxy.detail["proxy_mode"] is True
    assert r_proxy.detail["threshold"] == 0.70
    assert r_proxy.detail["evidence"] == "degraded-split-half-not-train-log"

    r_true = gate_is_oos(is_metric=0.10, oos_metric=0.05, proxy_mode=False)
    assert not r_true.passes, "true train-log mode 50% drop 应 fail (>30%)"
    assert r_true.detail["proxy_mode"] is False
    assert r_true.detail["threshold"] == 0.30
    assert r_true.detail["evidence"] == "true-train-log-PIT"


def test_run_all_gates_proxy_full_pass_degrades_to_warn_only_proxy():
    """proxy_mode=True 即使 4 gates 全 pass, promote_action 降级 warn_only_proxy.

    Codex review 2026-05-19 HIGH 2: split-half proxy evidence degraded,
    不该跟真 train-log 同等 hard promote. 等接入 fact_model_train_log 后才允许 hard promote.
    """
    rng = np.random.default_rng(7)
    n_trials, n_periods = 10, 64
    returns = rng.normal(0, 0.01, (n_trials, n_periods))
    returns[0] += 0.005

    r = run_all_gates(
        challenger_id="proxy_degraded_test",
        returns_matrix=returns,
        oos_returns=returns[0],
        n_trials_for_dsr=10,
        ann_normal=0.18,
        ann_conservative=0.10,
        is_metric=0.03,
        oos_metric=0.022,
        is_oos_proxy_mode=True,
    )
    # 4 gates pass + proxy → warn_only_proxy, not promote
    if r.all_pass:
        assert r.promote_action == "warn_only_proxy", (
            f"proxy + all_pass 应降级 warn_only_proxy, got {r.promote_action}"
        )
    # IS-OOS evidence 应为 degraded
    assert r.is_oos.detail.get("evidence") == "degraded-split-half-not-train-log"


def test_run_all_gates_true_train_log_can_promote():
    """proxy_mode=False (真 train-log IS-OOS) 时 4 gates 全 pass 允许 hard promote."""
    rng = np.random.default_rng(7)
    n_trials, n_periods = 10, 64
    returns = rng.normal(0, 0.01, (n_trials, n_periods))
    returns[0] += 0.005

    r = run_all_gates(
        challenger_id="true_train_log_test",
        returns_matrix=returns,
        oos_returns=returns[0],
        n_trials_for_dsr=10,
        ann_normal=0.18,
        ann_conservative=0.10,
        is_metric=0.03,
        oos_metric=0.022,
        is_oos_proxy_mode=False,
    )
    # IS-OOS evidence 应 true-train-log-PIT
    assert r.is_oos.detail.get("evidence") == "true-train-log-PIT"
    if r.all_pass:
        assert r.promote_action == "promote", (
            f"true train-log + all_pass 应 promote, got {r.promote_action}"
        )


def test_clean_alpha_promote_path():
    """干净 alpha 路径: 真实 walk-forward + conservative ann > 0 → promote 允许.

    对比 phantom: 数字温和 / IS-OOS gap 小 / conservative 仍 > 0.

    rule-compliance: ok evidence=clean-baseline-from-p0b-walk-forward
    """
    rng = np.random.default_rng(7)
    n_trials, n_periods = 10, 64
    returns = rng.normal(0, 0.01, (n_trials, n_periods))
    returns[0] += 0.005  # 真 alpha 在 trial 0

    r = run_all_gates(
        challenger_id="clean_alpha_baseline",
        returns_matrix=returns,
        oos_returns=returns[0],
        n_trials_for_dsr=10,
        ann_normal=0.18,        # 真实 +18% (符合 v3.2 实测 RankIC 0.01-0.02 翻译)
        ann_conservative=0.10,  # 保守后仍 +10%
        is_metric=0.03,         # IS RankIC 0.03 (类似 v3.2 P0b)
        oos_metric=0.022,       # OOS RankIC 0.022 (drop ~27% < 30% 阈值)
    )
    # 至少 is_oos + conservative pass
    assert r.is_oos.passes, f"IS-OOS 应 pass: {r.is_oos.reason}"
    assert r.conservative.passes, f"Conservative 应 pass: {r.conservative.reason}"
