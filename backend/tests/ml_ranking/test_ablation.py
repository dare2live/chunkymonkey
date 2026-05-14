"""P1 ablation framework 单测.

Mock synthetic rows, verify run_ablation_suite produces baseline + drop_one + add_one
results with correct experiment_name + n_features.
"""
from __future__ import annotations

import random

from services.ml_ranking.ablation import (
    DEFAULT_GROUPS,
    FeatureGroup,
    run_ablation_suite,
)
from services.ml_ranking.lightgbm_walkforward import LightGBMWalkForwardConfig


def _synthetic_rows(n_months: int = 15, n_stocks: int = 30, seed: int = 42) -> list[dict]:
    """y = 0.5 * f1 + 0.3 * f2 + noise; f3 random; group_a / group_b / group_c."""
    random.seed(seed)
    rows = []
    for m in range(n_months):
        year = 2024 + m // 12
        month = (m % 12) + 1
        date = f"{year}-{month:02d}-15"
        for s in range(n_stocks):
            f1 = random.gauss(0, 1)
            f2 = random.gauss(0, 1)
            f3 = random.gauss(0, 1)  # noise
            rows.append({
                "stock_code": f"60{s:04d}",
                "signal_date": date,
                "f1": f1, "f2": f2, "f3": f3,
                "fwd_cost_after_10d": 0.5 * f1 + 0.3 * f2 + random.gauss(0, 0.5),
            })
    return rows


def test_ablation_runs_baseline_drop_add():
    rows = _synthetic_rows(n_months=15, n_stocks=30, seed=42)
    groups = (
        FeatureGroup(name="signal_group", columns=("f1", "f2")),
        FeatureGroup(name="noise_group", columns=("f3",)),
    )
    cfg = LightGBMWalkForwardConfig(
        n_estimators=30, min_train_months=3, forward_months=1,
    )
    suite = run_ablation_suite(rows, groups=groups, base_cfg=cfg)
    # baseline + 2 drop_one + 2 add_one
    assert suite.baseline.experiment_name == "baseline_all_groups"
    assert len(suite.drop_one) == 2
    assert len(suite.add_one) == 2

    # Baseline should have all 3 columns (f1, f2, f3)
    assert suite.baseline.n_features == 3

    # drop_signal_group → 仅 f3 → 1 feature
    drop_signal = [r for r in suite.drop_one if r.experiment_name == "drop_signal_group"][0]
    assert drop_signal.n_features == 1
    # drop_noise_group → 仅 f1+f2 → 2 features
    drop_noise = [r for r in suite.drop_one if r.experiment_name == "drop_noise_group"][0]
    assert drop_noise.n_features == 2

    # only_signal_group → f1+f2 → 2 features
    only_signal = [r for r in suite.add_one if r.experiment_name == "only_signal_group"][0]
    assert only_signal.n_features == 2


def test_ablation_summary_format():
    rows = _synthetic_rows(n_months=15, n_stocks=30)
    groups = (FeatureGroup(name="g1", columns=("f1", "f2")),)
    cfg = LightGBMWalkForwardConfig(n_estimators=30, min_train_months=3, forward_months=1)
    suite = run_ablation_suite(rows, groups=groups, base_cfg=cfg)
    summary = suite.summary()
    # baseline + only_g1 = 2 entries (drop_g1 → 0 features → skip)
    assert "baseline_all_groups" in summary
    assert summary["baseline_all_groups"]["delta_vs_baseline"] == 0.0
    for k, v in summary.items():
        assert "rank_ic" in v
        assert "ic_ir" in v
        assert "n_features" in v
        assert "delta_vs_baseline" in v


def test_ablation_signal_group_better_than_noise():
    """signal_group (f1+f2) ablation RankIC > only_noise (f3)."""
    rows = _synthetic_rows(n_months=15, n_stocks=30, seed=42)
    groups = (
        FeatureGroup(name="signal", columns=("f1", "f2")),
        FeatureGroup(name="noise", columns=("f3",)),
    )
    cfg = LightGBMWalkForwardConfig(n_estimators=30, min_train_months=3, forward_months=1)
    suite = run_ablation_suite(rows, groups=groups, base_cfg=cfg)
    only_signal = [r for r in suite.add_one if r.experiment_name == "only_signal"][0]
    only_noise = [r for r in suite.add_one if r.experiment_name == "only_noise"][0]
    # Signal 应明显 > noise
    assert only_signal.mean_rank_ic > only_noise.mean_rank_ic


def test_default_groups_has_alpha158_risk_financial_events():
    names = [g.name for g in DEFAULT_GROUPS]
    assert "alpha158" in names
    assert "risk_factors" in names
    assert "financial_pit" in names
    assert "events" in names
