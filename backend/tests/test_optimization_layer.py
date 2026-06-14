"""寻参治理层测试 — DSR 数学正确 + 搜索空间闸真触发 + 网格寻参 best-params。

守: DSR (stdlib norm 替 scipy) 数值正确; plan_validator 空网格 raise (防白跑反例); 网格穷举 + dotted key。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.optimization import deflated_sharpe as DS  # noqa: E402
from services.optimization.formula_param_search import expand_grid, search_formula  # noqa: E402
from services.optimization.plan_validator import (  # noqa: E402
    PlanValidationError, enforce_search_space_nonempty, grid_size,
)


# ---- DSR stdlib 正态 (替 scipy) 数值正确 ----
def test_norm_cdf_known_values():
    assert DS._norm_cdf(0.0) == pytest.approx(0.5, abs=1e-9)
    assert DS._norm_cdf(1.959964) == pytest.approx(0.975, abs=1e-5)


def test_norm_ppf_known_values():
    assert DS._norm_ppf(0.5) == pytest.approx(0.0, abs=1e-6)
    assert DS._norm_ppf(0.975) == pytest.approx(1.959964, abs=1e-4)
    assert math.isnan(DS._norm_ppf(0.0)) and math.isnan(DS._norm_ppf(1.0))


def test_expected_max_sharpe_monotonic():
    assert DS.expected_max_sharpe(1) == 0.0          # <2 trials
    assert DS.expected_max_sharpe(100) > DS.expected_max_sharpe(10) > 0  # N 越大越高


def test_deflated_sharpe_deflates_with_more_trials():
    # 同 observed SR, trials 越多 -> p 越低 (多重比较去过拟合)
    p_few = DS.deflated_sharpe_ratio(1.0, n_trials=2, n_observations=100)
    p_many = DS.deflated_sharpe_ratio(1.0, n_trials=500, n_observations=100)
    assert 0.0 <= p_many < p_few <= 1.0, "trials 多 -> deflate 更狠"
    assert math.isnan(DS.deflated_sharpe_ratio(1.0, n_trials=1, n_observations=100))  # <2 trials


def test_deflated_sharpe_higher_obs_higher_p():
    lo = DS.deflated_sharpe_ratio(0.3, n_trials=20, n_observations=100)
    hi = DS.deflated_sharpe_ratio(2.0, n_trials=20, n_observations=100)
    assert hi > lo  # observed 越高越显著


# ---- plan_validator 闸 (防白跑) ----
def test_grid_size():
    assert grid_size({"a": [1, 2, 3], "b": [4, 5]}) == 6
    assert grid_size({}) == 0
    assert grid_size({"a": []}) == 0


def test_enforce_passes_real_formulas():
    res = enforce_search_space_nonempty(["macd_golden_cross", "reversal_short_term"])
    assert res["macd_golden_cross"] == 9 and res["reversal_short_term"] == 3


def test_enforce_raises_on_empty():
    with pytest.raises(PlanValidationError, match="无搜索空间"):
        enforce_search_space_nonempty(["macd_golden_cross"], spaces={"macd_golden_cross": {}})
    with pytest.raises(PlanValidationError):
        enforce_search_space_nonempty(["nonexist_formula"], spaces={})


# ---- 网格寻参 ----
def test_expand_grid_cartesian_and_dotted():
    combos = expand_grid({"a": [1, 2], "b.c": [3]})
    assert combos == [{"a": 1, "b": {"c": 3}}, {"a": 2, "b": {"c": 3}}]
    assert len(expand_grid({"x": [1, 2, 3], "y": [4, 5]})) == 6


def _synth(n_stocks=20, n_days=200, seed=4):
    rng = np.random.default_rng(seed)
    out = {}
    for s in range(n_stocks):
        close = np.maximum(100 + np.cumsum(rng.normal(0, 1, n_days)), 1.0)
        dates = []
        for d in range(n_days):
            mo = d // 17
            yy, mm = (2024, mo + 1) if mo < 12 else (2025, mo - 11)
            dates.append(f"{yy}{mm:02d}{(d % 28) + 1:02d}")
        out[f"s{s}"] = {"date": dates, "close": close.tolist(),
                        "high": (close + 0.5).tolist(), "low": (close - 0.5).tolist()}
    return out


def test_search_formula_returns_best_params_and_dsr():
    res = search_formula("reversal_short_term", _synth(), horizon=5, embargo=5)
    assert res["n_trials"] == 3                       # reversal 网格 3 组合
    assert res["best_params"] is not None
    assert "lookback" in res["best_params"]
    assert res["dsr_pvalue"] is None or 0.0 <= res["dsr_pvalue"] <= 1.0
