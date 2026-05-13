"""Phase ψ — governance.py Optuna 治理守门单测.

防回退:
- enforce_pre_optimize: n_trials 太少 raise / 无 seed raise
- enforce_pre_insert: walk_forward_mode='none' / OOS 字段缺失 / 不真实 metric → raise
"""
from __future__ import annotations

import pytest

from services.optimization.config import (
    GovernanceConfig, OptunaConfig, load_optuna_config,
)
from services.optimization.governance import (
    GovernanceViolation, enforce_pre_insert, enforce_pre_optimize,
)


# ━━━━━ enforce_pre_optimize ━━━━━

def test_enforce_pre_optimize_passes_with_valid_args():
    enforce_pre_optimize(n_trials=100, has_seed=True)   # 不 raise


def test_enforce_pre_optimize_rejects_too_few_trials():
    with pytest.raises(GovernanceViolation, match="min_n_trials"):
        enforce_pre_optimize(n_trials=5, has_seed=True)


def test_enforce_pre_optimize_rejects_too_many_trials():
    with pytest.raises(GovernanceViolation, match="max_n_trials"):
        enforce_pre_optimize(n_trials=1000, has_seed=True)


def test_enforce_pre_optimize_rejects_no_seed_by_default():
    with pytest.raises(GovernanceViolation, match="seed"):
        enforce_pre_optimize(n_trials=100, has_seed=False)


def test_enforce_pre_optimize_allow_no_seed_with_override():
    cfg = load_optuna_config(override={"governance": {"require_sampler_seed": False}})
    enforce_pre_optimize(n_trials=100, has_seed=False, cfg=cfg)


# ━━━━━ enforce_pre_insert ━━━━━

_VALID_RECORD = {
    "walk_forward_mode": "holdout",
    "train_n_signals": 50,
    "test_n_signals": 20,
    "oos_sharpe": 1.2,
    "oos_win_rate": 0.65,
    "oos_avg_ret": 0.025,
    "oos_n_traded": 18,
    "oos_period_start": "2025-05-01",
    "oos_period_end": "2026-05-12",
}


def test_enforce_pre_insert_passes_valid_record():
    enforce_pre_insert(_VALID_RECORD.copy())   # 不 raise


def test_enforce_pre_insert_rejects_none_mode():
    rec = _VALID_RECORD.copy()
    rec["walk_forward_mode"] = "none"
    with pytest.raises(GovernanceViolation, match="in-sample fit"):
        enforce_pre_insert(rec)


def test_enforce_pre_insert_rejects_empty_walk_forward_mode():
    rec = _VALID_RECORD.copy()
    rec["walk_forward_mode"] = ""
    with pytest.raises(GovernanceViolation):
        enforce_pre_insert(rec)


def test_enforce_pre_insert_rejects_missing_oos_field():
    rec = _VALID_RECORD.copy()
    del rec["oos_sharpe"]
    with pytest.raises(GovernanceViolation, match="OOS 字段缺失"):
        enforce_pre_insert(rec)


def test_enforce_pre_insert_rejects_none_oos_field():
    rec = _VALID_RECORD.copy()
    rec["oos_sharpe"] = None
    with pytest.raises(GovernanceViolation, match="OOS 字段缺失"):
        enforce_pre_insert(rec)


def test_enforce_pre_insert_rejects_unreal_sharpe():
    """oos_sharpe > 5 → 强 reject (按 Rule 6 大概率 leakage)."""
    rec = _VALID_RECORD.copy()
    rec["oos_sharpe"] = 7.5
    with pytest.raises(GovernanceViolation, match="leakage"):
        enforce_pre_insert(rec)


def test_enforce_pre_insert_rejects_unreal_win_rate():
    rec = _VALID_RECORD.copy()
    rec["oos_win_rate"] = 0.99
    with pytest.raises(GovernanceViolation):
        enforce_pre_insert(rec)


def test_enforce_pre_insert_rejects_unreal_avg_ret():
    rec = _VALID_RECORD.copy()
    rec["oos_avg_ret"] = 0.60   # > 50%
    with pytest.raises(GovernanceViolation):
        enforce_pre_insert(rec)


def test_enforce_pre_insert_rejects_too_few_oos_trades():
    rec = _VALID_RECORD.copy()
    rec["oos_n_traded"] = 2
    with pytest.raises(GovernanceViolation, match="OOS 样本太少"):
        enforce_pre_insert(rec)


def test_enforce_pre_insert_allows_negative_oos_sharpe_within_bound():
    """OOS 表现差是真实的, 不应 reject (区分 'reject 不可能数值' vs '诚实负数')."""
    rec = _VALID_RECORD.copy()
    rec["oos_sharpe"] = -1.5
    rec["oos_avg_ret"] = -0.05
    rec["oos_win_rate"] = 0.35
    enforce_pre_insert(rec)   # 不 raise — 负数但都在合理范围内
