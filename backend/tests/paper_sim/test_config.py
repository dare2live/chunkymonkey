"""Paper Sim v2 — config 加载 + 校验单测."""
from __future__ import annotations

from pathlib import Path

import pytest

from services.paper_sim.config import load_config, PaperSimConfig


def test_load_default_config_valid():
    cfg = load_config()
    assert isinstance(cfg, PaperSimConfig)
    # 用户原话: 100 万, 最多 5 只
    assert cfg.portfolio.initial_cash == 1_000_000
    assert cfg.portfolio.max_positions == 5
    # Swap 严格 STRONG_BUY (D1 用户审过)
    assert cfg.selection.min_tier_to_swap_in == "STRONG_BUY"
    # 达成率阈值
    assert 0 < cfg.swap.severe_threshold <= 1.0


def test_override_works():
    cfg = load_config(override={"portfolio": {"max_positions": 3}})
    assert cfg.portfolio.max_positions == 3
    # 其它字段保留
    assert cfg.portfolio.initial_cash == 1_000_000


def test_invalid_max_positions_raises():
    with pytest.raises(AssertionError):
        load_config(override={"portfolio": {"max_positions": 50}})


def test_invalid_severe_threshold_raises():
    with pytest.raises(AssertionError):
        load_config(override={"swap": {"severe_threshold": 1.5}})


def test_invalid_sizing_strategy_raises():
    with pytest.raises(AssertionError):
        load_config(override={"portfolio": {"position_sizing": "random_walk"}})


def test_hard_stop_must_be_stricter_than_warning():
    # daily_dd_warning -3% / max_dd_hard_stop -25% — hard 应该更负
    cfg = load_config()
    assert cfg.risk.max_dd_hard_stop_pct < cfg.risk.daily_dd_warning_pct


def test_validation_thresholds_align_with_user_goals():
    """KPI 验证阈值必须跟用户 3 标准对齐."""
    cfg = load_config()
    uc = cfg.validation.user_criteria
    assert uc["annual_return_min"] >= 0.30, "用户原话: 年化 ≥ 30%"
    assert uc["max_dd_min"] >= -0.20, "用户原话: 不缩水 max_dd ≥ -20%"
    assert uc["excess_vs_hs300_min"] >= 0.0, "用户原话: 超额 > 0"


def test_lambdamart_v6_config_uses_dedicated_prediction_table():
    cfg_path = Path(__file__).resolve().parents[2] / "config" / "paper_sim_ml_score_lambdamart_v6.yaml"
    cfg = load_config(path=cfg_path)

    assert cfg.selection.mode == "ml_score"
    assert cfg.selection.ml_score_model_id == "lambdamart_v6_20260518"
    assert cfg.selection.ml_score_prediction_table == "mart_p0b_lambdamart_v6_predictions"
