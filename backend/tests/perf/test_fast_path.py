"""Tests for fast_path (phase 3 perf)."""
from __future__ import annotations

import numpy as np
import pytest

from services.perf.fast_path import (
    SimResult, ExitReason,
    compute_sharpe, compute_mean_ret, compute_ic_ir,
    compute_objectives_from_arrays,
)


def _make_sim_result(n=10):
    """Synthetic SimResult."""
    return SimResult(
        net_ret=np.array([0.02, -0.01, 0.03, 0.01, -0.02, 0.04, -0.01, 0.02, 0.0, 0.01], dtype=np.float32),
        gross_ret=np.array([0.025, -0.005, 0.035, 0.015, -0.015, 0.045, -0.005, 0.025, 0.005, 0.015], dtype=np.float32),
        max_drawdown=np.array([-0.01, -0.02, -0.005, -0.01, -0.03, -0.005, -0.02, -0.01, -0.005, -0.01], dtype=np.float32),
        holding_days=np.array([10, 5, 8, 12, 7, 15, 6, 9, 11, 8], dtype=np.int16),
        exit_reason=np.array([1, 2, 3, 1, 2, 3, 2, 1, 1, 3], dtype=np.int8),
        n_blocked=0,
    )


class TestSimResult:
    def test_length_invariant(self):
        with pytest.raises(ValueError, match="length mismatch"):
            SimResult(
                net_ret=np.array([1.0]),
                gross_ret=np.array([1.0, 2.0]),  # mismatch
                max_drawdown=np.array([1.0]),
                holding_days=np.array([1], dtype=np.int16),
                exit_reason=np.array([1], dtype=np.int8),
            )

    def test_n_valid_excludes_unable(self):
        r = SimResult(
            net_ret=np.zeros(5, dtype=np.float32),
            gross_ret=np.zeros(5, dtype=np.float32),
            max_drawdown=np.zeros(5, dtype=np.float32),
            holding_days=np.ones(5, dtype=np.int16),
            exit_reason=np.array([1, 5, 2, 5, 3], dtype=np.int8),  # 2 UNABLE
        )
        assert r.n_valid == 3


class TestObjectives:
    def test_compute_sharpe_positive(self):
        net_ret = np.array([0.02, 0.01, 0.03, 0.015, 0.025], dtype=np.float32)
        sharpe = compute_sharpe(net_ret, periods_per_year=12.0)
        assert sharpe > 0  # all positive returns

    def test_compute_sharpe_empty(self):
        assert compute_sharpe(np.array([], dtype=np.float32)) == 0.0

    def test_compute_sharpe_constant(self):
        # std=0 → sharpe=0
        assert compute_sharpe(np.array([0.01, 0.01, 0.01], dtype=np.float32)) == 0.0

    def test_compute_mean_ret(self):
        assert abs(compute_mean_ret(np.array([0.01, 0.02, 0.03], dtype=np.float32)) - 0.02) < 1e-6

    def test_compute_ic_ir(self):
        # Perfect correlation
        scores = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        returns = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
        ic, _ = compute_ic_ir(scores, returns)
        assert ic > 0.9  # near 1

    def test_compute_objectives_sharpe_minus_dd(self):
        r = _make_sim_result()
        obj = compute_objectives_from_arrays(r, objective="sharpe_minus_dd")
        # Some value (sharpe - 0.5 * |dd|)
        assert isinstance(obj, float)
