"""Phase ψ — optuna_config.yaml + config.py 加载器单测.

防回退:
- yaml 缺字段 / 类型错 raise
- 权重和 != 1.0 raise
- 阈值顺序错 raise (例 lo > hi)
"""
from __future__ import annotations

import pytest

from services.optimization.config import (
    OptunaConfig, get_optuna_config, load_optuna_config, reload_optuna_config,
)


def test_default_config_loads():
    cfg = load_optuna_config()
    assert isinstance(cfg, OptunaConfig)
    # 关键字段存在
    assert cfg.governance.min_n_trials > 0
    assert cfg.governance.max_n_trials >= cfg.governance.min_n_trials
    assert cfg.walk_forward.default_mode in {"expanding_monthly", "expanding", "holdout", "none"}
    assert len(cfg.search_space.strategy.hp_choices) >= 2
    assert cfg.constraints.max_acceptable_drawdown < 0   # 是负数 (回撤)


def test_get_optuna_config_singleton():
    cfg1 = get_optuna_config()
    cfg2 = get_optuna_config()
    assert cfg1 is cfg2


def test_reload_optuna_config_refresh_singleton():
    cfg1 = get_optuna_config()
    cfg2 = reload_optuna_config()
    # 内容相同 (yaml 没改) 但实例不同 (重 load 了)
    assert cfg2 is not cfg1
    cfg3 = get_optuna_config()
    assert cfg3 is cfg2   # reload 后, 新 singleton


def test_override_merges_governance():
    """override 必须满足 _validate (例 min ≤ execution.n_trials ≤ max)."""
    cfg = load_optuna_config(override={
        "governance": {"min_n_trials": 80},
        "execution": {"n_trials": 100},   # 100 ≥ 80 ✓
    })
    assert cfg.governance.min_n_trials == 80
    # 其他字段不变
    assert cfg.governance.max_realistic_sharpe > 0
    assert cfg.execution.n_trials == 100


def test_override_rejects_bad_composite_weights():
    """权重和必须 = 1.0."""
    bad = {"composite": {"calmar_w": 5.0}}   # 现在 ∑ != 1.0
    with pytest.raises((AssertionError, ValueError)):
        load_optuna_config(override=bad)


def test_override_rejects_bad_constraints():
    """worst_single_loss 必须比 max_acceptable_drawdown 更严."""
    bad = {"constraints": {"worst_single_loss": -0.10}}   # > -0.25 太宽
    with pytest.raises(AssertionError):
        load_optuna_config(override=bad)


def test_override_rejects_inverted_stop_range():
    bad = {"search_space": {"strategy": {"stop_pct": {"lo": -0.01, "hi": -0.05}}}}
    # lo > hi → 但因为 _to_range 不强制顺序, _validate 才检查
    # 这里改成检查 lo < hi <= 0
    with pytest.raises(AssertionError):
        load_optuna_config(override=bad)
